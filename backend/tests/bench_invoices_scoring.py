"""How a reading is judged. One rule matters more than the rest: a reading may be incomplete, it
may not be *silently* incomplete — every miss has to show up as a failed arithmetic check, because
that is what puts the invoice in front of a person with a warning instead of a green light."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import bench_invoices_hard as hard
from bench_invoices_corpus import Case, Expected, cases

from app.adapters.extraction.regex_extractor import RegexExtractor
from app.domain.invoices.checks import arithmetic_holds
from app.domain.invoices.ports import ExtractionContext, ExtractionFailed, ExtractionInput, FreightInvoice
from app.domain.models import CostType

CENT = Decimal("0.01")
REAL = Path(__file__).parent / "fixtures" / "invoices_real"


@dataclass
class Score:
    case: Case
    failure: str | None = None
    expected_lines: int = 0
    found_lines: int = 0
    invented_lines: int = 0
    typed_expected: int = 0
    typed_right: int = 0
    header: dict[str, bool] = field(default_factory=dict)
    flagged: bool = False

    @property
    def lines_wrong(self) -> bool:
        return self.found_lines < self.expected_lines or self.invented_lines > 0

    @property
    def silent(self) -> bool:
        """Wrong, and nothing on the reading says so."""
        return self.lines_wrong and not self.flagged and self.failure is None


def context() -> ExtractionContext:
    return ExtractionContext(base_currency="EUR", containers={}, purchase_orders={}, shipment_references={})


def read(case: Case) -> FreightInvoice:
    return RegexExtractor().extract(
        ExtractionInput(case.pdf, "application/pdf", f"{case.name}.pdf"), context()
    )


def score(case: Case) -> Score:
    expected = case.expected
    result = Score(case, expected_lines=len(expected.lines))
    result.typed_expected = sum(1 for _, cost_type in expected.lines if cost_type is not None)
    try:
        invoice = read(case)
    except ExtractionFailed as exc:
        result.failure = exc.code
        result.header = dict.fromkeys(
            ("number", "date", "currency", "subtotal", "total", "containers"), False
        )
        return result

    # Lines are matched on the amount: a line read with the wrong money is a line not read.
    wanted: dict[Decimal, list[CostType | None]] = defaultdict(list)
    for amount, cost_type in expected.lines:
        wanted[amount.quantize(CENT)].append(cost_type)
    for line in invoice.lines:
        amount = (line.decimal() or Decimal(0)).quantize(CENT)
        if wanted.get(amount):
            cost_type = wanted[amount].pop(0)
            result.found_lines += 1
            if cost_type is not None and line.cost_type == cost_type:
                result.typed_right += 1
        else:
            result.invented_lines += 1

    def money(value: str | None) -> Decimal | None:
        return Decimal(value).quantize(CENT) if value is not None else None

    result.header = {
        "number": invoice.invoice_number == expected.invoice_number,
        "date": invoice.invoice_date == expected.invoice_date,
        "currency": invoice.currency == expected.currency,
        "subtotal": money(invoice.subtotal)
        == (expected.subtotal.quantize(CENT) if expected.subtotal else None),
        "total": money(invoice.total) == expected.total.quantize(CENT),
        "containers": set(expected.containers) <= set(invoice.container_numbers),
    }
    holds, _ = arithmetic_holds(invoice)
    result.flagged = not holds
    return result


def real_cases() -> list[Case]:
    """Anonymised invoices a customer agreed to share: `name.pdf` next to `name.json` in REAL."""
    found = []
    for pdf in sorted(REAL.glob("*.pdf")):
        spec = json.loads(pdf.with_suffix(".json").read_text())
        expected = Expected(
            invoice_number=spec["invoice_number"],
            invoice_date=date.fromisoformat(spec["invoice_date"]),
            currency=spec["currency"],
            subtotal=Decimal(spec["subtotal"]) if spec.get("subtotal") else None,
            total=Decimal(spec["total"]),
            lines=tuple(
                (Decimal(line["amount"]), CostType(line["cost_type"]) if line.get("cost_type") else None)
                for line in spec["lines"]
            ),
            containers=tuple(spec.get("containers", ())),
        )
        found.append(
            Case(pdf.stem, spec.get("family", "facture réelle"), pdf.read_bytes(), expected, ("real",))
        )
    return found


@dataclass
class Totals:
    cases: int = 0
    failures: Counter[str] = field(default_factory=Counter)
    expected_lines: int = 0
    found_lines: int = 0
    invented_lines: int = 0
    typed_expected: int = 0
    typed_right: int = 0
    header_checks: int = 0
    header_right: int = 0
    perfect: int = 0
    silent: int = 0

    def add(self, s: Score) -> None:
        self.cases += 1
        if s.failure:
            self.failures[s.failure] += 1
        self.expected_lines += s.expected_lines
        self.found_lines += s.found_lines
        self.invented_lines += s.invented_lines
        self.typed_expected += s.typed_expected
        self.typed_right += s.typed_right
        self.header_checks += len(s.header)
        self.header_right += sum(s.header.values())
        self.perfect += int(not s.lines_wrong and all(s.header.values()))
        self.silent += int(s.silent)

    @property
    def recall(self) -> float:
        return self.found_lines / self.expected_lines if self.expected_lines else 1.0

    @property
    def type_accuracy(self) -> float:
        return self.typed_right / self.typed_expected if self.typed_expected else 1.0

    @property
    def header_accuracy(self) -> float:
        return self.header_right / self.header_checks if self.header_checks else 1.0


def run(selected: list[Case] | None = None) -> tuple[list[Score], Totals, dict[str, Totals]]:
    scores = [
        score(case) for case in (selected if selected is not None else cases() + hard.cases() + real_cases())
    ]
    overall = Totals()
    by_family: dict[str, Totals] = defaultdict(Totals)
    for s in scores:
        overall.add(s)
        by_family[s.case.family].add(s)
    return scores, overall, dict(by_family)


def report() -> str:
    scores, overall, by_family = run()
    columns = ("Mise en page", "Factures", "Lignes lues", "Lignes inventées", "Type de coût juste",
               "En-tête juste", "Lecture parfaite", "Erreur silencieuse")  # fmt: skip
    rows = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for family, t in [*sorted(by_family.items()), ("**Ensemble**", overall)]:
        rows.append(
            f"| {family} | {t.cases} | {t.found_lines}/{t.expected_lines} ({t.recall:.0%}) "
            f"| {t.invented_lines} "
            f"| {t.typed_right}/{t.typed_expected} ({t.type_accuracy:.0%}) | {t.header_accuracy:.0%} "
            f"| {t.perfect}/{t.cases} | {t.silent} |"
        )
    worst = [s for s in scores if s.lines_wrong or s.failure or not all(s.header.values())]
    detail = [
        f"- {s.case.name} : "
        + (
            f"échec `{s.failure}`"
            if s.failure
            else f"{s.found_lines}/{s.expected_lines} lignes, {s.invented_lines} inventée(s)"
        )
        + (
            ""
            if all(s.header.values())
            else " ; en-tête : " + ", ".join(k for k, ok in s.header.items() if not ok)
        )
        + (" ; **silencieux**" if s.silent else "")
        for s in worst
    ]
    return "\n".join(rows) + ("\n\n" + "\n".join(detail) if detail else "")


if __name__ == "__main__":
    print(report())
