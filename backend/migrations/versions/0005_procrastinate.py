"""Procrastinate's schema (decision 6: a Postgres queue, no Redis).

The SQL is Procrastinate's own, taken from the installed package rather than copied into this file:
a hand-copied thousand lines would drift silently the first time the dependency moves. What keeps
that honest is the version guard below — upgrading Procrastinate past the pinned version fails this
migration loudly, and the upgrade then gets its own Alembic revision applying the packaged delta from
`procrastinate/sql/migrations/`.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The Procrastinate schema this revision was written against. Keep in step with pyproject.toml.
PROCRASTINATE_VERSION = "3.9"

APP_ROLE = "freightsight_app"


def upgrade() -> None:
    import procrastinate
    from procrastinate.schema import SchemaManager

    installed = ".".join(procrastinate.__version__.split(".")[:2])
    if installed != PROCRASTINATE_VERSION:
        raise RuntimeError(
            f"This revision installs the Procrastinate {PROCRASTINATE_VERSION} schema but "
            f"Procrastinate {procrastinate.__version__} is installed. Add a new revision applying "
            f"the packaged migrations from procrastinate/sql/migrations/ instead of editing this one."
        )
    # op.execute wraps the string in text(), where % is literal: no escaping, unlike Procrastinate's
    # own apply_schema which feeds psycopg directly.
    op.execute(SchemaManager.get_schema())

    # The application and the worker connect as freightsight_app, which owns none of this.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"""
        DO $$
        DECLARE obj RECORD;
        BEGIN
            FOR obj IN
                SELECT c.relname, c.relkind FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname LIKE 'procrastinate%'
                  AND c.relkind IN ('r', 'S')
            LOOP
                IF obj.relkind = 'r' THEN
                    EXECUTE format(
                        'GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO {APP_ROLE}', obj.relname
                    );
                ELSE
                    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE public.%I TO {APP_ROLE}', obj.relname);
                END IF;
            END LOOP;
            FOR obj IN
                SELECT p.oid::regprocedure AS signature FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate%'
            LOOP
                EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO {APP_ROLE}', obj.signature);
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    """Drop everything Procrastinate owns. Its schema ships no teardown, so this walks the catalogue."""
    op.execute(
        """
        DO $$
        DECLARE obj RECORD;
        BEGIN
            FOR obj IN
                SELECT p.oid::regprocedure AS signature FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate%'
            LOOP
                EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', obj.signature);
            END LOOP;
            FOR obj IN
                SELECT c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname LIKE 'procrastinate%' AND c.relkind = 'r'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', obj.relname);
            END LOOP;
            FOR obj IN
                SELECT t.typname FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'public' AND t.typname LIKE 'procrastinate%'
            LOOP
                EXECUTE format('DROP TYPE IF EXISTS public.%I CASCADE', obj.typname);
            END LOOP;
        END $$;
        """
    )
