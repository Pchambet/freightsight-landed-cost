#!/usr/bin/env python3
"""End-to-end smoke test against a running local stack (docker compose).

Usage:
  python scripts/e2e_live_demo.py
  API_URL=http://localhost:8001 python scripts/e2e_live_demo.py

Requires: API + Postgres + worker (APP_ENV=dev and ALLOW_DEV_PRINCIPAL=true, for X-Org-Id auth —
docker-compose.yml sets both; the header alone is no longer enough).
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import httpx

API = os.environ.get("API_URL", "http://localhost:8001").rstrip("/")
PDF = Path(__file__).resolve().parents[1] / "docs/demo/fixtures/facture-transdemo-demo.pdf"
POLL_SEC = 2
EXTRACT_TIMEOUT = 90

# After sample data, only add invoice lines that are not already in sample costs.
ACCEPT_COST_TYPES = {"THC", "CUSTOMS_CLEARANCE", "CUSTOMS_BROKERAGE", "BROKERAGE", "IMPORT_DUTY"}


class Step:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> Step:
        print(f"\n▶ {self.name}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            print(f"  ✓ {self.name}")
        else:
            print(f"  ✗ {self.name}: {exc}")


def fail(msg: str) -> None:
    raise AssertionError(msg)


def main() -> int:
    org_id = str(uuid.uuid4())
    headers = {"X-Org-Id": org_id}
    client = httpx.Client(base_url=API, headers=headers, timeout=60.0)

    print(f"API={API}  org={org_id}")

    with Step("Health check"):
        r = client.get("/healthz")
        if r.status_code != 200:
            fail(f"/healthz → {r.status_code}")

    with Step("Organization bootstrap"):
        r = client.get("/api/v1/organization")
        if r.status_code != 200:
            fail(f"organization → {r.status_code} {r.text}")
        assert r.json()["base_currency"] == "EUR"

    with Step("Load sample data"):
        r = client.post("/api/v1/organization/sample-data")
        if r.status_code != 201:
            fail(f"sample-data → {r.status_code} {r.text}")
        body = r.json()
        assert body["containers"] == 2 and body["purchase_orders"] == 2

    with Step("Containers hub"):
        containers = client.get("/api/v1/containers").json()
        by_num = {c["container_number"]: c for c in containers}
        if "MSCU4821990" not in by_num:
            fail("MSCU4821990 missing from sample data")
        mscu = by_num["MSCU4821990"]
        assert mscu["load_count"] > 0
        print(f"  MSCU allocated={mscu['allocated_base']} risk={mscu['dnd_risk']}")

    with Step("Demurrage alert after sample data"):
        org = client.get("/api/v1/organization").json()
        if org.get("unread_alerts", 0) < 1:
            fail(f"expected unread demurrage alert for MSCU, got {org.get('unread_alerts')}")
        alerts = client.get("/api/v1/alerts").json()
        dnd = [a for a in alerts if a.get("kind") == "DND_RISK" and a.get("container_id") == mscu["id"]]
        if not dnd:
            fail("no DND_RISK alert for MSCU4821990")
        print(f"  unread_alerts={org['unread_alerts']} dnd_alerts={len(dnd)}")

    with Step("Landed cost before invoice"):
        report = client.get(f"/api/v1/landed-costs/containers/{mscu['id']}").json()
        lines = {ln["po_number"]: ln["unit_landed_cost"] for ln in report["lines"]}
        before_pneu = lines.get("PO-2026-014")
        if not before_pneu:
            fail(f"no PO-2026-014 line in report: {lines}")
        print(f"  PO-2026-014 unit before invoice: {before_pneu}")

    with Step("Upload demo invoice PDF"):
        if not PDF.is_file():
            fail(f"missing {PDF}")
        with PDF.open("rb") as f:
            r = client.post(
                "/api/v1/invoices",
                files={"file": ("facture-transdemo-demo.pdf", f, "application/pdf")},
            )
        if r.status_code != 201:
            fail(f"upload → {r.status_code} {r.text}")
        invoice_id = r.json()["id"]
        print(f"  invoice_id={invoice_id}")

    with Step("Worker extracts invoice (poll)"):
        deadline = time.time() + EXTRACT_TIMEOUT
        status = "UPLOADED"
        while time.time() < deadline:
            inv = client.get(f"/api/v1/invoices/{invoice_id}").json()
            status = inv["status"]
            if status in ("NEEDS_REVIEW", "CONFIRMED", "FAILED"):
                break
            time.sleep(POLL_SEC)
        if status == "UPLOADED" or status == "EXTRACTING":
            fail(
                f"extraction timed out after {EXTRACT_TIMEOUT}s (status={status}). "
                "Is the worker running? Try: docker compose logs worker"
            )
        if status == "FAILED":
            fail(f"extraction failed: {inv.get('error')}")
        lines = inv.get("lines") or []
        if not lines:
            fail("no lines extracted")
        print(f"  status={status} lines={len(lines)} vendor={inv.get('vendor')}")

    with Step("Accept THC + clearance (skip freight/drayage duplicates)"):
        accepted = 0
        for line in lines:
            ct = line.get("cost_type")
            if ct not in ACCEPT_COST_TYPES:
                print(f"  skip {ct}: {line.get('description', '')[:40]}")
                continue
            r = client.patch(
                f"/api/v1/invoices/{invoice_id}/lines/{line['id']}",
                json={"accepted": True},
            )
            if r.status_code != 200:
                fail(f"accept line → {r.status_code} {r.text}")
            accepted += 1
        if accepted == 0:
            fail("no lines accepted — check ACCEPT_COST_TYPES vs extracted cost_type values")
        print(f"  accepted {accepted} line(s)")

    with Step("Confirm invoice"):
        r = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
        if r.status_code != 200:
            fail(f"confirm → {r.status_code} {r.text}")
        confirmed = r.json()
        assert confirmed["invoice"]["status"] == "CONFIRMED"
        assert confirmed["costs_created"] == accepted
        container_ids = [c["id"] for c in confirmed.get("containers") or []]
        if not container_ids:
            fail("confirm response missing containers")
        print(f"  costs_created={confirmed['costs_created']} containers={len(container_ids)}")

    with Step("Landed cost after invoice (unit cost should rise)"):
        report = client.get(f"/api/v1/landed-costs/containers/{mscu['id']}").json()
        lines_after = {ln["po_number"]: ln["unit_landed_cost"] for ln in report["lines"]}
        after_pneu = lines_after.get("PO-2026-014")
        if not after_pneu:
            fail(f"no PO-2026-014 after confirm: {lines_after}")
        print(f"  PO-2026-014 unit after:  {after_pneu} (was {before_pneu})")
        if float(after_pneu) <= float(before_pneu):
            fail(f"expected unit cost to increase: {before_pneu} → {after_pneu}")

    with Step("Reports integrity"):
        r = client.get("/api/v1/reports/integrity")
        if r.status_code != 200:
            fail(f"integrity → {r.status_code}")
        integrity = r.json()
        if not integrity.get("ok"):
            fail(f"integrity check failed: {integrity}")

    with Step("Purchase orders + imports list"):
        pos = client.get("/api/v1/purchase-orders").json()
        imports = client.get("/api/v1/invoices").json()
        assert len(pos) >= 2
        assert any(i["id"] == invoice_id for i in imports)

    print("\n════════════════════════════════════════")
    print("E2E LIVE DEMO: ALL STEPS PASSED")
    print("════════════════════════════════════════\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"\nE2E FAILED: {e}\n", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPError as e:
        print(f"\nE2E HTTP ERROR: {e}\n", file=sys.stderr)
        raise SystemExit(1)
