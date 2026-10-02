#!/usr/bin/env python3
"""The README's worked example: one real-looking container, three allocation policies, run through the
pure engine.

    cd backend && uv run python scripts/worked_example.py             # print the Markdown table
    cd backend && uv run --with matplotlib python scripts/worked_example.py \\
        --figure ../docs/figures/worked-example.png                  # also redraw the hero figure

The container is MSCU4821990 from the sample dataset (`app.domain.sample_data`) with the five actual
lines of the demo forwarder invoice. The "FreightSight default" policy is what the application shows for
it after the invoice is confirmed — `tests/test_demo_story.py` pins the same two unit costs through the
API and a real database — so the table below is the product's own number, not a re-implementation.
`tests/test_worked_example.py` fails if the README block drifts from what this script prints.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost:1/x")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domain import sample_data as seed
from app.domain.costing.engine import Cost, Load, allocate

NS = UUID("6f2b8e0c-3d1a-4c55-9a59-6b1f0f4c1e00")  # stable ids, so every run allocates identically
CONTAINER = uuid5(NS, "MSCU4821990")

#: (sku, label, PO number, quantity, unit price €, kg/unit, m³/unit, duty rate) — the seed's two lines.
LINES = [
    ("TYR-20555R16-91V", "Tyre 205/55 R16", "PO-2026-014", "750", "14.00", "9", "0.050", seed.TYRE_DUTY_RATE),
    ("MAT-EVA-6040-GY", "EVA floor mat", "PO-2026-015", "500", "20.00", "1.5", "0.010", seed.MAT_DUTY_RATE),
]

#: The demo invoice's actual charges on that container.
CHARGES = [
    ("OCEAN_FREIGHT", seed.DEMO_INVOICE_OCEAN_FREIGHT),
    ("THC", seed.DEMO_INVOICE_THC),
    ("CUSTOMS_BROKERAGE", seed.DEMO_INVOICE_CUSTOMS_BROKERAGE),
    ("DRAYAGE", seed.DEMO_INVOICE_DRAYAGE),
    ("CUSTOMS_DUTY", seed.DEMO_INVOICE_CUSTOMS_DUTY),
]

#: Space-driven charges: what a box costs to move does not depend on what the goods are worth.
SPACE_DRIVEN = {"OCEAN_FREIGHT", "THC", "DRAYAGE"}


@dataclass(frozen=True)
class Policy:
    name: str
    description: str
    freight_method: str  # ocean freight, THC and haulage
    duty_method: str


POLICIES = [
    Policy("Spreadsheet", "every charge pro rata of FOB value", "BY_VALUE", "BY_VALUE"),
    Policy(
        "FreightSight default",
        "value pro rata, duty on each line's rate times CIF value (second pass)",
        "BY_VALUE",
        "BY_THEORETICAL_DUTY",
    ),
    Policy(
        "Freight by volume",
        "freight, THC and haulage by m³, duty on rate times CIF value",
        "BY_VOLUME",
        "BY_THEORETICAL_DUTY",
    ),
]


def loads() -> list[Load]:
    return [
        Load(
            id=uuid5(NS, sku),
            container_id=CONTAINER,
            shipment_id=uuid5(NS, "MEDUSH2604417"),
            po_id=uuid5(NS, po),
            po_line_id=uuid5(NS, f"{po}/1"),
            quantity=Decimal(qty),
            unit_price_base=Decimal(price),
            unit_weight_kg=Decimal(kg),
            unit_volume_cbm=Decimal(cbm),
            duty_rate=duty,
            sort_key=("MSCU4821990", po, 1),
        )
        for sku, _, po, qty, price, kg, cbm, duty in LINES
    ]


def costs(policy: Policy) -> list[Cost]:
    def method(cost_type: str) -> str:
        if cost_type == "CUSTOMS_DUTY":
            return policy.duty_method
        return policy.freight_method if cost_type in SPACE_DRIVEN else "BY_VALUE"

    return [
        Cost(uuid5(NS, cost_type), "CONTAINER", CONTAINER, cost_type, amount, method(cost_type))
        for cost_type, amount in CHARGES
    ]


def unit_landed_costs(policy: Policy) -> dict[str, Decimal]:
    """SKU -> landed cost per unit (FOB + allocated charges) / quantity, to 4 decimals like the API."""
    result = allocate(costs(policy), loads())
    if result.unallocated:
        raise RuntimeError(f"{policy.name}: unallocated {result.unallocated}")
    out = {}
    for (sku, *_), ld in zip(LINES, loads(), strict=True):
        allocated = sum((a.amount_base for a in result.allocations if a.load_id == ld.id), Decimal(0))
        out[sku] = ((ld.fob + allocated) / ld.quantity).quantize(Decimal("0.0001"))
    return out


def container_total(policy: Policy) -> Decimal:
    result = allocate(costs(policy), loads())
    return sum((ld.fob for ld in loads()), Decimal(0)) + sum(
        (a.amount_base for a in result.allocations), Decimal(0)
    )


def eur(value: Decimal) -> str:
    return f"{value:,.2f} €"


def markdown() -> str:
    table = {p.name: unit_landed_costs(p) for p in POLICIES}
    head = "| SKU | Qty | FOB / unit | " + " | ".join(p.name for p in POLICIES) + " |"
    rows = [head, "|---|--:|--:|" + "--:|" * len(POLICIES)]
    for sku, label, _, qty, price, _, cbm, _ in LINES:
        cells = " | ".join(eur(table[p.name][sku]) for p in POLICIES)
        rows.append(f"| `{sku}` ({label}, {cbm} m³) | {qty} | {eur(Decimal(price))} | {cells} |")
    totals = {container_total(p) for p in POLICIES}
    if len(totals) != 1:
        raise RuntimeError(f"policies disagree on the container total: {totals}")
    rows.append("")
    rows += [f"- **{p.name}**: {p.description}." for p in POLICIES]
    rows.append("")
    rows.append(
        f"Container landed cost: **{eur(totals.pop())}** under all three policies "
        "(the parts of every charge sum to the cent)."
    )
    return "\n".join(rows) + "\n"


def figure(path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, teal, amber, slate, grid = "#0f172a", "#0d9488", "#d97706", "#64748b", "#e2e8f0"
    colours = [slate, teal, amber]
    table = [unit_landed_costs(p) for p in POLICIES]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), dpi=200)
    for ax, (sku, label, _, _, price, _, cbm, _) in zip(axes, LINES, strict=True):
        fob = float(price)
        landed = [float(t[sku]) for t in table]
        rows = range(len(POLICIES))
        ax.barh(rows, [fob] * len(POLICIES), color=grid, height=0.6)
        ax.barh(rows, [v - fob for v in landed], left=fob, color=colours, height=0.6)
        ax.text(fob / 2, 0, f"FOB {fob:.2f} €", ha="center", va="center", fontsize=8, color=slate)
        for row, v in zip(rows, landed, strict=True):
            note = f"{v:.2f} €  (+{v - fob:.2f} € charges)"
            ax.text(v + 0.3, row, note, va="center", fontsize=8.5, color=ink)
        ax.set_yticks(list(rows), [p.name for p in POLICIES], fontsize=9, color=ink)
        ax.invert_yaxis()
        ax.set_xlim(0, max(landed) * 1.55)
        ax.set_xlabel("Landed cost per unit (€)", color=ink, fontsize=9)
        charges = [v - fob for v in landed]
        ax.set_title(
            f"{label}, {cbm} m³ per unit\n"
            f"logistics charges per unit: {min(charges):.2f} to {max(charges):.2f} €",
            fontsize=10,
            color=ink,
            loc="left",
        )
        ax.grid(axis="x", color=grid)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="x", colors=slate, labelsize=8)
        ax.tick_params(axis="y", length=0)
    fig.suptitle(
        f"One container, {eur(container_total(POLICIES[0]))} landed: "
        "the allocation method decides what each product costs",
        fontsize=11,
        color=ink,
        x=0.01,
        ha="left",
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--figure", type=Path, help="also write the hero figure (needs matplotlib)")
    args = parser.parse_args()
    sys.stdout.write(markdown())
    if args.figure:
        figure(args.figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
