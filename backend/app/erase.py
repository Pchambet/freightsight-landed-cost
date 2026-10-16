"""`python -m app.erase <organization>` — delete a company from the database, at the end of its contract.

Run it inside the API's container, where `MIGRATIONS_DATABASE_URL` reaches the database over the private
network (`railway ssh -s FreightSight -e production`, then the command), naming the organization by its
id, its Clerk id or its slug: the ops runbook, section 9.

Without `--confirm` it deletes nothing and prints what would go; with `--confirm` followed by the
organization's exact name or slug it deletes every row of it, audit log included, in one transaction —
or nothing, and says why.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text

from app.ops.erase import ErasureError, Plan, erase, find, plan


def render(found: Plan) -> str:
    org = found.organization
    lines = [
        f"organization {org.name!r}  slug={org.slug or '-'}  id={org.id}  clerk={org.external_id or '-'}",
        *(f"  {count:>8}  {table}" for table, count in found.rows.items() if count),
        f"  {sum(found.rows.values()):>8}  rows in {sum(1 for c in found.rows.values() if c)} tables",
        f"  {len(found.users):>8}  users who belong to no other organization",
    ]
    if found.documents_in_object_storage:
        lines.append(
            f"  {found.documents_in_object_storage:>8}  documents in object storage under {org.id}/ "
            "(deleted from the bucket by hand)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.erase", description=__doc__)
    parser.add_argument("organization", help="its id, its Clerk id (org_…) or its slug")
    parser.add_argument("--confirm", metavar="NAME", help="the organization's exact name or slug: deletes it")
    args = parser.parse_args(argv)

    # The owner, never the application role: under row-level security the application role would
    # delete what it sees, and the audit log refuses it DELETE outright.
    url = os.environ.get("MIGRATIONS_DATABASE_URL")
    if not url:
        print("MIGRATIONS_DATABASE_URL is not set: this runs as the owner of the tables", file=sys.stderr)
        return 2
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            who, superuser = conn.execute(
                text("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")
            ).one()
            print(f"connected as {who} ({'superuser' if superuser else 'not a superuser'})")
            org = find(conn, args.organization)
            if args.confirm is None:
                print(render(plan(conn, org)))
                print(f"\ndry run, nothing deleted. To delete: --confirm {org.slug or org.name!r}")
                return 0
            done = erase(conn, org, confirm=args.confirm)
    except ErasureError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print(render(done))
    print("\ndeleted. Still to do by hand: the ops runbook, section 9.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
