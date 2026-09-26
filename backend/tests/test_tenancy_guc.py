"""The tenant variable must survive a commit even when the pooled connection is reset on checkin.

Reproduces a production 500 ("Could not refresh instance"): the API commits, the connection goes back
to the pool where the checkin hook resets app.org_id, and the next statement runs without a tenant.
"""

from __future__ import annotations

import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app.core.tenancy import set_current_org
from app.domain.models import Organization


def test_org_guc_is_reapplied_after_commit(database_url: str, engine: object) -> None:
    from app.main import install_guc_reset  # needs DATABASE_URL, set by the engine fixture

    engine = create_engine(database_url, poolclass=QueuePool, pool_size=1, max_overflow=0)
    install_guc_reset(engine)
    org_id = uuid.uuid4()
    try:
        with Session(engine) as s:
            set_current_org(s, org_id)
            assert s.scalar(text("SELECT current_setting('app.org_id', true)")) == str(org_id)
            s.commit()  # connection returned to the pool -> RESET app.org_id
            assert s.scalar(text("SELECT current_setting('app.org_id', true)")) == str(org_id)
            s.execute(select(Organization).limit(1))
            s.commit()
            assert s.scalar(text("SELECT current_setting('app.org_id', true)")) == str(org_id)
        # a session that never set a tenant sees nothing set (no leak from the previous session)
        with Session(engine) as s2:
            assert (s2.scalar(text("SELECT current_setting('app.org_id', true)")) or "") == ""
    finally:
        engine.dispose()
