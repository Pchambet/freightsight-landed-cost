"""The invoices the bench reads: the same charges, laid out the ways forwarders really lay them out.

These are not real documents — nobody's invoices belong in a repository — and the bench does not
claim they are. They are the *structures* met on French import invoices, each built as a real PDF
table: four columns with a quantity, a VAT-code column after the amount, taxable and non-taxable side
by side, a foreign currency converted on the line, a carrier's English statement, disbursements apart
from services, a credit note, several containers on one invoice, a carried-forward second page — and
each of them also drawn a column at a time, which is how some accounting packages write a table.

Real invoices, once a customer agrees to share some, go in `tests/fixtures/invoices_real/` (ignored by
git) as `name.pdf` + `name.json` and are scored with the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from bench_invoices_pdf import Cell, make_pdf

from app.domain.models import CostType

LEFT, RIGHT = 40.0, 555.0


@dataclass(frozen=True)
class Charge:
    label: str
    amount: Decimal
    #: None: a wording no rule can be expected to know. The line must still be read.
    cost_type: CostType | None
    quantity: int = 1
    taxable: bool = False
    #: Billed in another currency and converted on the line (amount is then in that currency).
    foreign: tuple[str, Decimal] | None = None  # (currency, rate to the invoice currency)

    @property
    def billed(self) -> Decimal:
        """What the line comes to in the invoice's currency."""
        if self.foreign is None:
            return self.amount
        return (self.amount * self.foreign[1]).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Invoice:
    vendor: str
    address: str
    number: str
    issued: date
    containers: tuple[str, ...]
    bl: str
    charges: tuple[Charge, ...]
    currency: str = "EUR"
    vat_rate: Decimal = Decimal("0.20")
    english: bool = False
    credit_note: bool = False

    @property
    def subtotal(self) -> Decimal:
        return sum((c.billed for c in self.charges), Decimal(0))

    @property
    def vat(self) -> Decimal:
        taxable = sum((c.billed for c in self.charges if c.taxable), Decimal(0))
        return (taxable * self.vat_rate).quantize(Decimal("0.01"))

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.vat


@dataclass(frozen=True)
class Expected:
    invoice_number: str
    invoice_date: date
    currency: str
    #: None when the document prints no pre-tax total: the reader must then not make one up.
    subtotal: Decimal | None
    total: Decimal
    #: (amount, cost type or None when any type — or none — is acceptable)
    lines: tuple[tuple[Decimal, CostType | None], ...]
    containers: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    name: str
    family: str
    pdf: bytes
    expected: Expected
    tags: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------------------------- the invoices

F, T, B, D, H, W, I, O = (  # noqa: E741 - the table below is read by column, short names keep it one
    CostType.OCEAN_FREIGHT,
    CostType.THC,
    CostType.CUSTOMS_BROKERAGE,
    CostType.DRAYAGE,
    CostType.CUSTOMS_DUTY,
    CostType.WAREHOUSING,
    CostType.INSPECTION,
    CostType.BL_FEE,
)


def _d(value: str) -> Decimal:
    return Decimal(value)


INVOICES: tuple[Invoice, ...] = (
    Invoice(
        "TRANSIT MARITIME ATLANTIQUE SAS",
        "12 quai de la Saône, 76600 Le Havre - SIRET 812 345 678 00021 - TVA FR12812345678",
        "FAC-2026-08871",
        date(2026, 9, 5),
        ("MSCU4821990",),
        "MEDUSH2604417",
        (
            Charge("Fret maritime Shanghai / Le Havre", _d("2450.00"), F),
            Charge("THC destination Le Havre", _d("285.00"), T),
            Charge("Frais de B/L", _d("65.00"), O, taxable=True),
            Charge("Dédouanement import", _d("120.00"), B, taxable=True),
            Charge("Camionnage Le Havre - entrepôt client", _d("430.00"), D, taxable=True),
        ),
    ),
    # Wordings no keyword list knows: the lines must be read all the same.
    Invoice(
        "SOGDEMO COMMISSIONNAIRE DE TRANSPORT",
        "Zone portuaire, 13270 Fos-sur-Mer - TVA FR40123456789",
        "F2609-00412",
        date(2026, 9, 12),
        ("CMAU6089031",),
        "CMDUNGB2605233",
        (
            Charge("Fret maritime Ningbo / Fos", _d("2710.00"), F),
            Charge("Frais de sûreté portuaire", _d("18.50"), None),
            Charge("Taxe d'escale et de péage", _d("42.00"), None),
            Charge("Redevance informatique Cargo Community", _d("12.00"), None, taxable=True),
            Charge("Pesage VGM", _d("35.00"), None, taxable=True),
            Charge("Manutention terminal", _d("262.00"), T),
            Charge("Post-acheminement Fos - Lyon", _d("680.00"), None, taxable=True),
        ),
    ),
    # Quantities above one: "2 285,00" must not become two thousand.
    Invoice(
        "SCHELDE FORWARDING NV",
        "Noorderlaan 147, 2030 Antwerpen - BTW BE0456789123",
        "INV-26-117734",
        date(2026, 8, 21),
        ("HLXU4377125", "HLXU4456316"),
        "HLCUSHA2605918",
        (
            Charge("Fret maritime Shanghai / Anvers", _d("5900.00"), F, quantity=2),
            Charge("THC Anvers", _d("570.00"), T, quantity=2),
            Charge("Surestaries 3 jours", _d("255.00"), CostType.DEMURRAGE, quantity=3),
            Charge("Honoraires d'agréé en douane", _d("95.00"), B, taxable=True),
            Charge("Camionnage Anvers - Lille", _d("1240.00"), D, quantity=2, taxable=True),
        ),
    ),
    # Freight in dollars, converted on the line; small and large amounts side by side.
    Invoice(
        "TRANSDEMO SAS",
        "12 quai de la Marine, 76600 Le Havre - TVA FR12345678901",
        "FA-2026-1104",
        date(2026, 9, 2),
        ("TGHU7245081",),
        "MEDUNB2606329",
        (
            Charge("Fret maritime CNNGB / FRLEH", _d("2950.00"), F, foreign=("USD", _d("0.8637"))),
            Charge("BAF", _d("310.00"), F, foreign=("USD", _d("0.8637"))),
            Charge("THC destination", _d("268.00"), T),
            Charge("Droits de douane", _d("1336.89"), H),
            Charge("Visite douanière scanner", _d("145.00"), I, taxable=True),
            Charge("Magasinage 4 jours", _d("96.00"), W, taxable=True),
        ),
    ),
)

CARRIER_STATEMENT = Invoice(
    "OCEANIC CONTAINER LINES (FRANCE) SA",
    "4 boulevard de Dunkerque, 13002 Marseille - VAT FR77552024875",
    "FRIM2600918842",
    date(2026, 7, 30),
    ("MSCU6168220",),
    "MEDUNB2606192",
    (
        Charge("OCEAN FREIGHT", _d("2700.00"), F),
        Charge("BUNKER ADJUSTMENT FACTOR", _d("412.00"), None),
        Charge("LOW SULPHUR SURCHARGE", _d("96.00"), None),
        Charge("TERMINAL HANDLING CHARGE DESTINATION", _d("245.00"), T),
        Charge("DOCUMENTATION FEE DESTINATION", _d("55.00"), None),
        Charge("CONTAINER CLEANING", _d("30.00"), None),
    ),
    currency="USD",
    vat_rate=Decimal(0),
    english=True,
)

CREDIT_NOTE = Invoice(
    "TRANSIT MARITIME ATLANTIQUE SAS",
    "12 quai de la Saône, 76600 Le Havre - TVA FR12812345678",
    "AV-2026-00231",
    date(2026, 9, 9),
    ("MSCU4821990",),
    "MEDUSH2604417",
    (
        Charge("Fret maritime Shanghai / Le Havre - trop facturé", _d("-240.00"), F),
        Charge("Camionnage - annulation attente", _d("-85.00"), D, taxable=True),
    ),
    credit_note=True,
)


# ---------------------------------------------------------------------------------- the layouts


def fr(amount: Decimal) -> str:
    whole, _, cents = f"{abs(amount):,.2f}".partition(".")
    return f"{whole.replace(',', ' ')},{cents}"


def en(amount: Decimal) -> str:
    return f"{abs(amount):,.2f}"


def _header(inv: Invoice) -> list[Cell]:
    title = (
        ("CREDIT NOTE" if inv.credit_note else "INVOICE")
        if inv.english
        else ("AVOIR" if inv.credit_note else "FACTURE")
    )
    number = "No." if inv.english else "N°"
    issued = inv.issued.strftime("%d/%m/%Y")
    cells = [
        Cell(LEFT, 800, inv.vendor, 13),
        Cell(LEFT, 784, inv.address, 8),
        Cell(330, 745, f"{title} {number} {inv.number}", 11),
        Cell(330, 730, f"{'Date' if inv.english else 'Date de facture'} : {issued}"),
        Cell(330, 716, "Due date : 30 days" if inv.english else "Échéance : 30 jours fin de mois"),
        Cell(LEFT, 745, "Client : RoulezBio SAS"),
        Cell(LEFT, 731, "8 rue des Entrepreneurs, 69007 Lyon"),
        Cell(LEFT, 700, f"Dossier import LEH-26-4417 - B/L {inv.bl}"),
        Cell(
            LEFT, 686, ("Container " if inv.english else "Conteneur ") + " / ".join(inv.containers) + "  40HC"
        ),
    ]
    return cells


def _footer(y: float, inv: Invoice) -> list[Cell]:
    if inv.english:
        return [
            Cell(
                LEFT,
                y,
                "Payment within 30 days. Bank: IBAN FR76 3000 4000 0500 0012 3456 789 - BIC BNPAFRPP",
                7,
            )
        ]
    return [
        Cell(
            LEFT, y, "Fret et THC exonérés de TVA (art. 262 II-14° CGI). Paiement à 30 jours par virement.", 7
        ),
        Cell(
            LEFT,
            y - 10,
            "IBAN FR76 3000 4000 0500 0012 3456 789 - Capital 150 000 EUR - RCS Le Havre 812 345 678",
            7,
        ),
    ]


def _totals(y: float, inv: Invoice, money=fr) -> list[Cell]:  # type: ignore[no-untyped-def]
    sign = "-" if inv.credit_note else ""
    if inv.english:
        return [
            Cell(430, y, f"TOTAL AMOUNT DUE {inv.currency}"),
            Cell(RIGHT, y, sign + money(inv.total), align="right"),
        ]
    return [
        Cell(430, y, "Total HT"),
        Cell(RIGHT, y, sign + money(inv.subtotal), align="right"),
        Cell(430, y - 14, "TVA 20 %"),
        Cell(RIGHT, y - 14, sign + money(inv.vat), align="right"),
        Cell(430, y - 28, f"Total TTC {inv.currency}"),
        Cell(RIGHT, y - 28, sign + money(inv.total), align="right"),
    ]


def _expected(inv: Invoice) -> Expected:
    return Expected(
        invoice_number=inv.number,
        invoice_date=inv.issued,
        currency=inv.currency,
        subtotal=None if inv.english else inv.subtotal,
        total=inv.total,
        lines=tuple((c.billed, c.cost_type) for c in inv.charges),
        containers=inv.containers,
    )


def four_columns(inv: Invoice) -> list[list[Cell]]:
    """Désignation | Qté | PU HT | Montant HT — the layout of the repository's first fixture."""
    cells = _header(inv)
    y = 650.0
    cells += [
        Cell(LEFT, y, "Désignation"),
        Cell(380, y, "Qté", align="right", column=1),
        Cell(465, y, "PU HT", align="right", column=2),
        Cell(RIGHT, y, "Montant HT", align="right", column=3),
    ]
    for charge in inv.charges:
        y -= 16
        unit = charge.billed / charge.quantity
        sign = "-" if charge.billed < 0 else ""
        cells += [
            Cell(LEFT, y, charge.label),
            Cell(380, y, str(charge.quantity), align="right", column=1),
            Cell(465, y, sign + fr(unit), align="right", column=2),
            Cell(RIGHT, y, sign + fr(charge.billed), align="right", column=3),
        ]
    return [cells + _totals(y - 30, inv) + _footer(60, inv)]


def vat_code_column(inv: Invoice) -> list[list[Cell]]:
    """Code | Libellé | Montant HT | TVA — what Sage and EBP print: a letter after the amount."""
    cells = _header(inv)
    y = 650.0
    cells += [
        Cell(LEFT, y, "Code"),
        Cell(90, y, "Libellé", column=1),
        Cell(500, y, "Montant HT", align="right", column=2),
        Cell(RIGHT, y, "TVA", align="right", column=3),
    ]
    for index, charge in enumerate(inv.charges, start=1):
        y -= 16
        sign = "-" if charge.billed < 0 else ""
        cells += [
            Cell(LEFT, y, f"P{index:03d}"),
            Cell(90, y, charge.label, column=1),
            Cell(500, y, sign + fr(charge.billed), align="right", column=2),
            Cell(RIGHT, y, "N" if charge.taxable else "E", align="right", column=3),
        ]
    y -= 24
    cells += [Cell(LEFT, y, "E = exonéré (art. 262 II CGI)   N = taux normal 20 %", 7)]
    return [cells + _totals(y - 24, inv) + _footer(60, inv)]


def two_amount_columns(inv: Invoice) -> list[list[Cell]]:
    """Libellé | Non taxable | Taxable — the amount sits in one column, 0,00 or nothing in the other."""
    cells = _header(inv)
    y = 650.0
    cells += [
        Cell(LEFT, y, "Prestation"),
        Cell(450, y, "Non taxable", align="right", column=1),
        Cell(RIGHT, y, "Taxable", align="right", column=2),
    ]
    for index, charge in enumerate(inv.charges):
        y -= 16
        sign = "-" if charge.billed < 0 else ""
        cells.append(Cell(LEFT, y, charge.label))
        x_amount, x_other, column = (RIGHT, 450.0, 2) if charge.taxable else (450.0, RIGHT, 1)
        cells.append(Cell(x_amount, y, sign + fr(charge.billed), align="right", column=column))
        if index % 2 == 0:  # some packages print the empty column as 0,00, some leave it blank
            cells.append(Cell(x_other, y, "0,00", align="right", column=3 - column))
    y -= 22
    exempt = sum((c.billed for c in inv.charges if not c.taxable), Decimal(0))
    taxed = sum((c.billed for c in inv.charges if c.taxable), Decimal(0))
    cells += [
        Cell(LEFT, y, "Sous-totaux par colonne"),
        Cell(450, y, fr(exempt), align="right", column=1),
        Cell(RIGHT, y, fr(taxed), align="right", column=2),
    ]
    return [cells + _totals(y - 30, inv) + _footer(60, inv)]


def converted_on_the_line(inv: Invoice) -> list[list[Cell]]:
    """Libellé | Devise | Montant devise | Taux | Montant EUR — the dollar freight of a euro invoice."""
    cells = _header(inv)
    y = 650.0
    cells += [
        Cell(LEFT, y, "Libellé"),
        Cell(300, y, "Devise", column=1),
        Cell(410, y, "Montant devise", align="right", column=2),
        Cell(470, y, "Taux", align="right", column=3),
        Cell(RIGHT, y, f"Montant {inv.currency}", align="right", column=4),
    ]
    for charge in inv.charges:
        y -= 16
        currency, rate = charge.foreign or (inv.currency, Decimal(1))
        cells += [
            Cell(LEFT, y, charge.label),
            Cell(300, y, currency, column=1),
            Cell(410, y, fr(charge.amount), align="right", column=2),
            Cell(470, y, f"{rate:.4f}".replace(".", ","), align="right", column=3),
            Cell(RIGHT, y, fr(charge.billed), align="right", column=4),
        ]
    y -= 20
    cells.append(Cell(LEFT, y, "Cours du jour : 1 USD = 0,8637 EUR (BCE)", 7))
    return [cells + _totals(y - 24, inv) + _footer(60, inv)]


def carrier_statement(inv: Invoice) -> list[list[Cell]]:
    """CHARGE | BASIS | RATE | CUR | AMOUNT, in English, amounts as 2,700.00."""
    cells = _header(inv)
    y = 650.0
    cells += [
        Cell(LEFT, y, "CHARGE DESCRIPTION"),
        Cell(330, y, "BASIS", column=1),
        Cell(430, y, "RATE", align="right", column=2),
        Cell(470, y, "CUR", column=3),
        Cell(RIGHT, y, "AMOUNT", align="right", column=4),
    ]
    for charge in inv.charges:
        y -= 16
        cells += [
            Cell(LEFT, y, charge.label),
            Cell(330, y, "PER CNTR", column=1),
            Cell(430, y, en(charge.billed), align="right", column=2),
            Cell(470, y, inv.currency, column=3),
            Cell(RIGHT, y, en(charge.billed), align="right", column=4),
        ]
    return [cells + _totals(y - 30, inv, en) + _footer(60, inv)]


def disbursements_apart(inv: Invoice) -> list[list[Cell]]:
    """Two sections with their own subtotals: what was paid on the customer's behalf, then services."""
    cells = _header(inv)
    y = 650.0
    paid_for = [c for c in inv.charges if c.cost_type in (H, F, T)]
    services = [c for c in inv.charges if c not in paid_for]
    for title, group in (("DÉBOURS (non soumis à TVA)", paid_for), ("PRESTATIONS", services)):
        cells.append(Cell(LEFT, y, title, 10))
        for charge in group:
            y -= 16
            cells += [
                Cell(LEFT + 10, y, charge.label),
                Cell(RIGHT, y, fr(charge.billed), align="right", column=1),
            ]
        y -= 16
        subtotal = sum((c.billed for c in group), Decimal(0))
        cells += [
            Cell(330, y, f"Sous-total {title.split()[0].lower()}"),
            Cell(RIGHT, y, fr(subtotal), align="right", column=1),
        ]
        y -= 26
    return [cells + _totals(y - 10, inv) + _footer(60, inv)]


def per_container(inv: Invoice) -> list[list[Cell]]:
    """Charges repeated under each container number, as a forwarder bills a two-box shipment."""
    cells = _header(inv)
    y = 660.0
    cells += [Cell(LEFT, y, "Désignation"), Cell(RIGHT, y, "Montant HT", align="right", column=1)]
    share = len(inv.containers)
    for number in inv.containers:
        y -= 20
        cells.append(Cell(LEFT, y, f"Conteneur {number}", 10))
        for charge in inv.charges:
            y -= 15
            cells += [
                Cell(LEFT + 10, y, charge.label),
                Cell(RIGHT, y, fr(charge.billed / share), align="right", column=1),
            ]
    return [cells + _totals(y - 30, inv) + _footer(60, inv)]


def two_pages(inv: Invoice) -> list[list[Cell]]:
    """The table runs over the page: « À reporter » at the foot of one, « Report » at the head of the next."""
    first, second = inv.charges[: len(inv.charges) // 2], inv.charges[len(inv.charges) // 2 :]
    carried = sum((c.billed for c in first), Decimal(0))
    page_one = _header(inv)
    y = 650.0
    page_one += [Cell(LEFT, y, "Désignation"), Cell(RIGHT, y, "Montant HT", align="right", column=1)]
    for charge in first:
        y -= 16
        page_one += [Cell(LEFT, y, charge.label), Cell(RIGHT, y, fr(charge.billed), align="right", column=1)]
    page_one += [Cell(400, y - 24, "À reporter"), Cell(RIGHT, y - 24, fr(carried), align="right", column=1)]
    page_one += [Cell(280, 40, "Page 1 / 2", 7)]

    page_two = [Cell(LEFT, 800, inv.vendor, 13), Cell(330, 800, f"FACTURE N° {inv.number} (suite)", 9)]
    y = 760.0
    page_two += [Cell(400, y, "Report"), Cell(RIGHT, y, fr(carried), align="right", column=1)]
    for charge in second:
        y -= 16
        page_two += [Cell(LEFT, y, charge.label), Cell(RIGHT, y, fr(charge.billed), align="right", column=1)]
    page_two += _totals(y - 30, inv) + _footer(60, inv) + [Cell(280, 40, "Page 2 / 2", 7)]
    return [page_one, page_two]


def _case(
    family: str,
    inv: Invoice,
    pages: list[list[Cell]],
    *,
    column_major: bool,
    expected: Expected | None = None,
) -> Case:
    order = "colonnes" if column_major else "lignes"
    return Case(
        name=f"{family} · {inv.number} · {order}",
        family=family,
        pdf=make_pdf(pages, column_major=column_major),
        expected=expected or _expected(inv),
        tags=("column_major",) if column_major else (),
    )


def _per_container_expected(inv: Invoice) -> Expected:
    share = len(inv.containers)
    lines = tuple((c.billed / share, c.cost_type) for _ in inv.containers for c in inv.charges)
    base = _expected(inv)
    return Expected(
        base.invoice_number,
        base.invoice_date,
        base.currency,
        base.subtotal,
        base.total,
        lines,
        base.containers,
    )


def cases() -> list[Case]:
    found: list[Case] = []
    for column_major in (False, True):
        for inv in INVOICES:
            found.append(_case("quatre colonnes", inv, four_columns(inv), column_major=column_major))
            found.append(
                _case("code TVA après le montant", inv, vat_code_column(inv), column_major=column_major)
            )
            found.append(
                _case("taxable / non taxable", inv, two_amount_columns(inv), column_major=column_major)
            )
            found.append(_case("débours à part", inv, disbursements_apart(inv), column_major=column_major))
            found.append(_case("deux pages", inv, two_pages(inv), column_major=column_major))
        dollars = INVOICES[3]
        found.append(
            _case(
                "devise convertie sur la ligne",
                dollars,
                converted_on_the_line(dollars),
                column_major=column_major,
            )
        )
        found.append(
            _case(
                "relevé armateur en anglais",
                CARRIER_STATEMENT,
                carrier_statement(CARRIER_STATEMENT),
                column_major=column_major,
            )
        )
        found.append(_case("avoir", CREDIT_NOTE, four_columns(CREDIT_NOTE), column_major=column_major))
        two_boxes = INVOICES[2]
        found.append(
            _case(
                "par conteneur",
                two_boxes,
                per_container(two_boxes),
                column_major=column_major,
                expected=_per_container_expected(two_boxes),
            )
        )
    return found
