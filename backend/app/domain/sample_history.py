"""Six months of an importer's life, for the screens that only mean something with a history.

The reference dataset (`sample_data.py`) is two containers and one invoice: enough to tell the story
of one shipment, not enough to draw a curve. This one is twenty containers over the last half year —
three suppliers, two routes, eight recurring articles of which two are heavy or bulky enough for the
allocation basis to matter — with an ocean freight that climbs and comes back down, so the landed
unit cost of an article has something to say from one arrival to the next.

As in the reference dataset, the seed only places inputs and lets the product compute: terminal
handling, clearance, haulage and duty are estimated by the real `estimate_container` from rate cards
and the tariff rates on the lines; the freight quote is an estimate typed by hand, as a buyer would
from the forwarder's offer; invoices then replace those estimates one for one (`supersedes_cost_id`),
which is where the variance report gets its figures. Dates are relative to today, so a dataset loaded
next month still has a container at risk *now*.

Goods are bought in USD and in EUR and the freight is invoiced in USD. Rates are the ECB's, asked for
in one call over the whole period; with the provider unreachable the seed falls back to one indicative
rate, recorded as `manual` — it never labels a rate it made up as the ECB's.

Prices, weights and tariff rates are plausible, not authoritative: nobody should read a duty rate
here and declare goods with it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.extraction.regex_extractor import RegexExtractor
from app.core.errors import Unprocessable
from app.core.money import q2, to_base
from app.core.tiny_pdf import make_pdf
from app.domain.alerts.service import raise_dnd_risk_alert
from app.domain.costing.service import recompute_org
from app.domain.documents.ports import DocumentStore
from app.domain.estimates.service import estimate_container
from app.domain.fx.service import FxService
from app.domain.invoices.service import create_invoice, extract
from app.domain.models import (
    AllocationMethod,
    Container,
    ContainerLoad,
    ContainerMilestone,
    Cost,
    CostScope,
    CostStatus,
    CostType,
    Incoterm,
    Organization,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    RateBasis,
    RateCard,
    Shipment,
    Supplier,
    TrackingState,
)
from app.domain.sample_data import SampleData
from app.domain.sample_registry import register
from app.domain.tracking.state import derive_dnd

FREE_DAYS_DEMURRAGE = 7
DEMURRAGE_DAILY_RATE = Decimal("85.00")

#: 1 USD in EUR at the ECB reference rate of 17 September 2026 (1 EUR = 1.1481 USD). Used only when
#: the provider cannot be reached, and written on the cost as a manual rate.
INDICATIVE_USD_IN_EUR = Decimal("0.8710")


@dataclass(frozen=True)
class _Article:
    sku: str
    description: str
    hs_code: str
    supplier: str
    unit_price: Decimal  # in the supplier's currency
    weight_kg: Decimal
    volume_cbm: Decimal
    duty_rate: Decimal | None


#: name, country, currency the supplier quotes in
SUPPLIERS: dict[str, tuple[str, str, str]] = {
    "tyres": ("Zhejiang Kaiyuan Tyre Co.", "CN", "USD"),
    "house": ("Ningbo Hanwei Housewares", "CN", "EUR"),
    "parts": ("Shanghai Lianhe Auto Parts", "CN", "USD"),
}

#: sku, description, HS code, supplier, unit price, kg, m³, duty rate
# fmt: off
_ARTICLE_ROWS = (
    ("TYR-20555R16-91V", "Pneu 205/55 R16 91V", "40111000", "tyres", "15.20", "9", "0.050", "0.045"),
    ("TYR-22545R17-94W", "Pneu 225/45 R17 94W", "40111000", "tyres", "19.80", "10.5", "0.058", "0.045"),
    ("MAT-EVA-6040-GY", "Tapis de sol EVA 60x40 gris", "39269097", "house", "18.50", "1.5", "0.010", "0.065"),
    ("WIP-BLADE-600", "Balai d'essuie-glace 600 mm", "85124000", "house", "1.75", "0.25", "0.0012", "0.027"),
    # Heavy for its size: by weight it carries a container's freight, by volume almost none.
    ("BAT-12V-70AH", "Batterie 12 V 70 Ah", "85071020", "parts", "38.00", "17.5", "0.009", "0.037"),
    # And the opposite: a third of a cubic metre for fourteen kilos.
    ("BOX-ROOF-420L", "Coffre de toit 420 L", "87082990", "parts", "62.00", "14", "0.32", "0.045"),
    # Duty-free, which is a rate (0) and not a missing one.
    ("JACK-HYD-2T", "Cric hydraulique 2 t", "84254200", "parts", "16.40", "8.2", "0.012", "0"),
    ("CHAIN-SNOW-9MM", "Chaînes à neige 9 mm", "73158200", "parts", "11.20", "3.8", "0.006", "0.027"),
)
# fmt: on

ARTICLES: dict[str, _Article] = {
    sku: _Article(sku, label, hs, supplier, Decimal(price), Decimal(kg), Decimal(cbm), Decimal(duty))
    for sku, label, hs, supplier, price, kg, cbm, duty in _ARTICLE_ROWS
}

#: What the importer sells each article for, before VAT, in euros: the catalogue a real importer keeps,
#: and what turns a landed unit cost into a margin. Two articles have a sheet and no price, on purpose:
#: a report has to say what it cannot measure rather than leave it out.
SALE_PRICES_EUR: dict[str, Decimal] = {
    "TYR-20555R16-91V": Decimal("24.90"),
    "TYR-22545R17-94W": Decimal("32.90"),
    "MAT-EVA-6040-GY": Decimal("34.90"),
    "WIP-BLADE-600": Decimal("4.90"),
    "BAT-12V-70AH": Decimal("69.00"),
    "BOX-ROOF-420L": Decimal("119.00"),
}

#: What a container is filled with. Quantities are those of a 40'HC that closes by volume or by weight.
RECIPES: dict[str, tuple[tuple[str, int], ...]] = {
    "tyres": (("TYR-20555R16-91V", 800), ("TYR-22545R17-94W", 300)),
    "access": (("MAT-EVA-6040-GY", 2000), ("WIP-BLADE-600", 8000), ("JACK-HYD-2T", 900)),
    "heavy": (("BAT-12V-70AH", 900), ("BOX-ROOF-420L", 150)),
    "mixed": (("TYR-20555R16-91V", 600), ("BAT-12V-70AH", 400), ("CHAIN-SNOW-9MM", 500)),
}

#: carrier SCAC, container owner prefix, bill of lading prefix, origin, destination, forwarder
LINES: dict[str, tuple[str, str, str, str, str, str]] = {
    "msc": ("MSCU", "MSCU", "MEDUNB", "CNNGB", "FRLEH", "TRANSDEMO SAS"),
    "cma": ("CMDU", "CMAU", "CMDUNGB", "CNNGB", "FRLEH", "TRANSDEMO SAS"),
    "hapag": ("HLCU", "HLXU", "HLCUSHA", "CNSHA", "BEANR", "SCHELDE FORWARDING NV"),
}


@dataclass(frozen=True)
class _Voyage:
    arrived_days_ago: int  # negative: still at sea
    line: str
    recipe: str
    freight_usd: int  # the forwarder's quote for the box
    fate: str  # closed | paid:<days over> | avoided | overdue | at_risk | sailing


#: Oldest first. The freight quote climbs from spring to midsummer and eases off, which is what the
#: unit-cost curve of every article then shows. Antwerp is a little dearer than Le Havre.
VOYAGES: tuple[_Voyage, ...] = (
    _Voyage(172, "msc", "tyres", 2100, "closed"),
    _Voyage(165, "hapag", "access", 2300, "closed"),
    _Voyage(151, "cma", "heavy", 2300, "closed"),
    _Voyage(144, "msc", "mixed", 2350, "closed"),
    _Voyage(130, "msc", "tyres", 2800, "closed"),
    _Voyage(121, "hapag", "access", 3100, "closed"),
    _Voyage(109, "cma", "heavy", 3300, "closed"),
    _Voyage(100, "msc", "tyres", 3400, "closed"),
    _Voyage(88, "hapag", "mixed", 3500, "closed"),
    _Voyage(79, "msc", "access", 3150, "paid:2"),
    _Voyage(67, "cma", "tyres", 2950, "closed"),
    _Voyage(58, "hapag", "heavy", 2950, "closed"),
    _Voyage(46, "msc", "mixed", 2700, "avoided"),
    _Voyage(37, "cma", "tyres", 2650, "closed"),
    _Voyage(26, "hapag", "access", 2750, "closed"),
    _Voyage(17, "msc", "heavy", 2700, "paid:3"),
    _Voyage(9, "cma", "tyres", 2750, "overdue"),
    _Voyage(4, "msc", "mixed", 2800, "at_risk"),
    _Voyage(-8, "hapag", "access", 3050, "sailing"),
    _Voyage(-20, "msc", "tyres", 2950, "sailing"),
)

#: What the invoice added to (or took off) the freight quote, in USD: bunker and peak-season
#: surcharges mostly. Cycled over the invoiced voyages.
FREIGHT_SURPRISES = (0, 120, -60, 240, 0, 85, -40, 310, 0, 150)
#: What the forwarder really billed for a flat rate card, in euros, cycled the same way.
ACTUALS: dict[CostType, tuple[str, ...]] = {
    CostType.THC: ("268.00", "255.00", "262.00", "250.00"),
    CostType.CUSTOMS_BROKERAGE: ("120.00", "142.50", "120.00", "135.00"),
    CostType.DRAYAGE: ("396.00", "480.00", "450.00", "520.00", "465.00"),
}
#: The duty paid against the theoretical one: customs values the goods slightly differently.
DUTY_DRIFT = ("1.000", "1.012", "0.994", "1.021", "1.000", "1.007")

#: Three things that go wrong in a real year, placed so that the audit has something to find:
#: a terminal handling billed twice by two invoices (voyage 5), a haulier's own invoice for a second
#: delivery that puts the haulage of one box at three times the going rate of its route (voyage 13 —
#: nobody quoted it, so it is a question, not a claim), and a clearance the forwarder never invoiced
#: (voyage 9, whose estimate still stands). Everything else about those boxes is as clean as the
#: rest; the invoices above their quotes are the dataset's ordinary surcharges, as on the variance
#: screen.
DUPLICATE_THC_ON = 5
OUTLIER_DRAYAGE_ON, OUTLIER_DRAYAGE_EXTRA = 13, Decimal("1000.00")
NEVER_INVOICED_ON, NEVER_INVOICED_TYPE = 9, CostType.CUSTOMS_BROKERAGE


def container_number(prefix: str, serial: int) -> str:
    """An ISO 6346 number with its real check digit, so nothing downstream has reason to doubt it."""
    body = f"{prefix}{serial:06d}"
    values = []
    for char in body:
        if char.isdigit():
            values.append(int(char))
        else:
            # A = 10, and the multiples of 11 are skipped: B = 12 ... K = 21, L = 23 ... U = 32, V = 34
            value = ord(char) - ord("A") + 10
            values.append(value + (value - 1) // 10)
    check = sum(v * 2**i for i, v in enumerate(values)) % 11 % 10
    return f"{body}{check}"


class _Rates:
    """The rate of a day, the ECB's when it can be had. One range call up front, then the cache."""

    def __init__(self, db: Session, fx: FxService, org: Organization, start: date, end: date) -> None:
        self.db, self.fx, self.base = db, fx, org.base_currency
        for currency in {"USD", "EUR"} - {self.base}:
            fx.preload(db, self.base, currency, start, end)

    def on(self, currency: str, day: date) -> tuple[Decimal, date, str]:
        try:
            found = self.fx.resolve(self.db, self.base, currency, day)
        except Unprocessable:
            if (self.base, currency) != ("EUR", "USD"):
                raise
            return INDICATIVE_USD_IN_EUR, day, "manual"
        return found.rate, found.rate_date, found.source


def seed_history(db: Session, fx: FxService, store: DocumentStore, org: Organization) -> SampleData:
    """Create the six-month dataset for `org` and materialise its allocations. Caller commits."""
    now = datetime.now(UTC)
    today = now.date()
    rates = _Rates(db, fx, org, today - timedelta(days=260), today)

    suppliers = {
        key: Supplier(org_id=org.id, name=name, country=country, default_currency=currency)
        for key, (name, country, currency) in SUPPLIERS.items()
    }
    db.add_all(suppliers.values())
    db.flush()

    lines = _purchase_orders(db, org, rates, suppliers, today)
    _catalogue(db, org)

    db.add_all(
        [
            RateCard(
                org_id=org.id,
                cost_type=cost_type,
                scope=CostScope.CONTAINER,
                basis=RateBasis.FLAT,
                amount=Decimal(amount),
                currency="EUR",
                notes=notes,
            )
            for cost_type, amount, notes in (
                (CostType.THC, "250.00", "Manutention terminal, port de destination"),
                (CostType.CUSTOMS_BROKERAGE, "120.00", "Honoraires de déclaration en douane"),
                (CostType.DRAYAGE, "450.00", "Camionnage terminal → entrepôt"),
            )
        ]
    )
    db.flush()

    containers: list[Container] = []
    invoiced = 0
    for index, voyage in enumerate(VOYAGES):
        container = _container(db, org, voyage, index, now)
        containers.append(container)
        for sku, quantity in _filled(voyage, index):
            db.add(
                ContainerLoad(
                    org_id=org.id,
                    container_id=container.id,
                    po_line_id=lines[(index // 2, sku)].id,
                    quantity=Decimal(quantity),
                )
            )
        db.flush()

        booked_on = today - timedelta(days=voyage.arrived_days_ago + 35)
        _cost(
            db,
            org,
            rates,
            container,
            CostType.OCEAN_FREIGHT,
            Decimal(voyage.freight_usd),
            "USD",
            booked_on,
            status=CostStatus.ESTIMATE,
            notes="Cotation du transitaire à la réservation",
        )
        estimate_container(db, fx, org, container, on_date=booked_on)

        if voyage.fate in ("closed", "avoided") or voyage.fate.startswith("paid"):
            _invoice(db, org, rates, container, voyage, index, invoiced, today)
            invoiced += 1
        _demurrage(db, org, rates, container, voyage, index, now)

    # Only now, as in the reference dataset: `estimate_container` does not know that demurrage is a
    # penalty, and a card present earlier would have estimated one on every container. The freight
    # card is the price of a box today, for whoever presses "Estimer avec les barèmes" next.
    db.add_all(
        [
            RateCard(
                org_id=org.id,
                cost_type=CostType.OCEAN_FREIGHT,
                scope=CostScope.CONTAINER,
                basis=RateBasis.FLAT,
                amount=Decimal(VOYAGES[-1].freight_usd),
                currency="USD",
                notes="Asie → Europe du Nord, 40'HC, dernière cotation",
            ),
            RateCard(
                org_id=org.id,
                cost_type=CostType.DEMURRAGE,
                scope=CostScope.CONTAINER,
                basis=RateBasis.FLAT,
                amount=DEMURRAGE_DAILY_RATE,
                currency="EUR",
                notes="Tarif journalier, indicatif — pour le rapport « surestaries évitées »",
            ),
        ]
    )
    db.flush()
    recompute_org(db, org)

    _invoice_to_review(db, fx, store, org, containers, today)

    cost_count = len(list(db.scalars(select(Cost.id).where(Cost.org_id == org.id))))
    first = db.get(Shipment, containers[0].shipment_id)
    return SampleData(
        containers=containers,
        supplier_count=len(suppliers),
        purchase_order_count=len({po_id for po_id in (line.po_id for line in lines.values())}),
        cost_count=cost_count,
        shipment_reference=first.reference if first is not None else "",
    )


def _catalogue(db: Session, org: Organization) -> None:
    """One product sheet per article. A catalogue can be filled before any order exists, so a sheet
    already there is the user's and is left as it is; the others are registered as the demo's one by
    one, never swept up with everything else."""
    existing = set(db.scalars(select(Product.sku).where(Product.org_id == org.id)))
    sheets = [
        Product(
            org_id=org.id,
            sku=article.sku,
            description=article.description,
            hs_code=article.hs_code,
            duty_rate=article.duty_rate,
            unit_weight_kg=article.weight_kg,
            unit_volume_cbm=article.volume_cbm,
            sale_price=SALE_PRICES_EUR.get(article.sku),
            sale_currency="EUR" if article.sku in SALE_PRICES_EUR else None,
        )
        for article in ARTICLES.values()
        if article.sku not in existing
    ]
    db.add_all(sheets)
    db.flush()
    register(db, org.id, "product", [sheet.id for sheet in sheets])


def _filled(voyage: _Voyage, index: int) -> list[tuple[str, int]]:
    """The recipe, a little fuller or emptier from one voyage to the next: no two boxes are alike."""
    factor = (Decimal("1.00"), Decimal("0.90"), Decimal("1.05"), Decimal("0.95"))[index % 4]
    return [(sku, int(quantity * factor)) for sku, quantity in RECIPES[voyage.recipe]]


def _purchase_orders(
    db: Session, org: Organization, rates: _Rates, suppliers: dict[str, Supplier], today: date
) -> dict[tuple[int, str], PurchaseOrderLine]:
    """One order per supplier for every two voyages, so an order routinely spans two containers.

    Returns the order line that carries `sku` for the pair of voyages `index // 2`."""
    wanted: dict[tuple[int, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for index, voyage in enumerate(VOYAGES):
        for sku, quantity in _filled(voyage, index):
            wanted[(index // 2, ARTICLES[sku].supplier)][sku] += quantity

    lines: dict[tuple[int, str], PurchaseOrderLine] = {}
    last_pair = (len(VOYAGES) - 1) // 2
    for number, ((pair, supplier_key), articles) in enumerate(sorted(wanted.items()), start=101):
        currency = SUPPLIERS[supplier_key][2]
        ordered_on = today - timedelta(days=VOYAGES[pair * 2].arrived_days_ago + 70)
        rate, rate_date, _ = rates.on(currency, ordered_on)
        order = PurchaseOrder(
            org_id=org.id,
            po_number=f"PO-2026-{number}",
            supplier_id=suppliers[supplier_key].id,
            currency=currency,
            fx_rate=rate,
            fx_date=rate_date,
            incoterm=Incoterm.FOB,
            order_date=ordered_on,
        )
        for line_no, (sku, quantity) in enumerate(sorted(articles.items()), start=1):
            article = ARTICLES[sku]
            # Suppliers raise their prices a little over the half year: under one per cent a quarter.
            price = (article.unit_price * (Decimal(1) + Decimal("0.004") * pair)).quantize(Decimal("0.01"))
            line = PurchaseOrderLine(
                org_id=org.id,
                line_no=line_no,
                sku=sku,
                description=article.description,
                hs_code=article.hs_code,
                quantity=Decimal(quantity),
                unit_price=price,
                unit_weight_kg=article.weight_kg,
                unit_volume_cbm=article.volume_cbm,
                # The newest order of snow chains came without its tariff rate: the duty of that
                # container cannot be spread over it, and the screen has to say so.
                duty_rate=None if sku == "CHAIN-SNOW-9MM" and pair == last_pair - 1 else article.duty_rate,
            )
            order.lines.append(line)
            lines[(pair, sku)] = line
        db.add(order)
    db.flush()
    return lines


def _container(db: Session, org: Organization, voyage: _Voyage, index: int, now: datetime) -> Container:
    scac, prefix, bl_prefix, origin, destination, _ = LINES[voyage.line]
    arrived = now - timedelta(days=voyage.arrived_days_ago)
    shipment = Shipment(
        org_id=org.id,
        reference=f"{bl_prefix}{2604000 + index * 137}",
        carrier_scac=scac,
        incoterm=Incoterm.FOB,
        origin_unlocode=origin,
        destination_unlocode=destination,
        etd=arrived.date() - timedelta(days=37),
        eta=arrived.date(),
    )
    db.add(shipment)
    db.flush()

    container = Container(
        org_id=org.id,
        shipment_id=shipment.id,
        container_number=container_number(prefix, (482199 + index * 7919) % 1_000_000),
        iso_type="45G1",  # ISO 6346 for a 40' high cube
        carrier_scac=scac,
        tracking_state=TrackingState.MANUAL,
        eta=arrived,
        free_days_demurrage=FREE_DAYS_DEMURRAGE,
        milestone=ContainerMilestone.VESSEL_DEPARTED,
    )
    if voyage.fate != "sailing":
        container.ata = arrived
        container.discharged_at = arrived + timedelta(hours=9)
        container.milestone = ContainerMilestone.DISCHARGED
    db.add(container)
    db.flush()
    return container


def _cost(
    db: Session,
    org: Organization,
    rates: _Rates,
    container: Container,
    cost_type: CostType,
    amount: Decimal,
    currency: str,
    cost_date: date,
    *,
    status: CostStatus,
    vendor: str | None = None,
    invoice_number: str | None = None,
    supersedes: Cost | None = None,
    notes: str | None = None,
) -> Cost:
    rate, rate_date, source = rates.on(currency, cost_date)
    cost = Cost(
        org_id=org.id,
        scope=CostScope.CONTAINER,
        container_id=container.id,
        cost_type=cost_type,
        amount=q2(amount),
        currency=currency,
        fx_rate=rate,
        fx_date=rate_date,
        fx_source=source,
        amount_base=to_base(q2(amount), rate),
        allocation_method=(
            supersedes.allocation_method
            if supersedes is not None
            else AllocationMethod.BY_CIF_VALUE
            if cost_type is CostType.CUSTOMS_DUTY
            else org.default_allocation_method
        ),
        cost_date=cost_date,
        status=status,
        vendor=vendor,
        invoice_number=invoice_number,
        supersedes_cost_id=supersedes.id if supersedes is not None else None,
        notes=notes,
    )
    db.add(cost)
    db.flush()
    return cost


def _invoice(
    db: Session,
    org: Organization,
    rates: _Rates,
    container: Container,
    voyage: _Voyage,
    index: int,
    nth: int,
    today: date,
) -> None:
    """The forwarder's invoice, a week after arrival: every estimate of the box gets its real figure."""
    forwarder = LINES[voyage.line][5]
    number = f"FA-2026-{1000 + index * 7}"
    billed_on = min(today, today - timedelta(days=voyage.arrived_days_ago - 6))
    estimates = db.scalars(
        select(Cost).where(
            Cost.org_id == org.id,
            Cost.container_id == container.id,
            Cost.status == CostStatus.ESTIMATE,
        )
    )
    billed: dict[CostType, Decimal] = {}
    for estimate in list(estimates):
        if index == NEVER_INVOICED_ON and estimate.cost_type is NEVER_INVOICED_TYPE:
            continue  # the forwarder forgot this one: its estimate stands, and the audit says so
        if estimate.cost_type is CostType.OCEAN_FREIGHT:
            amount = estimate.amount + FREIGHT_SURPRISES[nth % len(FREIGHT_SURPRISES)]
        elif estimate.cost_type is CostType.CUSTOMS_DUTY:
            amount = estimate.amount * Decimal(DUTY_DRIFT[nth % len(DUTY_DRIFT)])
        else:
            grid = ACTUALS[estimate.cost_type]
            amount = Decimal(grid[nth % len(grid)])
        billed[estimate.cost_type] = amount
        _cost(
            db,
            org,
            rates,
            container,
            estimate.cost_type,
            amount,
            estimate.currency,
            billed_on,
            status=CostStatus.ACTUAL,
            vendor=forwarder,
            invoice_number=number,
            supersedes=estimate,
        )
    if index == DUPLICATE_THC_ON:
        # The forwarder re-billed the terminal's handling at cost, as a disbursement; then the
        # terminal's own invoice reached the importer too. Same box, same charge, same amount, two
        # providers and two invoice numbers: a fact, not a resemblance.
        _cost(
            db,
            org,
            rates,
            container,
            CostType.THC,
            billed[CostType.THC],
            "EUR",
            billed_on + timedelta(days=9),
            status=CostStatus.ACTUAL,
            vendor="TERMINAL PORTUAIRE",
            invoice_number=f"TH-2026-{400 + index}",
            notes="Manutention facturée directement par le terminal",
        )
    if index == OUTLIER_DRAYAGE_ON:
        # The truck found the warehouse closed and came back the next day; the haulier billed the
        # second run directly. Nobody quoted it, so it is not "above the quote": it is a haulage
        # three times the going rate of its route, and the audit can only ask.
        _cost(
            db,
            org,
            rates,
            container,
            CostType.DRAYAGE,
            OUTLIER_DRAYAGE_EXTRA,
            "EUR",
            billed_on + timedelta(days=12),
            status=CostStatus.ACTUAL,
            vendor="TRANSPORTS LEMAIRE",
            invoice_number=f"TR-2026-{500 + index}",
            notes="Seconde présentation — entrepôt fermé à l'arrivée du camion",
        )


def _demurrage(
    db: Session,
    org: Organization,
    rates: _Rates,
    container: Container,
    voyage: _Voyage,
    index: int,
    now: datetime,
) -> None:
    """Where the box went after discharge, which is the whole demurrage story of the dataset."""
    if voyage.fate in ("sailing", "overdue", "at_risk"):
        return  # at sea, or still on the terminal: the endpoint derives today's risk and alerts on it
    discharged = container.discharged_at
    assert discharged is not None
    over = int(voyage.fate.split(":")[1]) if voyage.fate.startswith("paid") else 0

    if voyage.fate == "avoided":
        # Told two days before the last free day, collected on it: what the alert is for. The alert
        # is raised as the product would have raised it that morning, then dated to that morning.
        warned_at = discharged + timedelta(days=FREE_DAYS_DEMURRAGE - 3)
        derive_dnd(container, org, warned_at)
        alert = raise_dnd_risk_alert(db, org, container, warned_at)
        if alert is not None:
            alert.created_at = warned_at
            alert.read_at = warned_at + timedelta(hours=2)
            alert.notified_at = warned_at
        collected = discharged + timedelta(days=FREE_DAYS_DEMURRAGE - 1)
    elif over:
        collected = discharged + timedelta(days=FREE_DAYS_DEMURRAGE - 1 + over)
    else:
        collected = discharged + timedelta(days=2 + index % 4)

    container.gate_out_at = collected
    container.empty_returned_at = collected + timedelta(days=3)
    container.milestone = ContainerMilestone.GATE_IN_EMPTY_RETURN
    db.flush()

    if over:
        _cost(
            db,
            org,
            rates,
            container,
            CostType.DEMURRAGE,
            DEMURRAGE_DAILY_RATE * over,
            "EUR",
            collected.date() + timedelta(days=4),
            status=CostStatus.ACTUAL,
            vendor="TERMINAL PORTUAIRE",
            invoice_number=f"SUR-2026-{300 + index}",
            notes=f"{over} jour(s) au-delà du dernier jour franc",
        )


def _invoice_to_review(
    db: Session,
    fx: FxService,
    store: DocumentStore,
    org: Organization,
    containers: list[Container],
    today: date,
) -> None:
    """One invoice waiting for its reader: a real PDF, read by the rules, never by a model — sample
    data has no business being sent to an outside provider."""
    index = next(i for i, voyage in enumerate(VOYAGES) if voyage.fate == "overdue")
    container, voyage = containers[index], VOYAGES[index]
    _, _, bl_prefix, origin, destination, forwarder = LINES[voyage.line]
    reference = f"{bl_prefix}{2604000 + index * 137}"

    def money(amount: Decimal) -> str:
        return f"{amount:,.2f}".replace(",", " ").replace(".", ",")

    def charge(label: str, amount: Decimal) -> str:
        return f"{label:<47}1          {money(amount):<10} {money(amount)}"

    charges = [
        (f"Fret maritime {origin} / {destination}", Decimal("2412.60")),
        ("THC destination Le Havre", Decimal("262.00")),
        ("Dedouanement import", Decimal("135.00")),
        ("Camionnage Le Havre - entrepot", Decimal("465.00")),
    ]
    subtotal = sum((amount for _, amount in charges), Decimal(0))
    vat = q2(subtotal * Decimal("0.20"))
    text = [
        forwarder,
        "12 quai de la Marine, 76600 Le Havre - TVA FR12345678901",
        f"FACTURE N° FA-2026-{1000 + index * 7}",
        f"Date : {(today - timedelta(days=2)).strftime('%d/%m/%Y')}",
        f"Client : {org.name}",
        f"Dossier : {reference}   B/L {reference}",
        f"Conteneur {container.container_number}  40HC",
        "",
        "Designation                                   Quantite   PU          Montant",
        *(charge(label, amount) for label, amount in charges),
        "",
        f"Total HT                                                 {money(subtotal)}",
        f"TVA 20%                                                  {money(vat)}",
        f"Total TTC                                                {money(subtotal + vat)} EUR",
    ]
    invoice = create_invoice(
        db, store, org, make_pdf(text), filename=f"facture-{forwarder.split()[0].lower()}.pdf"
    )
    extract(db, store, fx, org, invoice, [RegexExtractor()])
