"""Backups: what is dumped, what is encrypted, what is kept, and whether it comes back."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, create_engine, text

from app.adapters.s3 import S3Client, S3NotFound
from app.ops.backup import (
    MAGIC,
    BackupError,
    decode_key,
    decrypt,
    dump,
    encrypt,
    expired,
    fetch,
    libpq_url,
    object_key,
    restore,
    run,
    settings_from_env,
)

KEY = base64.b64encode(b"k" * 32).decode()

ENV = {
    "MIGRATIONS_DATABASE_URL": "postgresql+psycopg://owner:pass@localhost:5432/fs",
    "BACKUP_ENCRYPTION_KEY": KEY,
    "SCW_ACCESS_KEY": "SCW00000000000000000",
    "SCW_SECRET_KEY": "secret",
    "SCW_BUCKET": "freightsight-backups",
    "APP_ENV": "prod",
}


# ---------------------------------------------------------------------------- configuration


def test_a_missing_variable_stops_the_job_and_names_itself() -> None:
    """A backup that quietly does nothing is worse than no backup: you believe in it."""
    incomplete = {k: v for k, v in ENV.items() if k != "SCW_SECRET_KEY"}
    with pytest.raises(BackupError) as raised:
        settings_from_env(incomplete)
    assert "SCW_SECRET_KEY" in raised.value.args[0]
    assert "Scaleway API secret key" in raised.value.args[0]


def test_the_dump_is_taken_as_the_owner_not_the_application_role() -> None:
    """RLS applies to the application role, so a dump taken with it restores cleanly and is missing
    rows. The variable read here is the owner's."""
    config = settings_from_env(ENV)
    assert config["database_url"] == ENV["MIGRATIONS_DATABASE_URL"]
    with pytest.raises(BackupError):
        settings_from_env({k: v for k, v in ENV.items() if k != "MIGRATIONS_DATABASE_URL"})


def test_the_driver_is_stripped_for_libpq() -> None:
    assert libpq_url("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"


def test_the_key_has_to_be_thirty_two_bytes_of_base64() -> None:
    assert len(decode_key(KEY)) == 32
    with pytest.raises(BackupError):
        decode_key("not base64 at all !!")
    with pytest.raises(BackupError):
        decode_key(base64.b64encode(b"too short").decode())


# ---------------------------------------------------------------------------- encryption


def test_the_dump_never_leaves_the_process_in_clear() -> None:
    plaintext = b"PGDMP fake dump with customer data"
    blob = encrypt(plaintext, decode_key(KEY))
    assert blob.startswith(MAGIC)
    assert plaintext not in blob
    assert decrypt(blob, decode_key(KEY)) == plaintext


def test_another_key_cannot_read_it() -> None:
    blob = encrypt(b"secret", decode_key(KEY))
    other = base64.b64encode(b"x" * 32).decode()
    with pytest.raises(Exception):  # noqa: B017 - cryptography raises its own InvalidTag
        decrypt(blob, decode_key(other))


def test_a_file_that_is_not_ours_is_refused() -> None:
    with pytest.raises(BackupError):
        decrypt(b"this is not a backup", decode_key(KEY))


# ---------------------------------------------------------------------------- retention


def keys(days: list[str]) -> list[str]:
    return [f"backups/prod/{day}T0300.dump.enc" for day in days]


def test_thirty_days_and_twelve_months_are_kept() -> None:
    every_day = [f"2026-09-{day:02d}" for day in range(1, 10)]
    old_months = ["2025-10-01", "2025-10-15", "2025-11-01", "2025-12-01"]
    all_keys = keys(old_months + every_day)

    gone = expired(all_keys, daily=3, monthly=2)

    # the three most recent days survive on the daily rule
    assert all(f"2026-09-{day:02d}" not in " ".join(gone) for day in (7, 8, 9))
    # the first backup of each of the last two months survives on the monthly rule
    assert "backups/prod/2025-12-01T0300.dump.enc" not in gone
    assert "backups/prod/2026-09-01T0300.dump.enc" not in gone
    # a mid-month backup from a year ago does not
    assert "backups/prod/2025-10-15T0300.dump.enc" in gone


def test_a_gap_in_the_history_does_not_make_old_backups_look_recent() -> None:
    """The daily window is thirty days from the newest backup, not the newest thirty backups: the
    week the cron was broken must not keep a 2024 dump alive as "recent"."""
    sparse = keys(["2024-01-01", "2024-06-01", "2026-09-08", "2026-09-09"])
    gone = expired(sparse, daily=30, monthly=1)
    assert "backups/prod/2024-01-01T0300.dump.enc" in gone
    assert "backups/prod/2024-06-01T0300.dump.enc" in gone
    assert "backups/prod/2026-09-08T0300.dump.enc" not in gone  # inside the thirty days


def test_retention_leaves_anything_else_in_the_prefix_alone() -> None:
    assert expired(["backups/prod/README.txt", "backups/prod/2020-01-01T0300.dump.enc"]) == []


def test_the_key_says_when_and_which_environment() -> None:
    from datetime import UTC, datetime

    key = object_key("prod", datetime(2026, 9, 9, 3, 0, tzinfo=UTC))
    assert key == "backups/prod/2026-09-09T0300.dump.enc"


# ---------------------------------------------------------------------------- the whole job


def fake_pg_dump(tmp_path: Path, payload: bytes = b"PGDMP fake") -> str:
    """A stand-in binary, so the whole job can be run for real without a database."""
    written = tmp_path / "payload.bin"
    written.write_bytes(payload)
    script = tmp_path / "pg_dump"
    script.write_text(f'#!/bin/sh\ncat "{written}"\n')
    script.chmod(0o755)
    return str(script)


class FakeBucket:
    """An S3 that lives in a dict, so the job can be run for real without a network."""

    def __init__(self, existing: list[str] | None = None) -> None:
        self.objects: dict[str, bytes] = {key: b"old" for key in existing or []}
        self.deleted: list[str] = []

    def put(self, key: str, content: bytes, content_type: str = "") -> None:
        self.objects[key] = content

    def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise S3NotFound(key)
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deleted.append(key)

    def list(self, prefix: str):  # type: ignore[no-untyped-def]
        from app.adapters.s3 import S3Object

        return [S3Object(key, len(v), "") for key, v in self.objects.items() if key.startswith(prefix)]


def test_a_backup_is_dumped_encrypted_uploaded_and_pruned(tmp_path: Path) -> None:
    # Twenty months of history: with 12 monthly kept, the oldest eight have to go.
    history = [f"2024-{month:02d}-15" for month in range(1, 13)]
    history += [f"2025-{month:02d}-15" for month in range(1, 9)]
    bucket = FakeBucket(existing=keys(history))
    env = ENV | {"PG_DUMP_BIN": fake_pg_dump(tmp_path)}

    result = run(env, client=bucket)  # type: ignore[arg-type]

    assert result.key.startswith("backups/prod/")
    stored = bucket.objects[result.key]
    assert stored.startswith(MAGIC)
    assert decrypt(stored, decode_key(KEY)) == b"PGDMP fake"
    assert result.sha256 and result.size_bytes == len(stored)
    # Twenty-one months exist once today's is added; the twelve most recent are kept, nine go.
    assert len(bucket.deleted) == 9
    assert all(key.startswith("backups/prod/2024-0") for key in bucket.deleted)
    assert "backups/prod/2024-10-15T0300.dump.enc" in bucket.objects  # inside the twelve months
    assert "backups/prod/2025-08-15T0300.dump.enc" in bucket.objects


def test_a_download_comes_back_in_clear(tmp_path: Path) -> None:
    bucket = FakeBucket()
    env = ENV | {"PG_DUMP_BIN": fake_pg_dump(tmp_path, b"PGDMP round trip")}
    result = run(env, client=bucket)  # type: ignore[arg-type]
    assert fetch(result.key, env, client=bucket) == b"PGDMP round trip"  # type: ignore[arg-type]


def test_a_missing_pg_dump_says_so(tmp_path: Path) -> None:
    with pytest.raises(BackupError) as raised:
        dump("postgresql://u:p@h/db", str(tmp_path / "nothing-here"))
    assert "postgresql-client" in raised.value.args[0]


# ---------------------------------------------------------------------------- signing and listing


def test_the_upload_is_signed() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://s3.fr-par.test")
    S3Client("bucket", "fr-par", "SCW1", "secret", "https://s3.fr-par.test", client=client).put(
        "backups/prod/x.dump.enc", b"data"
    )
    assert seen["url"] == "https://s3.fr-par.test/bucket/backups/prod/x.dump.enc"
    assert "AWS4-HMAC-SHA256 Credential=SCW1/" in seen["auth"]
    assert "fr-par/s3/aws4_request" in seen["auth"]


def test_listing_follows_the_continuation_token() -> None:
    pages = [
        """<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
             <IsTruncated>true</IsTruncated><NextContinuationToken>tok</NextContinuationToken>
             <Contents><Key>backups/prod/a.dump.enc</Key><Size>10</Size>
               <LastModified>2026-09-01T03:00:00Z</LastModified></Contents>
           </ListBucketResult>""",
        """<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
             <IsTruncated>false</IsTruncated>
             <Contents><Key>backups/prod/b.dump.enc</Key><Size>20</Size>
               <LastModified>2026-09-02T03:00:00Z</LastModified></Contents>
           </ListBucketResult>""",
    ]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=pages[len(calls) - 1])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://s3.fr-par.test")
    objects = S3Client("bucket", "fr-par", "k", "s", "https://s3.fr-par.test", client=client).list(
        "backups/prod/"
    )
    assert [o.key for o in objects] == ["backups/prod/a.dump.enc", "backups/prod/b.dump.enc"]
    assert "continuation-token=tok" in calls[1]


# ---------------------------------------------------------------------------- the real round trip


PG_DUMP = os.environ.get("PG_DUMP_BIN", "pg_dump")
PG_RESTORE = os.environ.get("PG_RESTORE_BIN", "pg_restore")


def pg_tools_available(database_url: str) -> bool:
    """pg_dump refuses a server newer than itself, so the major versions have to match."""
    if not (shutil.which(PG_DUMP) or os.path.exists(PG_DUMP)):
        return False
    if not (shutil.which(PG_RESTORE) or os.path.exists(PG_RESTORE)):
        return False
    try:
        out = subprocess.run([PG_DUMP, "--version"], capture_output=True, check=True).stdout
    except Exception:
        return False
    client_major = out.decode().strip().split()[-1].split(".")[0]
    # SQLAlchemy engines keep the +psycopg driver: stripping it picks the psycopg2 dialect, which is
    # not installed. Only the string handed to pg_dump/pg_restore loses the driver.
    engine = create_engine(database_url)
    with engine.connect() as connection:
        server_major = str(connection.execute(text("SHOW server_version_num")).scalar())[:2]
    engine.dispose()
    return client_major == server_major


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="needs a database URL the dump can be pointed at",
)
def test_a_dump_restores_with_every_row(engine: Engine, database_url: str) -> None:
    """The only test that proves a backup is a backup: dump it, restore it elsewhere, count."""
    if not pg_tools_available(database_url):
        pytest.skip("pg_dump/pg_restore of the server's major version are not installed")

    counts_before = table_counts(engine)
    archive = dump(database_url, PG_DUMP)
    assert archive.startswith(b"PGDMP")

    target = f"restore_check_{uuid.uuid4().hex[:8]}"
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{target}"'))
    try:
        target_url = database_url.rsplit("/", 1)[0] + f"/{target}"
        restore(target_url, encrypt_round_trip(archive), PG_RESTORE)  # restore() strips the driver
        restored = create_engine(target_url)
        assert table_counts(restored) == counts_before
        restored.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
        admin.dispose()


def encrypt_round_trip(archive: bytes) -> bytes:
    """Prove the archive survives the encryption it will actually be stored with."""
    return decrypt(encrypt(archive, decode_key(KEY)), decode_key(KEY))


def table_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename NOT LIKE 'procrastinate%' ORDER BY tablename"
                )
            )
        ]
        return {
            table: connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() or 0
            for table in tables
        }


def test_the_success_line_is_structured_and_goes_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Railway paints stderr red. A nightly backup announcing success in red, for ever, at 03:00,
    is an alarm that teaches people to ignore alarms."""
    from app.core.observability import configure_logging
    from app.core.settings import Settings

    # The suite runs at APP_ENV=test, where the root logger sits at WARNING; this asks for the
    # configuration a deployment gets.
    configure_logging(Settings(database_url="postgresql+psycopg://x@x/x", app_env="dev"))
    bucket = FakeBucket()
    run(ENV | {"PG_DUMP_BIN": fake_pg_dump(tmp_path)}, client=bucket)  # type: ignore[arg-type]
    captured = capsys.readouterr()

    lines = [json.loads(line) for line in captured.out.splitlines() if line.startswith("{")]
    success = [line for line in lines if line.get("event") == "backup.succeeded"]
    assert len(success) == 1, captured.out
    assert success[0]["level"] == "info"
    assert success[0]["size_bytes"] > 0  # a field to filter on, not a sentence to match
    assert success[0]["plaintext_sha256"]
    assert "backup.succeeded" not in captured.err
