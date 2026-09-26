"""`python -m app.restore <key> --to <DATABASE_URL>` — the other half, the one that matters.

A backup nobody has restored is a rumour. This is deliberately a separate command with an explicit
target: it will not guess a database, and it never touches the one the application is using unless
someone types that URL themselves.
"""

from __future__ import annotations

import argparse
import sys

from app.core.observability import configure_logging
from app.ops.backup import BackupError, database_name, fetch, restore

configure_logging()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.restore", description=__doc__)
    parser.add_argument("key", help="the object key, e.g. backups/prod/2026-09-09T0300.dump.enc")
    parser.add_argument("--to", required=True, help="target database URL (this database is written to)")
    parser.add_argument(
        "--yes", action="store_true", help="do not ask; required when stdin is not a terminal"
    )
    args = parser.parse_args(argv)

    target = database_name(args.to)
    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to restore unattended without --yes", file=sys.stderr)
            return 2
        answer = input(f"Restore {args.key} into database {target!r}? This writes into it. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            return 2

    try:
        archive = fetch(args.key)
        restore(args.to, archive)
    except BackupError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"restored {args.key} into {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
