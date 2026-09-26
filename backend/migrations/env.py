import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.domain.models import Base

config = context.config
# Migrations need the table owner (superuser on Railway); the app runs as the least-privilege role.
database_url = os.environ.get("MIGRATIONS_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("MIGRATIONS_DATABASE_URL or DATABASE_URL must be set to run migrations")
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, _parent: object) -> bool:
    """Keep Procrastinate's own tables out of autogenerate.

    The queue's schema is installed by migration 0005 from the package's own SQL and is not described
    by our models; without this, `alembic check` sees tables it does not know and proposes to drop
    the whole queue.
    """
    return not (type_ == "table" and name and name.startswith("procrastinate"))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
