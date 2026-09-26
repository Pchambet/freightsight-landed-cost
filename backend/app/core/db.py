from collections.abc import Iterator
from functools import lru_cache

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session


def assert_migrated(engine: Engine, alembic_ini: str = "alembic.ini") -> None:
    """Refuse to serve on a database that is not at the latest migration."""
    script = ScriptDirectory.from_config(AlembicConfig(alembic_ini))
    head = script.get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current != head:
        raise RuntimeError(
            f"Database is at revision {current!r}, expected {head!r}. Run `alembic upgrade head`."
        )
