"""Demo dataset: a tyre-and-accessories importer's reference case, with figures a forwarder or a CFO
would accept (a tyre importer's landed-cost story).

Everything is priced in the organization's base currency, so the seed never needs a live FX lookup.
Freight, THC, customs clearance and haulage are seeded as ESTIMATES, one rate card per cost type,
applied through the real `estimate_container` (the same code path a user gets from the "Estimer avec
les barèmes" button) so the numbers can never drift from what the product itself would compute. Customs
duty is estimated the same way, from a `duty_rate` carried on each PO line rather than a rate card,
because the rate depends on the tariff heading of what is in the box, not on a price list. Nothing here
is an ACTUAL cost: the demo invoice (`docs/demo/fixtures/facture-transdemo-demo.pdf`) is what brings the
actuals, superseding these estimates one for one and producing a real, explainable variance — see
`backend/tests/test_demo_story.py`, which pins every figure this docstring and the README quote.

The DEMURRAGE rate card (for the "surestaries évitées" report) is added only after both containers are
estimated: `estimate_container` does not know that demurrage is a penalty rather than a landed cost, so
a card present too early would silently add a phantom "estimated demurrage" line to both containers'
landed cost. Seeding it last keeps today's seed clean; it does not fix the underlying gap, which is why
a fresh call to "Estimer avec les barèmes" later in the demo would revive it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.costing.service import recompute_org
from app.domain.estimates.service import estimate_container
from app.domain.fx.service import FxService
from app.domain.models import (
    Container,
    ContainerLoad,
    ContainerMilestone,
    Cost,
    CostScope,
    CostType,
    Incoterm,
    Organization,
    PurchaseOrder,
    PurchaseOrderLine,
    RateBasis,
    RateCard,
    Shipment,
    Supplier,
    TrackingState,
)

FREE_DAYS_DEMURRAGE = 7

#: Rate cards, one FLAT amount per container — realistic 2026 forwarder pricing for a Ningbo → Le Havre
#: FCL move (~4 000 $ Shanghai-Rotterdam per 40', ≈ 3 700 € at a
#: ~0.925 USD/EUR rate assumed for the demo, not looked up live). THC, customs clearance and haulage
#: sit mid-band of the ranges assumed for this lane (200-300 € / 80-150 € / 300-600 €).
OCEAN_FREIGHT_RATE = Decimal("3700.00")
THC_RATE = Decimal("250.00")
CUSTOMS_BROKERAGE_RATE = Decimal("120.00")
DRAYAGE_RATE = Decimal("450.00")
#: €/day, in line with the landing calculator's own Le Havre middle tier — not a coincidence, both are
#: order-of-magnitude figures for the same trade lane.
DEMURRAGE_DAILY_RATE = Decimal("85.00")

#: Tyres: HS 4011.10, new pneumatic tyres for passenger cars — 4.5 % is the standard EU MFN rate.
TYRE_DUTY_RATE = Decimal("0.045")
#: EVA floor mats: not a continuous floor covering (heading 3918), but a cut-to-shape moulded article,
#: which EU customs practice classifies under 3926.90 ("other articles of plastics"), MFN 6.5 %. Marked
#: as an assumption because no binding tariff information was sought for a demo fixture — worth a real
#: ruling before quoting a design partner.
MAT_DUTY_RATE = Decimal("0.065")

#: The demo invoice (docs/demo/fixtures/facture-transdemo-demo.pdf): Transdemo's ACTUAL figures for
#: MSCU4821990, meant to be confirmed whole (README, "Try it"). Each amount replaces the matching
#: ESTIMATE above one for one (same cost type, same container) — see `estimate_container`'s
#: supersession — so confirming every line is safe and produces one real variance per cost type, not a
#: duplicate.
#: These are not round numbers on purpose: a real forwarder's invoice never lands on the estimate.
DEMO_INVOICE_NUMBER = "FA-2026-0912"
DEMO_INVOICE_VENDOR = "TRANSDEMO SAS"
DEMO_INVOICE_OCEAN_FREIGHT = Decimal("3915.40")
DEMO_INVOICE_THC = Decimal("268.00")
DEMO_INVOICE_CUSTOMS_BROKERAGE = Decimal("142.50")
DEMO_INVOICE_DRAYAGE = Decimal("396.00")
#: Droits de douane: the tyre and mat lines' own rates (4,5 % / 6,5 %) applied to each line's real CIF
#: value once the actual freight above is known — the same "second pass on CIF" the theoretical
#: estimate above uses, worked out by hand because a customs broker's invoice states a number, it does
#: not run FreightSight's allocator. backend/tests/test_demo_story.py checks the total this produces.
DEMO_INVOICE_CUSTOMS_DUTY = Decimal("1336.89")
DEMO_INVOICE_SUBTOTAL = (
    DEMO_INVOICE_OCEAN_FREIGHT
    + DEMO_INVOICE_THC
    + DEMO_INVOICE_CUSTOMS_BROKERAGE
    + DEMO_INVOICE_DRAYAGE
    + DEMO_INVOICE_CUSTOMS_DUTY
)
DEMO_INVOICE_VAT = (DEMO_INVOICE_SUBTOTAL * Decimal("0.20")).quantize(Decimal("0.01"))
DEMO_INVOICE_TOTAL = DEMO_INVOICE_SUBTOTAL + DEMO_INVOICE_VAT


def demo_invoice_lines(invoice_date: date) -> list[str]:
    """The text of the demo invoice, as a forwarder's PDF prints it — one source for the PDF fixture
    (docs/demo/fixtures/generate_invoice.py) and for the regression test that reads it back
    (backend/tests/test_demo_story.py), so the two can never disagree with each other.

    `invoice_date` is left to the caller: the PDF is a static file, so whoever regenerates it decides
    how recent it should look (how far out still looks plausible is the caller's call).
    """

    def money(amount: Decimal) -> str:
        return f"{amount:,.2f}".replace(",", " ").replace(".", ",")

    def charge(label: str, amount: Decimal) -> str:
        # Quantity is always 1 here, so PU and Montant are the same figure — a forwarder's invoice
        # prints both columns anyway, and the extractor reads whichever one ends the line.
        return f"{label:<47}1          {money(amount):<10} {money(amount)}"

    return [
        DEMO_INVOICE_VENDOR,
        "12 quai de la Marine, 76600 Le Havre - TVA FR12345678901",
        f"FACTURE N° {DEMO_INVOICE_NUMBER}",
        f"Date : {invoice_date.strftime('%d/%m/%Y')}",
        "Client : RoulezBio SAS",
        "Dossier : MEDUSH2604417   B/L MEDUSH2604417",
        "Conteneur MSCU4821990  40HC",
        "",
        "Designation                                   Quantite   PU          Montant",
        charge("Fret maritime CNNGB / FRLEH", DEMO_INVOICE_OCEAN_FREIGHT),
        charge("THC destination Le Havre", DEMO_INVOICE_THC),
        charge("Dedouanement import", DEMO_INVOICE_CUSTOMS_BROKERAGE),
        charge("Camionnage Le Havre - entrepot", DEMO_INVOICE_DRAYAGE),
        charge("Droits de douane import", DEMO_INVOICE_CUSTOMS_DUTY),
        "",
        f"Total HT                                                 {money(DEMO_INVOICE_SUBTOTAL)}",
        f"TVA 20%                                                  {money(DEMO_INVOICE_VAT)}",
        f"Total TTC                                                {money(DEMO_INVOICE_TOTAL)} EUR",
    ]


def _no_live_fx(base: str, quote: str, on_date: date) -> tuple[Decimal, date] | None:
    """Every cost in this seed is already in the org's base currency, so this is never actually called
    — `FxService.resolve` short-circuits on `base == quote` before reaching the fetcher."""
    raise AssertionError(f"sample data seeded a foreign-currency cost: {quote} vs {base} on {on_date}")


@dataclass(frozen=True)
class SampleData:
    """What the seed created, for the API response and for the caller to finish (D&D derivation)."""

    containers: list[Container]
    supplier_count: int
    purchase_order_count: int
    cost_count: int
    shipment_reference: str


def has_data(db: Session, org_id: UUID) -> bool:
    """True as soon as the organization holds a purchase order or a container."""
    po = db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.org_id == org_id).limit(1))
    container = db.scalar(select(Container.id).where(Container.org_id == org_id).limit(1))
    return po is not None or container is not None


def seed_sample_data(db: Session, org: Organization) -> SampleData:
    """Create the demo dataset for `org` and materialise its allocations. Caller commits."""
    now = datetime.now(UTC)
    today = now.date()
    currency = org.base_currency

    tyres = Supplier(org_id=org.id, name="Zhejiang Kaiyuan Tyre Co.", country="CN", default_currency=currency)
    mats = Supplier(org_id=org.id, name="Ningbo Hanwei Housewares", country="CN", default_currency=currency)
    db.add_all([tyres, mats])
    db.flush()

    po_a = PurchaseOrder(
        org_id=org.id,
        po_number="PO-2026-014",
        supplier_id=tyres.id,
        currency=currency,
        fx_rate=Decimal("1"),
        incoterm=Incoterm.FOB,
        order_date=today - timedelta(days=75),
    )
    line_a = PurchaseOrderLine(
        org_id=org.id,
        line_no=1,
        sku="TYR-20555R16-91V",
        description="Pneu tourisme 205/55 R16 91V",
        hs_code="40111000",
        quantity=Decimal("1950"),
        unit_price=Decimal("14.00"),
        unit_weight_kg=Decimal("9"),
        unit_volume_cbm=Decimal("0.050"),
        duty_rate=TYRE_DUTY_RATE,
    )
    po_a.lines.append(line_a)

    po_b = PurchaseOrder(
        org_id=org.id,
        po_number="PO-2026-015",
        supplier_id=mats.id,
        currency=currency,
        fx_rate=Decimal("1"),
        incoterm=Incoterm.FOB,
        order_date=today - timedelta(days=68),
    )
    line_b = PurchaseOrderLine(
        org_id=org.id,
        line_no=1,
        sku="MAT-EVA-6040-GY",
        description="Tapis de sol EVA 60×40 gris",  # noqa: RUF001 - dimensions written with x, as a French catalogue would
        hs_code="39269097",
        quantity=Decimal("500"),
        unit_price=Decimal("20.00"),
        unit_weight_kg=Decimal("1.5"),
        unit_volume_cbm=Decimal("0.010"),
        duty_rate=MAT_DUTY_RATE,
    )
    po_b.lines.append(line_b)
    db.add_all([po_a, po_b])

    shipment = Shipment(
        org_id=org.id,
        reference="MEDUSH2604417",
        carrier_scac="MSCU",
        incoterm=Incoterm.FOB,
        origin_unlocode="CNNGB",
        destination_unlocode="FRLEH",
        etd=today - timedelta(days=42),
        eta=today - timedelta(days=5),
    )
    db.add(shipment)
    db.flush()

    # CNT1 is discharged and still on the terminal: the last free day is a few days out. CNT2 left the
    # terminal, so it carries a last free day but no demurrage risk.
    cnt1 = Container(
        org_id=org.id,
        shipment_id=shipment.id,
        container_number="MSCU4821990",
        iso_type="45G1",  # ISO 6346 for a 40' high cube
        carrier_scac="MSCU",
        milestone=ContainerMilestone.DISCHARGED,
        tracking_state=TrackingState.MANUAL,
        eta=now - timedelta(days=5),
        ata=now - timedelta(days=5),
        discharged_at=now - timedelta(days=4),
        free_days_demurrage=FREE_DAYS_DEMURRAGE,
    )
    cnt2 = Container(
        org_id=org.id,
        shipment_id=shipment.id,
        container_number="TGHU7245081",
        iso_type="45G1",  # ISO 6346 for a 40' high cube
        carrier_scac="MSCU",
        milestone=ContainerMilestone.GATE_OUT_FULL,
        tracking_state=TrackingState.MANUAL,
        eta=now - timedelta(days=5),
        ata=now - timedelta(days=5),
        discharged_at=now - timedelta(days=5),
        gate_out_at=now - timedelta(days=2),
        free_days_demurrage=FREE_DAYS_DEMURRAGE,
    )
    db.add_all([cnt1, cnt2])
    db.flush()

    # PO-A is split 750 / 1200 over the two containers (both a realistic fill for a 40'HC of loose
    # tyres — see the volume comment below), PO-B travels whole in CNT1.
    db.add_all(
        [
            ContainerLoad(org_id=org.id, container_id=cnt1.id, po_line_id=line_a.id, quantity=Decimal("750")),
            ContainerLoad(org_id=org.id, container_id=cnt1.id, po_line_id=line_b.id, quantity=Decimal("500")),
            ContainerLoad(
                org_id=org.id, container_id=cnt2.id, po_line_id=line_a.id, quantity=Decimal("1200")
            ),
        ]
    )
    db.flush()

    # Freight, THC, customs clearance and haulage: one rate card per cost type, applied through the
    # real estimate_container() below so every euro here is a number the product itself would produce
    # from these cards — never a hand-typed one that could drift from them.
    db.add_all(
        [
            RateCard(
                org_id=org.id,
                cost_type=CostType.OCEAN_FREIGHT,
                scope=CostScope.CONTAINER,
                basis=RateBasis.FLAT,
                amount=OCEAN_FREIGHT_RATE,
                currency=currency,
                notes="Ningbo-Le Havre, ~4 000 $/40'",
            ),
            RateCard(
                org_id=org.id,
                cost_type=CostType.THC,
                scope=CostScope.CONTAINER,
                basis=RateBasis.FLAT,
                amount=THC_RATE,
                currency=currency,
                notes="Manutention terminal, Le Havre",
            ),
            RateCard(
                org_id=org.id,
                cost_type=CostType.CUSTOMS_BROKERAGE,
                scope=CostScope.CONTAINER,
                basis=RateBasis.FLAT,
                amount=CUSTOMS_BROKERAGE_RATE,
                currency=currency,
                notes="Honoraires du transitaire pour la déclaration en douane",
            ),
            RateCard(
                org_id=org.id,
                cost_type=CostType.DRAYAGE,
                scope=CostScope.CONTAINER,
                basis=RateBasis.FLAT,
                amount=DRAYAGE_RATE,
                currency=currency,
                notes="Camionnage terminal → entrepôt",
            ),
        ]
    )
    db.flush()

    # No live FX lookup ever happens here: every cost above and every PO line is already in the org's
    # base currency, so FxService.resolve() takes the base == quote branch before it would call this.
    fx = FxService(_no_live_fx)
    for container in (cnt1, cnt2):
        estimate_container(db, fx, org, container, on_date=today)

    # Demurrage is not a landed cost, so its rate card is seeded only now — see the module docstring
    # for why adding it before estimate_container() would have injected a phantom estimate.
    db.add(
        RateCard(
            org_id=org.id,
            cost_type=CostType.DEMURRAGE,
            scope=CostScope.CONTAINER,
            basis=RateBasis.FLAT,
            amount=DEMURRAGE_DAILY_RATE,
            currency=currency,
            notes="Tarif journalier, indicatif — pour le rapport « surestaries évitées »",
        )
    )
    db.flush()
    recompute_org(db, org)

    total_costs = len(list(db.scalars(select(Cost.id).where(Cost.org_id == org.id))))
    return SampleData(
        containers=[cnt1, cnt2],
        supplier_count=2,
        purchase_order_count=2,
        cost_count=total_costs,
        shipment_reference=shipment.reference,
    )
