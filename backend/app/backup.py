"""`python -m app.backup` — the nightly dump. One Railway cron service runs this."""

from __future__ import annotations

import logging
import os

from app.core.observability import configure_logging, init_sentry
from app.core.settings import Settings
from app.ops.backup import BackupError, record_success, run

configure_logging()
logger = logging.getLogger("freightsight.backup")


def main() -> int:
    # This service has MIGRATIONS_DATABASE_URL and no DATABASE_URL: the settings are built on the
    # owner's URL only so that a failure at 03:00 reaches Sentry rather than a log nobody reads.
    try:
        init_sentry(Settings(database_url=os.environ.get("MIGRATIONS_DATABASE_URL", "")))
    except Exception:  # reporting must never be the reason a backup did not run
        logger.exception("Sentry could not be started for the backup")
    try:
        result = run()
        # The worker's freshness check reads this row: it holds no storage credentials of its own.
        record_success(os.environ["MIGRATIONS_DATABASE_URL"], result)
    except BackupError as exc:
        # A backup that fails silently is worse than none: say what is wrong — at ERROR, which is what
        # Sentry picks up — exit non-zero, and let the scheduler's own alerting see it too.
        logger.error("backup failed: %s", exc)
        return 1
    print(f"backup written to {result.key} ({result.size_bytes} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
