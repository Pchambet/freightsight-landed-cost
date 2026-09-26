"""`python -m app.doctor` — what is switched on in this environment, and what it takes to finish.

Run it where the variables are: `railway run -s FreightSight python -m app.doctor` for production.
It prints names and states, never a value. Exit code 1 when something a customer relies on is off or
degraded, so it can gate a go-live checklist.
"""

from __future__ import annotations

import sys

from app.core.db import get_engine
from app.core.settings import get_settings
from app.domain.readiness import Check, checks, job_runs, webhook_router_sees_tenants

MARK = {"ok": "ok      ", "degraded": "DEGRADED", "off": "OFF     "}


def render(found: list[Check]) -> str:
    lines = []
    for audience, title in (("customer", "What a customer relies on"), ("operator", "What only you see")):
        lines.append(f"\n{title}")
        for check in (c for c in found if c.audience == audience):
            detail = " ".join(f"{k}={v}" for k, v in check.params.items() if v)
            lines.append(
                f"  {MARK[check.state]}  {check.key:<22} {check.code}{'  ' + detail if detail else ''}"
            )
            if check.fix:
                lines.append(f"            → {check.fix}")
    return "\n".join(lines)


def main() -> int:
    settings = get_settings()
    try:
        with get_engine().connect() as conn:
            runs = job_runs(conn)
            router_ok = webhook_router_sees_tenants(conn)
    except Exception as exc:  # the database being down is the first thing a doctor should say
        print(f"database unreachable: {exc}", file=sys.stderr)
        return 2
    found = checks(settings, runs, webhook_router_ok=router_ok)
    print(f"FreightSight — APP_ENV={settings.app_env}")
    print(render(found))
    unhealthy = [c for c in found if c.audience == "customer" and c.state != "ok"]
    print(f"\n{len(unhealthy)} thing(s) a customer relies on are not fully on.")
    return 1 if unhealthy else 0


if __name__ == "__main__":
    sys.exit(main())
