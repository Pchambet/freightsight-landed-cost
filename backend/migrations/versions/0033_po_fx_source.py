"""Where an order's exchange rate came from, and which frozen lines had no charge.

`purchase_orders.fx_source` says where the rate came from (ecb, ecb_latest, manual, import, same). A
rate a person typed is then kept when the order's dates are edited, and the orders still at the 1 the
import used to default to (`fx_rate = 1`, no `fx_date`, a foreign currency) are the ones with no
source at all: a person confirms or sets their rate, which records one.

`period_close_lines.without_charge` says that the line's container carried no charge when the month
was closed: its stock value stays its FOB, and the month's landed cost leaves it out. Lines frozen
before this revision have none, and are read from the charges they froze.

Existing rows are not filled in here: both tables are under FORCE row-level security, so a migration
sees no organization's rows.

No new table, so no new row-level security policy.

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("purchase_orders", sa.Column("fx_source", sa.String(), nullable=True))
    op.add_column("period_close_lines", sa.Column("without_charge", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("period_close_lines", "without_charge")
    op.drop_column("purchase_orders", "fx_source")
