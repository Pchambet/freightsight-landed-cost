"""Nightly backups: dump, encrypt, upload, forget the old ones.

Three properties this has to have, and each one is a decision:

  * **The dump is taken as the owner** (`MIGRATIONS_DATABASE_URL`), never as the application role.
    Row Level Security applies to the application role, so a dump taken with it would be *silently
    partial* — the worst possible failure for a backup, because it restores cleanly.
  * **It is encrypted before it leaves the process.** The bucket holds every customer's data; a
    misconfigured ACL should mean a leaked ciphertext, not a leaked database.
  * **It refuses to run half-configured**, naming the variable that is missing. A backup that quietly
    does nothing is worse than no backup, because you believe in it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import structlog
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.adapters.s3 import S3Client

logger = structlog.get_logger("freightsight.backup")

#: Everything the job needs, and what each is for.
REQUIRED = {
    "MIGRATIONS_DATABASE_URL": "the database to dump, as its owner (RLS must not truncate the dump)",
    "BACKUP_ENCRYPTION_KEY": "32 bytes, base64, used to encrypt the dump before it leaves the process",
    "SCW_ACCESS_KEY": "Scaleway API access key",
    "SCW_SECRET_KEY": "Scaleway API secret key",
    "SCW_BUCKET": "the bucket that holds the backups",
}

KEY_PREFIX = "backups"
#: Our own container format: magic, version, 12-byte nonce, then AES-GCM ciphertext.
MAGIC = b"FSBK1"
NONCE_BYTES = 12
DAILY_KEPT = 30
MONTHLY_KEPT = 12

KEY_RE = re.compile(r"backups/[^/]+/(\d{4})-(\d{2})-(\d{2})T(\d{4})\.dump\.enc$")


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupResult:
    key: str
    size_bytes: int
    sha256: str
    duration_seconds: float
    deleted: list[str]
    taken_at: datetime


def settings_from_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Read the configuration, or say precisely what is missing and stop."""
    source = env if env is not None else dict(os.environ)
    missing = [name for name in REQUIRED if not source.get(name)]
    if missing:
        raise BackupError(
            "Cannot back up, these environment variables are not set:\n"
            + "\n".join(f"  {name}: {REQUIRED[name]}" for name in missing)
        )
    return {
        "database_url": source["MIGRATIONS_DATABASE_URL"],
        "encryption_key": source["BACKUP_ENCRYPTION_KEY"],
        "access_key": source["SCW_ACCESS_KEY"],
        "secret_key": source["SCW_SECRET_KEY"],
        "bucket": source["SCW_BUCKET"],
        "region": source.get("SCW_REGION", "fr-par"),
        "endpoint": source.get("SCW_ENDPOINT", "https://s3.fr-par.scw.cloud"),
        "env": source.get("APP_ENV", "dev"),
        "pg_dump": source.get("PG_DUMP_BIN", "pg_dump"),
        "pg_restore": source.get("PG_RESTORE_BIN", "pg_restore"),
    }


def libpq_url(url: str) -> str:
    """SQLAlchemy spells the driver in the URL; pg_dump does not understand that."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def decode_key(raw: str) -> bytes:
    import base64

    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise BackupError("BACKUP_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != 32:
        raise BackupError(f"BACKUP_ENCRYPTION_KEY must decode to 32 bytes, got {len(key)}")
    return key


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-256-GCM, which the `cryptography` package we already carry provides.

    `age` would be the nicer format, but it means a second dependency and a binary in the image for
    what is one primitive; the container written here is deliberately dull and self-describing.
    """
    nonce = os.urandom(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, MAGIC)


def decrypt(blob: bytes, key: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise BackupError("This file is not a FreightSight backup (bad magic)")
    nonce = blob[len(MAGIC) : len(MAGIC) + NONCE_BYTES]
    return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + NONCE_BYTES :], MAGIC)


def dump(database_url: str, pg_dump: str = "pg_dump") -> bytes:
    """`pg_dump -Fc`, the custom format: compressed, and restorable table by table."""
    try:
        # The binary and the URL both come from our own configuration, never from a request.
        completed = subprocess.run(
            [pg_dump, "--format=custom", "--no-owner", "--no-privileges", libpq_url(database_url)],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackupError(
            f"{pg_dump} is not on the PATH: the image needs postgresql-client of the server's major version"
        ) from exc
    if completed.returncode != 0:
        raise BackupError(f"pg_dump failed: {completed.stderr.decode()[:500]}")
    if not completed.stdout:
        raise BackupError("pg_dump produced nothing")
    return completed.stdout


def restore(database_url: str, archive: bytes, pg_restore: str = "pg_restore") -> None:
    """Restore into an existing, empty-enough database. Never the one you are reading this on."""
    try:
        completed = subprocess.run(
            [pg_restore, "--no-owner", "--no-privileges", "--dbname", libpq_url(database_url)],
            input=archive,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackupError(f"{pg_restore} is not on the PATH") from exc
    if completed.returncode != 0:
        raise BackupError(f"pg_restore failed: {completed.stderr.decode()[:2000]}")
    grant_application_role(database_url)


#: The role the API and the worker connect as, which owns nothing (migration 0003).
APP_ROLE = "freightsight_app"

#: What the migrations grant, replayed on a restored copy. Dumps carry no privileges — they must
#: restore into a database whose roles are not the source's — so without this the copy is readable
#: by its owner alone, the API answers "permission denied" on every table, and the SECURITY DEFINER
#: door through row-level security (`tracking_resolve_subscription`) comes back open to PUBLIC.
#: `tests/test_backup.py` compares the result with a migrated database, privilege by privilege.
APP_GRANTS = f"""
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        CREATE ROLE {APP_ROLE} NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO {APP_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {APP_ROLE};
REVOKE ALL ON FUNCTION tracking_resolve_subscription(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tracking_resolve_subscription(TEXT, TEXT, TEXT) TO {APP_ROLE};
DO $$
DECLARE fn regprocedure;
BEGIN
    FOR fn IN
        SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate%'
    LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO {APP_ROLE}', fn);
    END LOOP;
END $$;
"""


def grant_application_role(database_url: str) -> None:
    """Give the application role back what the migrations gave it, in one transaction."""
    import psycopg

    try:
        with psycopg.connect(libpq_url(database_url)) as connection:
            connection.execute(APP_GRANTS)
    except psycopg.Error as exc:
        raise BackupError(f"restored, but granting {APP_ROLE} failed: {exc}") from exc


def object_key(env: str, moment: datetime) -> str:
    return f"{KEY_PREFIX}/{env}/{moment:%Y-%m-%d}T{moment:%H%M}.dump.enc"


def expired(keys: list[str], *, daily: int = DAILY_KEPT, monthly: int = MONTHLY_KEPT) -> list[str]:
    """Which backups to delete: everything from the last `daily` days, plus the first backup of each
    of the last `monthly` months. Everything else goes.

    "The last 30 days" is counted from the newest backup, not from the newest *thirty* backups: a
    gap in the history — the week the cron was broken — must not make an ancient dump look recent.
    Dates come from the key itself rather than the object's mtime, so a re-uploaded file does not
    look young either.
    """
    dated: list[tuple[str, date, str]] = []  # (key, day, YYYY-MM)
    for key in keys:
        match = KEY_RE.search(key)
        if not match:
            continue  # something else lives in this prefix; leave it alone
        year, month, day, _ = match.groups()
        try:
            when = date(int(year), int(month), int(day))
        except ValueError:  # pragma: no cover - the pattern already constrains this
            continue
        dated.append((key, when, f"{year}-{month}"))
    if not dated:
        return []

    dated.sort(key=lambda item: item[0])
    newest = max(when for _, when, _ in dated)
    keep = {key for key, when, _ in dated if (newest - when).days < daily}

    by_month: dict[str, str] = {}
    for key, _, month in dated:
        by_month.setdefault(month, key)  # the first of the month; keys sort chronologically
    for month in sorted(by_month, reverse=True)[:monthly]:
        keep.add(by_month[month])
    return [key for key, _, _ in dated if key not in keep]


def run(env: dict[str, str] | None = None, *, client: S3Client | None = None) -> BackupResult:
    """Take the backup. Returns what happened; logs one JSON line for whoever is watching."""
    config = settings_from_env(env)
    key_bytes = decode_key(config["encryption_key"])
    started = time.monotonic()

    archive = dump(config["database_url"], config["pg_dump"])
    digest = hashlib.sha256(archive).hexdigest()
    encrypted = encrypt(archive, key_bytes)

    s3 = client or S3Client(
        config["bucket"],
        config["region"],
        config["access_key"],
        config["secret_key"],
        config["endpoint"],
    )
    taken_at = datetime.now(UTC)
    key = object_key(config["env"], taken_at)
    s3.put(key, encrypted, "application/octet-stream")

    existing = [obj.key for obj in s3.list(f"{KEY_PREFIX}/{config['env']}/")]
    deleted = expired(existing)
    for old in deleted:
        s3.delete(old)

    result = BackupResult(
        key=key,
        size_bytes=len(encrypted),
        sha256=digest,
        duration_seconds=round(time.monotonic() - started, 2),
        deleted=deleted,
        taken_at=taken_at,
    )
    # Structured fields rather than a JSON string in the message: the renderer puts them at the top
    # level of the line, where a log search can filter on `size_bytes` instead of matching text.
    logger.info(
        "backup.succeeded",
        key=result.key,
        size_bytes=result.size_bytes,
        plaintext_sha256=result.sha256,
        duration_seconds=result.duration_seconds,
        deleted=len(result.deleted),
        kept=len(existing) + 1 - len(result.deleted),
    )
    return result


#: The `job_runs` row a successful backup leaves behind. The worker's freshness check reads it: the
#: worker holds no storage credentials, and it is the one process that runs every day and reports.
RUN_NAME = "ops.backup"


def note_success(db: Session | Connection, result: BackupResult) -> None:
    """Write down that a backup reached the bucket, with its size and the size of the one before it,
    so that a dump that suddenly holds a third less can be noticed without opening the bucket."""
    previous = db.scalar(text("SELECT detail FROM job_runs WHERE name = :name"), {"name": RUN_NAME})
    try:
        previous_bytes = json.loads(previous)["size_bytes"] if previous else None
    except (ValueError, KeyError, TypeError):
        previous_bytes = None  # a row written by hand or by an older version: no comparison tonight
    detail = {"key": result.key, "size_bytes": result.size_bytes, "previous_bytes": previous_bytes}
    db.execute(
        text(
            "INSERT INTO job_runs (name, last_run_at, failures, detail) VALUES (:name, :at, 0, :detail) "
            "ON CONFLICT (name) DO UPDATE SET last_run_at = EXCLUDED.last_run_at, failures = 0, "
            "detail = EXCLUDED.detail"
        ),
        {"name": RUN_NAME, "at": result.taken_at, "detail": json.dumps(detail)},
    )


def record_success(database_url: str, result: BackupResult) -> None:
    """`note_success` in the database that was just dumped, as its owner."""
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            note_success(connection, result)
    except SQLAlchemyError as exc:
        raise BackupError(f"{result.key} was written but could not be recorded: {exc}") from exc
    finally:
        engine.dispose()


def fetch(key: str, env: dict[str, str] | None = None, *, client: S3Client | None = None) -> bytes:
    """Download one backup and decrypt it."""
    config = settings_from_env(env)
    s3 = client or S3Client(
        config["bucket"],
        config["region"],
        config["access_key"],
        config["secret_key"],
        config["endpoint"],
    )
    return decrypt(s3.get(key), decode_key(config["encryption_key"]))


def database_name(url: str) -> str:
    return urlparse(libpq_url(url)).path.lstrip("/")
