"""One piece of an order line given as its total adds back up to that total.

An order file that states a line's total weight or volume has it divided by the line's quantity, and the
per-unit columns kept four (weight) and six (volume) decimals: 120 kg over 2 000 000 labels was stored
as 0.0001 kg each — 200 kg for the line, two thirds more of a freight split by weight — and 0.5 kg over
20 000 pieces as nothing. Ten more decimals carry any line of up to ten billion pieces back to its total.
The digits before the point are unchanged, so every value already stored fits as it is.

No new table, so no new row-level security policy.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column("po_lines", "unit_weight_kg", type_=sa.Numeric(22, 14))
    op.alter_column("po_lines", "unit_volume_cbm", type_=sa.Numeric(22, 16))


def downgrade() -> None:
    # Rounds back to four and six decimals: the totals of the lines it touches drift again.
    op.alter_column("po_lines", "unit_volume_cbm", type_=sa.Numeric(12, 6))
    op.alter_column("po_lines", "unit_weight_kg", type_=sa.Numeric(12, 4))
