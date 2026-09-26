"""The audit of a closed period: what the goods really cost, and what is worth a claim.

This is the document the paid offer is made of — "your last quarter, audited" — and the quarterly
report a CFO puts in front of the owner. One rule governs it: **a finding is never an accusation and
never a promised saving.** Every line is a measured fact, with the objects behind it and the rule
that produced it; the total is called "amounts to check", not "savings". Each finding carries,
separately, how much money it is about (`amount`) and how sure the rule is (`confidence`): the same
provider billing a charge twice, an invoice above the estimate it replaced in the same currency, an
estimate nobody invoiced are facts; a charge merely far above the median of its route, or two
providers billing neighbouring amounts, is a question, and says so.

Everything here comes from one computation of the engine (`Computation`), the same the screens use,
and the figures of the sections it shares with other reports are those reports' figures for the same
dates — tested against them. The thresholds travel with the result (`Rules`): the document states its
own method from them, and a copy sent by link keeps the rules it was computed under.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Unprocessable
from app.core.money import q2, q4, to_base
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.entry import DUTY_GUARD, normalize_invoice_number, normalize_vendor, same_invoice
from app.domain.costing.service import Computation
from app.domain.models import Container, Cost, CostStatus, Invoice, InvoiceLine, InvoiceStatus, Organization
from app.domain.reporting.service import (
    UNKNOWN,
    AvoidedRule,
    Bucket,
    _route,
    arrival_date,
    dnd_report,
    landed_cost_report,
)
from app.domain.reporting.skus import SkuSummary, sku_report

ZERO = Decimal("0.00")
TENTH = Decimal("0.1")
Confidence = Literal["sure", "to_check"]

#: A charge of the same type, on the same container, from another invoice, this close in amount is
#: the same charge billed twice — and not "two handlings": those come on one invoice as one line. It
#: is a fact when one provider billed it twice or the two amounts are identical; two providers a
#: little apart is a question (a terminal's handling re-billed by the forwarder is the case to raise,
#: but two different services can have neighbouring flat fees).
DUPLICATE_TOLERANCE = Decimal("0.02")
#: Every payment carries its own bank fees, and "other" is anything: two of them are not a duplicate.
NEVER_DUPLICATE = frozenset({"BANK_FEES", "OTHER"})
#: An invoice is above its estimate from five per cent and fifty euros: below that it is a surcharge
#: or a rounding, and a claim for it would cost more than it brings. Measured in the currency both
#: were written in, so that the exchange rate moving between booking and invoice is never presented
#: as a price somebody charged.
ABOVE_QUOTE_MIN_SHARE = Decimal("0.05")
ABOVE_QUOTE_MIN_AMOUNT = Decimal("50.00")
#: A charge is out of the ordinary from one and a half times the median of the same charge on the
#: same route for the same size of box, and at least a hundred euros above it — measured on at least
#: four other containers of the last twelve months, since a median of three is not a norm.
OUTLIER_RATIO = Decimal("1.5")
OUTLIER_MIN_EXCESS = Decimal("100.00")
OUTLIER_MIN_BASIS = 4
OUTLIER_LOOKBACK = timedelta(days=365)
#: Freight follows a market that can triple in a quarter, so a year's median measures the market and
#: not the provider: freight is compared only with the boxes that landed within this many days of it.
FREIGHT = frozenset({"OCEAN_FREIGHT", "AIR_FREIGHT"})
FREIGHT_WINDOW = timedelta(days=45)
#: Never compared from one box to the next: duty, VAT and insurance follow the value of the goods;
#: demurrage, detention and storage the days a box stayed (they have their own section); bank fees and
#: "other" are anything.
NOT_COMPARED = frozenset(
    {"CUSTOMS_DUTY", "IMPORT_VAT", "INSURANCE", "DEMURRAGE", "DETENTION", "WAREHOUSING", "BANK_FEES", "OTHER"}
)
#: The length of a box, from the first character of its ISO 6346 size-type code (22G1, 45G1, L5G1),
#: or of the usual shorthand (20GP, 40HC). A 40' among 20' would stand out on everything.
SIZES = {"2": "20", "4": "40", "L": "45"}
#: A forwarder invoices within the month. An estimate still standing two months after the goods
#: landed is an invoice nobody chased, or a cost that was never billed and inflates the landed cost.
ESTIMATE_STALE_AFTER = timedelta(days=60)
#: The dates an audit can be about. Outside them a date is a typing mistake — and year 1 cannot even
#: have twelve months looked back from it.
EARLIEST, LATEST = date(2000, 1, 1), date(2100, 12, 31)

#: The findings a claim can be made about. The others are about the quality of the figures.
RECOVERABLE = frozenset({"DUPLICATE_CHARGE", "ABOVE_QUOTE", "OUTLIER_CHARGE"})
#: The params that name nobody. A copy sent with the providers' names left out keeps these and only
#: these, so that a rule which one day puts a name in its params is left out by default, not leaked.
PUBLIC_PARAMS = frozenset(
    {
        "amount",
        "currency",
        "days",
        "first_amount",
        "fx_effect",
        "invoiced",
        "landed_on",
        "lines",
        "median",
        "po_numbers",
        "quoted",
        "ratio",
        "reason",
        "route",
        "same_vendor",
        "share_pct",
        "size",
        "skus",
    }
)


@dataclass(frozen=True)
class Rules:
    """The thresholds an audit was produced with, amounts in the organization's base currency."""

    duplicate_tolerance_pct: Decimal
    never_duplicate: tuple[str, ...]
    above_quote_min_pct: Decimal
    above_quote_min_amount: Decimal
    outlier_ratio: Decimal
    outlier_min_excess: Decimal
    outlier_min_basis: int
    outlier_lookback_days: int
    freight_window_days: int
    not_compared: tuple[str, ...]
    estimate_stale_after_days: int
    #: How the figures were made, stated only by documents computed that way (a link sent before
    #: these rules existed must not claim them): customs duty spread by each line's rate when the
    #: rates explain what was paid within this per cent; demurrage and detention counted with the box
    #: that arrived in the period; no box of unknown route or length compared; one invoice entered
    #: twice never called a charge billed twice.
    duty_explained_pct: Decimal | None = None
    demurrage_by_arrival: bool | None = None
    outlier_needs_route_and_size: bool | None = None
    same_invoice_never_duplicate: bool | None = None


RULES = Rules(
    duplicate_tolerance_pct=DUPLICATE_TOLERANCE * 100,
    never_duplicate=tuple(sorted(NEVER_DUPLICATE)),
    above_quote_min_pct=ABOVE_QUOTE_MIN_SHARE * 100,
    above_quote_min_amount=ABOVE_QUOTE_MIN_AMOUNT,
    outlier_ratio=OUTLIER_RATIO,
    outlier_min_excess=OUTLIER_MIN_EXCESS,
    outlier_min_basis=OUTLIER_MIN_BASIS,
    outlier_lookback_days=OUTLIER_LOOKBACK.days,
    freight_window_days=FREIGHT_WINDOW.days,
    not_compared=tuple(sorted(NOT_COMPARED)),
    estimate_stale_after_days=ESTIMATE_STALE_AFTER.days,
    duty_explained_pct=DUTY_GUARD * 100,
    demurrage_by_arrival=True,
    outlier_needs_route_and_size=True,
    same_invoice_never_duplicate=True,
)


@dataclass
class Finding:
    code: str
    confidence: Confidence
    amount: Decimal
    container_id: UUID | None
    container_number: str | None
    cost_type: str | None
    vendor: str | None
    invoice_number: str | None
    cost_ids: list[UUID]
    #: How many containers the rule compared with (statistical rules only).
    basis: int | None = None
    #: The moving parts of the rule, as raw strings, for the sentence the screen writes.
    params: dict[str, str] = field(default_factory=dict)
    #: A claim can be made about it, and it counts in the first page's recoverable sums; otherwise it
    #: is about the quality of the figures.
    recoverable: bool = field(init=False)

    def __post_init__(self) -> None:
        self.recoverable = self.code in RECOVERABLE


@dataclass
class Margin:
    sku: str
    description: str | None
    quantity: Decimal
    fob: Decimal
    landed: Decimal
    unit_fob: Decimal
    unit_landed: Decimal
    #: Landed minus purchase over the period's volume: what a price computed on the purchase price
    #: leaves out. Present with or without a selling price.
    approach_costs: Decimal
    #: Landed minus purchase times the coefficient the company says it applies, over the same volume:
    #: what its own calculation leaves out. None when it gave no coefficient; negative when that
    #: coefficient is more prudent than the facts. Needs no selling price.
    gap_vs_assumed: Decimal | None = None
    sale_price: Decimal | None = None
    #: With a selling price in the base currency: the margin read on the purchase price, the real one,
    #: and the points between them.
    margin_on_fob_pct: Decimal | None = None
    margin_real_pct: Decimal | None = None
    points_lost: Decimal | None = None


@dataclass
class DemurrageLine:
    container_id: UUID
    container_number: str
    paid: Decimal
    avoided: Decimal | None
    #: Why `avoided` is what it is, with its moving parts (see reporting.service.DndLine).
    rule_code: AvoidedRule
    days: int | None
    daily_rate: Decimal | None
    #: Days the box stayed on the terminal past its last free day, when both dates are known.
    days_over: int | None
    quantity: Decimal
    #: What the demurrage added to every unit that was in the box.
    per_unit: Decimal | None


@dataclass
class CoefficientBucket:
    key: str
    label: str
    fob: Decimal
    landed: Decimal
    coefficient: Decimal | None


@dataclass
class Completeness:
    containers: int
    fob: Decimal
    landed: Decimal
    #: Part of the landed cost that still rests on estimates rather than invoices.
    estimated: Decimal
    estimated_share_pct: Decimal | None
    #: Invoices waiting for a person on the day of the audit, in the whole organization: until they
    #: are read, any of them may still change the period's figures.
    invoices_to_review: int
    containers_without_cost: int
    #: Where the exchange rates of the period's costs came from: ecb, ecb_latest, manual, same.
    fx_sources: dict[str, int]
    #: The period's boxes "far above the route" could not compare: no route, or no length.
    containers_not_compared: int | None = None
    #: Boxes of the organization, loaded or charged, that fall in no period at all: no arrival date.
    #: Whatever they cost is in no audit until someone dates them.
    containers_without_date: int | None = None


@dataclass
class Headline:
    #: Sum of `approach_costs` over the articles with a selling price: the margin a calculation on the
    #: purchase price announces and the goods do not make.
    margin_overstated: Decimal
    priced_skus: int
    unpriced_skus: int
    #: Sum of `gap_vs_assumed` over every article of the period; None without a coefficient.
    gap_vs_assumed: Decimal | None
    #: What the recoverable findings add up to, the sure ones and the doubtful ones apart.
    recoverable_sure: Decimal
    recoverable_to_check: Decimal
    demurrage_paid: Decimal


@dataclass
class Audit:
    base_currency: str
    period_from: date
    period_to: date
    #: The day it was computed: estimates are judged open *today*, invoices to review are today's.
    as_of: date
    headline: Headline
    margins: list[Margin]
    findings: list[Finding]
    demurrage: list[DemurrageLine]
    coefficient_by_month: list[CoefficientBucket]
    coefficient_by_supplier: list[CoefficientBucket]
    coefficient: Decimal | None
    assumed_coefficient: Decimal | None
    completeness: Completeness
    rules: Rules


def check_period(period_from: date, period_to: date) -> None:
    """Refuse a period that ends before it starts, or lies outside any year a business could mean."""
    if period_to < period_from:
        raise Unprocessable("period_to comes before period_from", code="PERIOD_INVALID")
    if period_from < EARLIEST or period_to > LATEST:
        raise Unprocessable(
            f"an audit covers dates between {EARLIEST.isoformat()} and {LATEST.isoformat()}",
            code="PERIOD_INVALID",
        )


def audit_report(
    db: Session,
    comp: Computation,
    org: Organization,
    period_from: date,
    period_to: date,
    *,
    today: date | None = None,
) -> Audit:
    check_period(period_from, period_to)
    as_of = today or datetime.now(UTC).date()
    every = {row.container_id: row.container for row in comp.load_rows.values()}
    boxes = _period_containers(comp, period_from, period_to)
    costs_in = _costs_of(comp, boxes)
    quantities = _quantities(comp, boxes)
    assumed = _assumed(org)

    margins = _margins(db, comp, org, period_from, period_to, assumed)
    # One euro, one finding: what the rules on invoices have already found is taken out of the charges
    # before the statistical rule looks at them, or a duplicate would come back as "far above the
    # route" and an invoice above its estimate would be claimed twice.
    claims = (
        _duplicates(comp, every, boxes, costs_in)
        + _double_entries(comp, every, costs_in, documents_of(db, costs_in))
        + _above_quote(comp, every, costs_in, org.base_currency)
    )
    findings = (
        claims
        + _outliers(comp, every, boxes, period_to, _claimed(claims))
        + _stale_estimates(comp, every, boxes, as_of)
        + _unallocated(comp, every, costs_in)
        + _duty_without_rate(comp, every, costs_in)
    )
    # A cost whose type was inferred — from its label, or from what a person said for a whole file —
    # may be of another type than it says: what a rule finds on it is a question, never a fact.
    inferred = {cost.id for cost in comp.cost_rows.values() if cost.type_inferred}
    for finding in findings:
        if finding.code in (*RECOVERABLE, "DOUBLE_ENTRY") and inferred & set(finding.cost_ids):
            finding.confidence = "to_check"
    findings.sort(key=lambda f: (f.confidence != "sure", -f.amount, f.code))
    demurrage = _demurrage(db, org, period_from, period_to, quantities)
    monthly = landed_cost_report(comp, org, group_by="month", period_from=period_from, period_to=period_to)
    by_supplier = landed_cost_report(
        comp, org, group_by="supplier", period_from=period_from, period_to=period_to
    )

    priced = [m for m in margins if m.sale_price is not None]
    return Audit(
        base_currency=org.base_currency,
        period_from=period_from,
        period_to=period_to,
        as_of=as_of,
        headline=Headline(
            margin_overstated=q2(sum((m.approach_costs for m in priced), ZERO)),
            priced_skus=len(priced),
            unpriced_skus=len(margins) - len(priced),
            gap_vs_assumed=(
                q2(sum((m.gap_vs_assumed or ZERO for m in margins), ZERO)) if assumed is not None else None
            ),
            recoverable_sure=_recoverable(findings, "sure"),
            recoverable_to_check=_recoverable(findings, "to_check"),
            demurrage_paid=q2(sum((line.paid for line in demurrage), ZERO)),
        ),
        margins=margins,
        findings=findings,
        demurrage=demurrage,
        coefficient_by_month=[_coefficient(b) for b in monthly.buckets],
        coefficient_by_supplier=[_coefficient(b) for b in by_supplier.buckets],
        coefficient=q4(monthly.totals.landed / monthly.totals.fob) if monthly.totals.fob else None,
        assumed_coefficient=assumed,
        completeness=_completeness(db, comp, org, boxes, costs_in, monthly.totals.fob, monthly.totals.landed),
        rules=RULES,
    )


def _recoverable(findings: list[Finding], confidence: Confidence) -> Decimal:
    return q2(sum((f.amount for f in findings if f.recoverable and f.confidence == confidence), ZERO))


# ------------------------------------------------------------------------------- what is in the period


def _period_containers(comp: Computation, period_from: date, period_to: date) -> dict[UUID, Container]:
    found: dict[UUID, Container] = {}
    for load in comp.loads:
        container = comp.load_rows[load.id].container
        landed_on = arrival_date(container)
        if landed_on is not None and period_from <= landed_on <= period_to:
            found[container.id] = container
    return found


def _costs_of(comp: Computation, boxes: dict[UUID, Container]) -> dict[UUID, Cost]:
    """The live costs of the period: those whose allocation lands on a container that arrived in it,
    plus those attached to such a container that the engine could not place."""
    found: dict[UUID, Cost] = {}
    for allocation in comp.result.allocations:
        if comp.load_rows[allocation.load_id].container_id in boxes:
            found[allocation.cost_id] = comp.cost_rows[allocation.cost_id]
    for cost in comp.cost_rows.values():
        if cost.container_id in boxes:
            found[cost.id] = cost
    return found


def _quantities(comp: Computation, boxes: dict[UUID, Container]) -> dict[UUID, Decimal]:
    per_box: dict[UUID, Decimal] = defaultdict(Decimal)
    for load in comp.loads:
        if load.container_id in boxes:
            per_box[load.container_id] += load.quantity
    return per_box


def _number(comp: Computation, every: dict[UUID, Container], cost: Cost) -> tuple[UUID | None, str | None]:
    """The container a cost is about: its own, else the one its allocation lands on when there is one."""
    if cost.container_id is not None:
        box = every.get(cost.container_id)
        return cost.container_id, box.container_number if box is not None else None
    landed_on = {
        comp.load_rows[a.load_id].container_id for a in comp.result.allocations if a.cost_id == cost.id
    }
    if len(landed_on) == 1:
        (container_id,) = landed_on
        return container_id, every[container_id].container_number
    return None, None


def _lines_of(comp: Computation, load_ids: Iterable[UUID]) -> tuple[str, str]:
    """The articles and the orders behind some loads, by the names a person knows them by: a shared
    document has nothing to click on."""
    lines = [comp.load_rows[load_id].po_line for load_id in load_ids if load_id in comp.load_rows]
    skus = sorted({line.sku for line in lines if line.sku})
    orders = sorted({comp.pos[line.po_id].po_number for line in lines if line.po_id in comp.pos})
    return ", ".join(skus), ", ".join(orders)


def _finding(
    comp: Computation,
    every: dict[UUID, Container],
    code: str,
    confidence: Confidence,
    amount: Decimal,
    cost: Cost,
    **params: str,
) -> Finding:
    container_id, number = _number(comp, every, cost)
    return Finding(
        code=code,
        confidence=confidence,
        amount=q2(amount),
        container_id=container_id,
        container_number=number,
        cost_type=cost.cost_type.value,
        vendor=cost.vendor,
        invoice_number=cost.invoice_number,
        cost_ids=[cost.id],
        params=params,
    )


# ------------------------------------------------------------------------------- the rules


def _duplicates(
    comp: Computation, every: dict[UUID, Container], boxes: dict[UUID, Container], costs_in: dict[UUID, Cost]
) -> list[Finding]:
    """The same charge on the same container from two invoices, this close in amount."""
    groups: dict[tuple[UUID, str], list[Cost]] = defaultdict(list)
    for cost in costs_in.values():
        kind = cost.cost_type.value
        if (
            cost.status is CostStatus.ACTUAL
            and cost.container_id in boxes
            and kind not in EXCLUDED_FROM_LANDED
            and kind not in NEVER_DUPLICATE
        ):
            groups[(cost.container_id, kind)].append(cost)
    found: list[Finding] = []
    for (container_id, _), costs in groups.items():
        costs.sort(key=lambda c: (c.cost_date, c.created_at))
        # A charge billed three times is two duplicates, not three pairs: once a cost has been found
        # to repeat an earlier one, it is not found again.
        repeated: set[UUID] = set()
        for i, first in enumerate(costs):
            for second in costs[i + 1 :]:
                if second.id in repeated:
                    continue
                if same_invoice(first.vendor, first.invoice_number, second.vendor, second.invoice_number):
                    continue  # one invoice entered twice is ours to fix, not a charge billed twice
                if not _close(first, second):
                    continue
                same_vendor = bool(normalize_vendor(first.vendor)) and normalize_vendor(
                    first.vendor
                ) == normalize_vendor(second.vendor)
                identical = (first.amount, first.currency) == (second.amount, second.currency)
                # Without a number on both, nothing says these are two invoices rather than one typed
                # twice: a question, whatever the amounts.
                numbered = bool(first.invoice_number) and bool(second.invoice_number)
                finding = _finding(
                    comp,
                    every,
                    "DUPLICATE_CHARGE",
                    "sure" if numbered and (same_vendor or identical) else "to_check",
                    min(first.amount_base, second.amount_base),
                    second,
                    first_invoice=first.invoice_number or "",
                    first_vendor=first.vendor or "",
                    first_amount=f"{first.amount_base:.2f}",
                    same_vendor="true" if same_vendor else "false",
                )
                finding.container_number = boxes[container_id].container_number
                finding.cost_ids = [first.id, second.id]
                found.append(finding)
                repeated.add(second.id)
    return found


def _close(a: Cost, b: Cost) -> bool:
    """Within the tolerance — in the currency both were billed in when they share one: two invoices in
    dollars a month apart differ in euros by the exchange rate alone."""
    x, y = (a.amount, b.amount) if a.currency == b.currency else (a.amount_base, b.amount_base)
    larger = max(x, y)
    return larger > 0 and abs(x - y) <= larger * DUPLICATE_TOLERANCE


def documents_of(db: Session, cost_ids: Iterable[UUID]) -> dict[UUID, UUID]:
    """The document each cost was written from: the confirmed invoice, or the costs file. One
    confirmation writes one cost per type, target and currency — and so does one ledger for the lines
    of one invoice: the freight in dollars and its security surcharge in euros, a surcharge on the bill
    of lading next to the box's freight, are two costs of one document — never a double entry."""
    ids = list(cost_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(InvoiceLine.cost_id, InvoiceLine.invoice_id).where(InvoiceLine.cost_id.in_(ids)).distinct()
    )
    found = {cost_id: invoice_id for cost_id, invoice_id in rows.tuples() if cost_id is not None}
    imported = db.execute(
        select(Cost.id, Cost.import_job_id).where(Cost.id.in_(ids), Cost.import_job_id.is_not(None))
    )
    for cost_id, job_id in imported.tuples():
        if job_id is not None:
            found.setdefault(cost_id, job_id)
    return found


def _target(cost: Cost) -> UUID | None:
    return cost.container_id or cost.shipment_id or cost.po_id or cost.po_line_id


def _double_entries(
    comp: Computation,
    every: dict[UUID, Container],
    costs_in: dict[UUID, Cost],
    written_by: dict[UUID, UUID],
) -> list[Finding]:
    """The same invoice recorded twice — a statement keyed in and its PDF confirmed, two spellings of
    one forwarder, once on a box and once on its bill of lading. Not the forwarder's doing and nothing
    to claim: a double entry of ours, which the landed cost counts twice until one of the two goes.

    `written_by` names the invoice a cost was confirmed from: two costs of one document are two of its
    lines, whatever their number says."""
    lands_on: dict[UUID, set[UUID]] = defaultdict(set)
    for allocation in comp.result.allocations:
        lands_on[allocation.cost_id].add(comp.load_rows[allocation.load_id].container_id)
    groups: dict[tuple[str, str], list[Cost]] = defaultdict(list)
    for cost in costs_in.values():
        number = normalize_invoice_number(cost.invoice_number)
        if cost.status is CostStatus.ACTUAL and number and cost.cost_type.value not in EXCLUDED_FROM_LANDED:
            groups[(number, cost.cost_type.value)].append(cost)
    found: list[Finding] = []
    for costs in groups.values():
        costs.sort(key=lambda c: (c.cost_date, c.created_at))
        repeated: set[UUID] = set()
        for i, first in enumerate(costs):
            for second in costs[i + 1 :]:
                if second.id in repeated:
                    continue
                document = written_by.get(first.id)
                if document is not None and document == written_by.get(second.id):
                    continue  # two lines of one invoice, written by its one confirmation
                if not same_invoice(first.vendor, first.invoice_number, second.vendor, second.invoice_number):
                    continue
                boxes_first = lands_on[first.id] | ({first.container_id} if first.container_id else set())
                boxes_second = lands_on[second.id] | ({second.container_id} if second.container_id else set())
                if not boxes_first & boxes_second:
                    continue  # one number on two freights: two invoices that happen to share it
                # A forwarder's invoice numbers are its own, one per document: with its name on both
                # copies, one line of it — type, currency, target — recorded twice is a fact. Without the
                # name, on a box and on its bill of lading, or in two currencies, the two may be two
                # lines of one invoice or a number every forwarder uses: a question.
                named = bool(normalize_vendor(first.vendor)) and bool(normalize_vendor(second.vendor))
                one_line = first.currency == second.currency and _target(first) == _target(second)
                finding = _finding(
                    comp,
                    every,
                    "DOUBLE_ENTRY",
                    "sure" if named and one_line else "to_check",
                    min(first.amount_base, second.amount_base),
                    second,
                    first_invoice=first.invoice_number or "",
                    first_vendor=first.vendor or "",
                    first_amount=f"{first.amount_base:.2f}",
                )
                finding.cost_ids = [first.id, second.id]
                found.append(finding)
                repeated.add(second.id)
    return found


def _above_quote(
    comp: Computation, every: dict[UUID, Container], costs_in: dict[UUID, Cost], base_currency: str
) -> list[Finding]:
    """An invoice above the estimate it replaced — a forwarder's quote typed at booking or the
    company's own rate card: either way, what was expected.

    Compared in the currency both were written in. Between the booking and the invoice the exchange
    rate moves, and that part of the difference in euros is nobody's price: it is given apart
    (`fx_effect`), and the finding is the price difference at the invoice's own rate. An estimate and
    an invoice in two different currencies cannot be taken apart that way: they are compared in the
    base currency, and the finding is a question.
    """
    found: list[Finding] = []
    for actual, estimate in comp.superseded:
        if actual.id not in costs_in or actual.cost_type.value in EXCLUDED_FROM_LANDED:
            continue
        in_base = actual.amount_base - estimate.amount_base
        confidence: Confidence
        if actual.currency == estimate.currency:
            if estimate.amount <= 0:
                continue
            share = (actual.amount - estimate.amount) / estimate.amount
            excess = to_base(actual.amount - estimate.amount, actual.fx_rate)
            quoted, invoiced, currency, confidence = estimate.amount, actual.amount, actual.currency, "sure"
        else:
            if estimate.amount_base <= 0:
                continue
            share = in_base / estimate.amount_base
            excess = in_base
            quoted, invoiced, currency = estimate.amount_base, actual.amount_base, base_currency
            confidence = "to_check"
        if excess < ABOVE_QUOTE_MIN_AMOUNT or share < ABOVE_QUOTE_MIN_SHARE:
            continue
        params = {
            "quoted": f"{quoted:.2f}",
            "invoiced": f"{invoiced:.2f}",
            "currency": currency,
            "share_pct": f"{(share * 100).quantize(TENTH)}",
        }
        if in_base != excess:  # what the exchange rate added (or took off) on top of the price
            params["fx_effect"] = f"{in_base - excess:.2f}"
        found.append(_finding(comp, every, "ABOVE_QUOTE", confidence, excess, actual, **params))
    return found


def _claimed(findings: list[Finding]) -> dict[tuple[UUID, str], Decimal]:
    """What the findings on invoices already account for, per container and type of charge."""
    claimed: dict[tuple[UUID, str], Decimal] = defaultdict(Decimal)
    for finding in findings:
        if finding.container_id is not None and finding.cost_type is not None:
            claimed[(finding.container_id, finding.cost_type)] += finding.amount
    return claimed


def _size(container: Container) -> str:
    return SIZES.get((container.iso_type or "").strip().upper()[:1], "")


def _outliers(
    comp: Computation,
    every: dict[UUID, Container],
    boxes: dict[UUID, Container],
    period_to: date,
    claimed: dict[tuple[UUID, str], Decimal],
) -> list[Finding]:
    """A charge far above the same charge on the same route, for the same size of box.

    Compared per container (the charges of one type on one box are added up, less what a finding on
    invoices already claims), with the boxes of the last twelve months — of the 45 days around it for
    freight — and only when there are enough of them to call their middle a norm. What comes out is a
    question — the box may have been heavier, the route congested — and the finding says so.
    """
    since = period_to - OUTLIER_LOOKBACK
    per_box_type: dict[tuple[UUID, str], Decimal] = defaultdict(Decimal)
    group_of: dict[UUID, tuple[str, str]] = {}
    landed_of: dict[UUID, date] = {}
    number_of: dict[UUID, str] = {}
    for cost in comp.cost_rows.values():
        if cost.status is not CostStatus.ACTUAL or cost.container_id is None:
            continue
        kind = cost.cost_type.value
        if kind in EXCLUDED_FROM_LANDED or kind in NOT_COMPARED:
            continue
        container = every.get(cost.container_id)
        if container is None:
            continue
        landed_on = arrival_date(container)
        if landed_on is None or not (since <= landed_on <= period_to):
            continue
        route, size = _route(container)[0], _size(container)
        if route == UNKNOWN or not size:
            # A box whose route or length nobody told us is compared with nothing: the middle of
            # "unknown" is not a norm, and a 40' measured against 20' boxes is always "far above".
            continue
        per_box_type[(cost.container_id, kind)] += cost.amount_base
        group_of[cost.container_id] = (route, size)
        landed_of[cost.container_id] = landed_on
        number_of[cost.container_id] = container.container_number
    for key in per_box_type:
        per_box_type[key] = max(per_box_type[key] - claimed.get(key, ZERO), ZERO)

    found: list[Finding] = []
    for (container_id, cost_type), amount in per_box_type.items():
        if container_id not in boxes:
            continue
        window = FREIGHT_WINDOW if cost_type in FREIGHT else None
        peers = [
            other
            for (other_id, other_type), other in per_box_type.items()
            if other_type == cost_type
            and other_id != container_id
            and group_of[other_id] == group_of[container_id]
            and (window is None or abs(landed_of[other_id] - landed_of[container_id]) <= window)
        ]
        if len(peers) < OUTLIER_MIN_BASIS:
            continue
        norm = Decimal(median(peers))
        if norm <= 0:
            continue  # the middle of charges billed at nothing is no norm, and nothing is "times" it
        excess = amount - norm
        if amount <= norm * OUTLIER_RATIO or excess < OUTLIER_MIN_EXCESS:
            continue
        # The invoices behind the sum, the largest first: that is the one the question is about.
        costs = sorted(
            (
                c
                for c in comp.cost_rows.values()
                if c.container_id == container_id
                and c.cost_type.value == cost_type
                and c.status is CostStatus.ACTUAL
            ),
            key=lambda c: -c.amount_base,
        )
        route, size = group_of[container_id]
        params = {
            "amount": f"{amount:.2f}",
            "median": f"{q2(norm):.2f}",
            "ratio": f"{(amount / norm).quantize(TENTH)}",
            "route": route,
        }
        if size:
            params["size"] = size
        found.append(
            Finding(
                code="OUTLIER_CHARGE",
                confidence="to_check",
                amount=q2(excess),
                container_id=container_id,
                container_number=number_of[container_id],
                cost_type=cost_type,
                vendor=costs[0].vendor if costs else None,
                invoice_number=costs[0].invoice_number if costs else None,
                cost_ids=[c.id for c in costs],
                basis=len(peers),
                params=params,
            )
        )
    return found


def _stale_estimates(
    comp: Computation, every: dict[UUID, Container], boxes: dict[UUID, Container], as_of: date
) -> list[Finding]:
    """An estimate still standing, two months after its goods landed. Judged on the day of the audit
    and not on the period's end: the estimate is open today, and that is what makes it a finding."""
    found: list[Finding] = []
    for cost in comp.cost_rows.values():
        if cost.status is not CostStatus.ESTIMATE or cost.container_id not in boxes:
            continue
        landed_on = arrival_date(boxes[cost.container_id])
        if landed_on is None or as_of - landed_on < ESTIMATE_STALE_AFTER:
            continue
        found.append(
            _finding(
                comp,
                every,
                "ESTIMATE_NEVER_INVOICED",
                "sure",
                cost.amount_base,
                cost,
                days=str((as_of - landed_on).days),
                landed_on=landed_on.isoformat(),
            )
        )
    return found


def _unallocated(
    comp: Computation, every: dict[UUID, Container], costs_in: dict[UUID, Cost]
) -> list[Finding]:
    found: list[Finding] = []
    for missing in comp.result.unallocated:
        cost = costs_in.get(missing.cost_id)
        if cost is None:
            continue
        params = {"reason": missing.code}
        if missing.load_ids:  # the lines a weight, a volume or a value is missing on
            params["skus"], params["po_numbers"] = _lines_of(comp, missing.load_ids)
        found.append(_finding(comp, every, "UNALLOCATED_COST", "sure", cost.amount_base, cost, **params))
    return found


def _duty_without_rate(
    comp: Computation, every: dict[UUID, Container], costs_in: dict[UUID, Cost]
) -> list[Finding]:
    found: list[Finding] = []
    for note in comp.result.notes:
        cost = costs_in.get(note.cost_id)
        if cost is None or note.code != "DUTY_RATE_PARTIAL":
            continue
        skus, orders = _lines_of(comp, note.load_ids)
        found.append(
            _finding(
                comp,
                every,
                "DUTY_RATE_MISSING",
                "sure",
                cost.amount_base,
                cost,
                lines=str(len(note.load_ids)),
                skus=skus,
                po_numbers=orders,
            )
        )
    return found


# ------------------------------------------------------------------------------- the other sections


def _margins(
    db: Session,
    comp: Computation,
    org: Organization,
    period_from: date,
    period_to: date,
    assumed: Decimal | None,
) -> list[Margin]:
    """Each article over the period: what its approach costs are, what the company's own coefficient
    leaves out of them when it gave one, and — with a selling price — its margin as read on the
    purchase price and as it is."""
    articles: list[SkuSummary] = sku_report(db, comp, org, period_from=period_from, period_to=period_to)
    found: list[Margin] = []
    for article in articles:
        margin = Margin(
            sku=article.sku,
            description=article.description,
            quantity=article.quantity,
            fob=article.fob,
            landed=article.landed,
            unit_fob=article.unit_fob,
            unit_landed=article.unit_landed,
            approach_costs=q2(article.landed - article.fob),
            gap_vs_assumed=q2(article.landed - article.fob * assumed) if assumed is not None else None,
        )
        price = article.sale_price
        # A price in another currency than the books gives no margin here either (see sku_report).
        if price is not None and article.margin_unit is not None and price > 0:
            margin.sale_price = price
            margin.margin_on_fob_pct = ((price - article.unit_fob) / price * 100).quantize(TENTH)
            margin.margin_real_pct = ((price - article.unit_landed) / price * 100).quantize(TENTH)
            margin.points_lost = margin.margin_on_fob_pct - margin.margin_real_pct
        found.append(margin)
    found.sort(key=lambda m: -m.approach_costs)
    return found


def _demurrage(
    db: Session, org: Organization, period_from: date, period_to: date, quantities: dict[UUID, Decimal]
) -> list[DemurrageLine]:
    # By the arrival of the box, whatever the date of the charge: detention is invoiced after the empty
    # comes back, often in the next quarter, and it belongs to the goods that arrived in this one.
    report = dnd_report(
        db,
        org,
        period_from=period_from,
        period_to=period_to,
        daily_rate=_daily_rate(db, org),
        basis="arrival",
    )
    lines: list[DemurrageLine] = []
    for line in report.lines:
        quantity = quantities.get(line.container_id, Decimal(0))
        lines.append(
            DemurrageLine(
                container_id=line.container_id,
                container_number=line.container_number,
                paid=line.paid,
                avoided=line.avoided,
                rule_code=line.rule_code,
                days=line.days,
                daily_rate=line.daily_rate,
                days_over=line.days_over,
                quantity=quantity,
                per_unit=q4(line.paid / quantity) if quantity and line.paid else None,
            )
        )
    return lines


def _daily_rate(db: Session, org: Organization) -> Decimal | None:
    from app.domain.models import CostType, RateBasis, RateCard

    card = db.scalar(
        select(RateCard).where(
            RateCard.org_id == org.id,
            RateCard.cost_type == CostType.DEMURRAGE,
            RateCard.basis == RateBasis.FLAT,
        )
    )
    return card.amount if card is not None else None


def _coefficient(bucket: Bucket) -> CoefficientBucket:
    return CoefficientBucket(
        key=bucket.key,
        label=bucket.label,
        fob=bucket.fob,
        landed=bucket.landed,
        coefficient=q4(bucket.landed / bucket.fob) if bucket.fob else None,
    )


def _assumed(org: Organization) -> Decimal | None:
    """The coefficient the company says it applies — if what is stored is one. The settings route
    refuses anything else at the door; this does not rely on it."""
    raw = (org.settings or {}).get("assumed_coefficient")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        ratio = Decimal(str(raw))
    except ArithmeticError:
        return None
    return q4(ratio) if ratio.is_finite() and Decimal(1) <= ratio <= Decimal(3) else None


def _completeness(
    db: Session,
    comp: Computation,
    org: Organization,
    boxes: dict[UUID, Container],
    costs_in: dict[UUID, Cost],
    fob: Decimal,
    landed: Decimal,
) -> Completeness:
    estimated = ZERO
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        in_period = comp.load_rows[allocation.load_id].container_id in boxes
        counted = cost.cost_type.value not in EXCLUDED_FROM_LANDED
        if cost.status is CostStatus.ESTIMATE and in_period and counted:
            estimated += allocation.amount_base
    costed = {comp.load_rows[a.load_id].container_id for a in comp.result.allocations}
    to_review = db.scalar(
        select(func.count(Invoice.id)).where(
            Invoice.org_id == org.id, Invoice.status == InvoiceStatus.NEEDS_REVIEW
        )
    )
    undated = {c.id for c in comp.containers.values() if arrival_date(c) is None}
    charged_only = {
        cost.container_id
        for cost in comp.cost_rows.values()
        if cost.container_id is not None and cost.container_id not in comp.containers
    }
    if charged_only:
        undated |= {
            c.id
            for c in db.scalars(
                select(Container).where(
                    Container.org_id == org.id,
                    Container.archived_at.is_(None),
                    Container.id.in_(charged_only),
                )
            )
            if arrival_date(c) is None
        }
    return Completeness(
        containers=len(boxes),
        fob=fob,
        landed=landed,
        estimated=q2(estimated),
        estimated_share_pct=(estimated / landed * 100).quantize(TENTH) if landed else None,
        invoices_to_review=int(to_review or 0),
        containers_without_cost=sum(1 for c in boxes if c not in costed),
        fx_sources=dict(Counter(cost.fx_source for cost in costs_in.values())),
        containers_not_compared=sum(1 for c in boxes.values() if _route(c)[0] == UNKNOWN or not _size(c)),
        containers_without_date=len(undated),
    )
