"""The audit of a period: every rule on a case built by hand, in both directions — it fires, and it
stays silent just under its threshold — because a finding in a document sold for 990 € must be a fact.
Then the six-month dataset, whose flaws were put there on purpose, and the audit sent by link."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.money import q2
from app.domain.models import Cost
from app.domain.reporting.audit import PUBLIC_PARAMS
from app.domain.sample_history import DUPLICATE_THC_ON, NEVER_INVOICED_ON, OUTLIER_DRAYAGE_ON, VOYAGES

PERIOD = {"period_from": "2026-03-01", "period_to": "2026-03-31"}
LINE = {"line_no": 1, "sku": "A", "quantity": "100", "unit_price": "10"}
#: Five boxes over a year, the last one inside PERIOD: the comparable history of a route.
YEAR = ("2025-06-10", "2025-09-10", "2025-12-10", "2026-02-10", "2026-03-10")
PUBLIC = "/api/v1/public/shared-reports/open"
NOBODY = {"X-Org-Id": ""}


def landed(
    client: TestClient, number: str, day: str, quantity: str = "100", iso_type: str | None = "42G1"
) -> str:
    """A container discharged on `day`, carrying `quantity` units of a fresh order: a 40' unless said
    otherwise, since a box of unknown length is compared with nothing."""
    po = client.post(
        "/api/v1/purchase-orders",
        json={"po_number": f"PO-{number}", "currency": "EUR", "lines": [{**LINE, "quantity": quantity}]},
    ).json()
    box = client.post("/api/v1/containers", json={"container_number": number, "iso_type": iso_type}).json()
    res = client.patch(
        f"/api/v1/containers/{box['id']}",
        json={"discharged_at": f"{day}T08:00:00Z", "milestone": "DISCHARGED"},
    )
    assert res.status_code == 200, res.text
    loads = [{"po_line_id": po["lines"][0]["id"], "quantity": quantity}]
    assert client.put(f"/api/v1/containers/{box['id']}/loads", json=loads).status_code == 200
    return str(box["id"])


def route(client: TestClient, boxes: list[str]) -> None:
    """Put boxes on one voyage from Ningbo to Le Havre, which is what makes them comparable."""
    voyage = client.post(
        "/api/v1/shipments",
        json={"reference": "BL-1", "origin_unlocode": "CNNGB", "destination_unlocode": "FRLEH"},
    ).json()
    for box in boxes:
        assert (
            client.patch(f"/api/v1/containers/{box}", json={"shipment_id": voyage["id"]}).status_code == 200
        )


def cost(client: TestClient, box: str, cost_type: str, amount: str, **extra: Any) -> dict[str, Any]:
    body = {"scope": "CONTAINER", "target_id": box, "cost_type": cost_type, "amount": amount,
            "currency": "EUR", "cost_date": "2026-03-15", **extra}  # fmt: skip
    res = client.post("/api/v1/costs", json=body)
    assert res.status_code == 201, res.text
    made: dict[str, Any] = res.json()
    return made


def supersede(client: TestClient, invoice: dict[str, Any], estimate: dict[str, Any]) -> None:
    res = client.patch(f"/api/v1/costs/{invoice['id']}", json={"supersedes_cost_id": estimate["id"]})
    assert res.status_code == 200, res.text


def audit(client: TestClient, **params: str) -> dict[str, Any]:
    res = client.get("/api/v1/reports/audit", params={**PERIOD, **params})
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def findings(client: TestClient, code: str) -> list[dict[str, Any]]:
    return [f for f in audit(client)["findings"] if f["code"] == code]


def days_ago(n: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=n)).isoformat()


# ------------------------------------------------------------------------------- charges billed twice


def test_one_charge_billed_three_times_is_two_duplicates_and_bank_fees_are_never_one(
    client: TestClient,
) -> None:
    box = landed(client, "MSCU4821990", "2026-03-10")
    for n in range(3):
        cost(client, box, "THC", "260.00", vendor="TERMINAL PORTUAIRE", invoice_number=f"TH-{n}")
    found = findings(client, "DUPLICATE_CHARGE")
    assert [(f["confidence"], f["amount"], f["params"]["first_invoice"]) for f in found] == [
        ("sure", "260.00", "TH-0"),
        ("sure", "260.00", "TH-0"),
    ]
    assert all(f["recoverable"] and f["params"]["same_vendor"] == "true" for f in found)
    assert audit(client)["headline"]["recoverable_sure"] == "520.00"  # two too many, not three pairs

    fees = landed(client, "TGHU7245081", "2026-03-12")
    for n in range(2):  # every payment carries its own bank fees
        cost(client, fees, "BANK_FEES", "15.00", vendor="BANQUE", invoice_number=f"B-{n}")
    assert len(findings(client, "DUPLICATE_CHARGE")) == 2


def test_two_providers_a_little_apart_is_a_question_and_identical_amounts_a_fact(client: TestClient) -> None:
    near = landed(client, "MSCU4821990", "2026-03-10")
    cost(client, near, "THC", "262.00", vendor="TRANSDEMO SAS", invoice_number="FA-1")
    cost(client, near, "THC", "260.00", vendor="TERMINAL PORTUAIRE", invoice_number="TH-9")
    rebilled = landed(client, "TGHU7245081", "2026-03-12")  # re-billed at cost: the very same amount
    cost(client, rebilled, "THC", "255.00", vendor="TRANSDEMO SAS", invoice_number="FA-2")
    cost(client, rebilled, "THC", "255.00", vendor="TERMINAL PORTUAIRE", invoice_number="TH-10")
    apart = landed(client, "TCLU1234565", "2026-03-14")  # 3 % apart: two different charges
    cost(client, apart, "THC", "300.00", vendor="A", invoice_number="A-1")
    cost(client, apart, "THC", "290.00", vendor="B", invoice_number="B-1")

    found = {f["container_number"]: f for f in findings(client, "DUPLICATE_CHARGE")}
    assert {n: f["confidence"] for n, f in found.items()} == {
        "MSCU4821990": "to_check",
        "TGHU7245081": "sure",
    }
    assert (found["MSCU4821990"]["amount"], found["MSCU4821990"]["params"]["same_vendor"]) == (
        "260.00",
        "false",
    )
    head = audit(client)["headline"]
    assert (head["recoverable_sure"], head["recoverable_to_check"]) == ("255.00", "260.00")


def test_a_charge_twice_without_invoice_numbers_is_a_question(client: TestClient) -> None:
    """Two THC of 260 € from one terminal, no number on either: billed twice, or one bill typed twice?
    Nothing says which, so it is asked, never claimed as a fact."""
    box = landed(client, "MSCU4821990", "2026-03-10")
    cost(client, box, "THC", "260.00", vendor="TERMINAL PORTUAIRE")
    cost(client, box, "THC", "260.00", vendor="TERMINAL PORTUAIRE")
    (found,) = findings(client, "DUPLICATE_CHARGE")
    assert (found["confidence"], found["amount"]) == ("to_check", "260.00")


def test_one_invoice_recorded_twice_is_a_double_entry_of_ours_and_nothing_to_claim(
    client: TestClient, db: Session
) -> None:
    """Two spellings of one invoice, kept from before the entry refused them: not the forwarder billing
    twice but the company's own books counting once too often. Said so, claimed from nobody — and
    certain only when both copies name the forwarder, whose numbers are its own."""
    box = landed(client, "MSCU4821990", "2026-03-10")
    cost(client, box, "THC", "260.00", vendor="TRANSDEMO", invoice_number="F-0412")
    twin = cost(client, box, "THC", "260.00", vendor="TRANSDEMO", invoice_number="F-0413")
    other = landed(client, "TGHU7245081", "2026-03-12")
    cost(client, other, "DRAYAGE", "425.00", vendor="TRANSDEMO", invoice_number="F-0500")
    unnamed = cost(client, other, "DRAYAGE", "425.00", invoice_number="F-0501")
    # What the entry now refuses, written the way older rows were: straight into the table.
    for made, vendor, number in ((twin, "Transdemo SAS", "F0412"), (unnamed, None, "f 0500")):
        row = db.get(Cost, uuid.UUID(made["id"]))
        assert row is not None
        row.vendor, row.invoice_number = vendor, number
    db.flush()

    found = {f["container_number"]: f for f in findings(client, "DOUBLE_ENTRY")}
    assert {n: (f["confidence"], f["recoverable"], f["amount"]) for n, f in found.items()} == {
        "MSCU4821990": ("sure", False, "260.00"),
        "TGHU7245081": ("to_check", False, "425.00"),
    }
    assert found["MSCU4821990"]["params"]["first_invoice"] == "F-0412"
    assert findings(client, "DUPLICATE_CHARGE") == []
    assert audit(client)["headline"]["recoverable_sure"] == "0.00"


def test_one_number_in_two_currencies_on_one_box_is_a_question_not_a_fact(client: TestClient) -> None:
    """The freight in dollars and a surcharge in euros under one number, both keyed in by hand: two
    lines of one invoice, or one line keyed twice — nothing on file says which."""
    box = landed(client, "MSCU4821990", "2026-03-10")
    cost(client, box, "OCEAN_FREIGHT", "3000.00", currency="USD", vendor="TRANSDEMO", invoice_number="F-0412")
    cost(client, box, "OCEAN_FREIGHT", "40.00", vendor="TRANSDEMO", invoice_number="F-0412")
    (found,) = findings(client, "DOUBLE_ENTRY")
    assert (found["confidence"], found["recoverable"]) == ("to_check", False)


# ------------------------------------------------------------------------------- above the estimate


def test_an_invoice_above_its_estimate_is_a_fact_from_five_per_cent_and_fifty_euros(
    client: TestClient,
) -> None:
    box = landed(client, "MSCU4821990", "2026-03-10")
    supersede(
        client,
        cost(client, box, "DRAYAGE", "520.00", vendor="T", invoice_number="D-1"),
        cost(client, box, "DRAYAGE", "450.00", status="ESTIMATE"),
    )
    (found,) = findings(client, "ABOVE_QUOTE")
    assert (found["confidence"], found["amount"], found["recoverable"]) == ("sure", "70.00", True)
    assert found["params"] == {
        "quoted": "450.00",
        "invoiced": "520.00",
        "currency": "EUR",
        "share_pct": "15.6",
    }

    small = landed(client, "TGHU7245081", "2026-03-12")  # 4 %: a surcharge, not a claim
    supersede(
        client,
        cost(client, small, "OCEAN_FREIGHT", "2080.00", vendor="T", invoice_number="F-1"),
        cost(client, small, "OCEAN_FREIGHT", "2000.00", status="ESTIMATE"),
    )
    cheap = landed(client, "TCLU1234565", "2026-03-14")  # 20 % of 200: forty euros are not worth a letter
    supersede(
        client,
        cost(client, cheap, "BL_FEE", "240.00", vendor="T", invoice_number="BL-1"),
        cost(client, cheap, "BL_FEE", "200.00", status="ESTIMATE"),
    )
    assert len(findings(client, "ABOVE_QUOTE")) == 1


def test_the_exchange_rate_is_never_presented_as_a_price(client: TestClient) -> None:
    """3 150 USD quoted at booking and 3 300 USD invoiced a month later is 4.8 %: under the threshold,
    even though the dollar rose and the difference in euros is 6.6 %."""
    risen = landed(client, "MSCU4821990", "2026-03-10")
    supersede(
        client,
        cost(client, risen, "OCEAN_FREIGHT", "3300.00", currency="USD", fx_rate="0.8650", vendor="T",
             invoice_number="F-1"),
        cost(client, risen, "OCEAN_FREIGHT", "3150.00", currency="USD", fx_rate="0.8500", status="ESTIMATE"),
    )  # fmt: skip
    assert findings(client, "ABOVE_QUOTE") == []

    fallen = landed(client, "TGHU7245081", "2026-03-12")
    supersede(
        client,
        cost(client, fallen, "OCEAN_FREIGHT", "3710.00", currency="USD", fx_rate="0.8500", vendor="T",
             invoice_number="F-2"),
        cost(client, fallen, "OCEAN_FREIGHT", "3400.00", currency="USD", fx_rate="0.8700", status="ESTIMATE"),
    )  # fmt: skip
    (found,) = findings(client, "ABOVE_QUOTE")
    # 310 dollars above, at the invoice's rate; the dollar's fall took 68 euros off the difference
    assert (found["confidence"], found["amount"]) == ("sure", "263.50")
    assert found["params"] == {
        "quoted": "3400.00", "invoiced": "3710.00", "currency": "USD", "share_pct": "9.1",
        "fx_effect": "-68.00",
    }  # fmt: skip

    mixed = landed(client, "TCLU1234565", "2026-03-14")  # estimated in euros, invoiced in dollars
    supersede(
        client,
        cost(
            client,
            mixed,
            "DRAYAGE",
            "600.00",
            currency="USD",
            fx_rate="0.9000",
            vendor="T",
            invoice_number="D-1",
        ),
        cost(client, mixed, "DRAYAGE", "450.00", status="ESTIMATE"),
    )
    by_box = {f["container_number"]: f for f in findings(client, "ABOVE_QUOTE")}
    assert by_box["TCLU1234565"]["confidence"] == "to_check"  # the rate and the price cannot be told apart
    assert by_box["TCLU1234565"]["params"] == {
        "quoted": "450.00", "invoiced": "540.00", "currency": "EUR", "share_pct": "20.0"
    }  # fmt: skip


# ------------------------------------------------------------------------------- far above the route


def test_a_charge_far_above_its_route_is_a_question_and_only_with_enough_peers(client: TestClient) -> None:
    boxes = [landed(client, f"MSCU48219{n:02d}", day) for n, day in enumerate(YEAR)]
    route(client, boxes)
    cost(client, boxes[4], "DRAYAGE", "1450.00", vendor="T", invoice_number="D-LAST")
    for box in boxes[:3]:
        cost(client, box, "DRAYAGE", "450.00", vendor="T", invoice_number=f"D-{box[:4]}")
    assert findings(client, "OUTLIER_CHARGE") == []  # a median of three is not a norm

    cost(client, boxes[3], "DRAYAGE", "450.00", vendor="T", invoice_number="D-4TH")
    (found,) = findings(client, "OUTLIER_CHARGE")
    assert (found["confidence"], found["amount"], found["basis"]) == ("to_check", "1000.00", 4)
    assert found["params"] == {
        "amount": "1450.00", "median": "450.00", "ratio": "3.2", "route": "CNNGB-FRLEH", "size": "40"
    }  # fmt: skip
    headline = audit(client)["headline"]
    assert (headline["recoverable_sure"], headline["recoverable_to_check"]) == ("0.00", "1000.00")


def test_boxes_are_compared_with_boxes_of_their_size_and_freight_with_its_own_season(
    client: TestClient,
) -> None:
    boxes = [
        landed(client, f"MSCU48219{n:02d}", day, iso_type="22G1" if n < 4 else "45G1")
        for n, day in enumerate(YEAR)
    ]
    route(client, boxes)
    for box in boxes[:4]:
        cost(client, box, "DRAYAGE", "450.00", vendor="T", invoice_number=f"D-{box[:6]}")
        cost(client, box, "OCEAN_FREIGHT", "1000.00", vendor="T", invoice_number=f"F-{box[:6]}")
    cost(client, boxes[4], "DRAYAGE", "900.00", vendor="T", invoice_number="D-LAST")
    assert findings(client, "OUTLIER_CHARGE") == []  # a 40' is not a 20' that costs twice as much

    # The same last box as a 20', with a freight at two and a half times its year: the haulage stands
    # out; the freight is compared with its own season, where there is one box a month earlier.
    assert client.patch(f"/api/v1/containers/{boxes[4]}", json={"iso_type": "22G1"}).status_code == 200
    cost(client, boxes[4], "OCEAN_FREIGHT", "2500.00", vendor="T", invoice_number="F-LAST")
    assert [(f["cost_type"], f["params"]["size"]) for f in findings(client, "OUTLIER_CHARGE")] == [
        ("DRAYAGE", "20")
    ]


def test_duty_demurrage_and_catch_alls_are_never_compared_and_one_euro_is_one_finding(
    client: TestClient,
) -> None:
    """A box of engines pays more duty than a box of tyres on the same voyage, and a box that waited
    pays demurrage the others did not: neither is a question about a provider. And a handling billed
    twice is a duplicate, not a duplicate plus a charge "far above the route"."""
    boxes = [landed(client, f"MSCU48219{n:02d}", day) for n, day in enumerate(YEAR)]
    route(client, boxes)
    for n, box in enumerate(boxes):
        ten = "0" if n == 4 else ""
        cost(client, box, "CUSTOMS_DUTY", f"300{ten}.00", vendor="T", invoice_number=f"C-{n}")
        cost(client, box, "DEMURRAGE", f"85{ten}.00", vendor="T", invoice_number=f"S-{n}")
        cost(client, box, "OTHER", f"90{ten}.00", vendor="T", invoice_number=f"O-{n}")
        cost(client, box, "THC", "260.00", vendor="T", invoice_number=f"T-{n}")
    cost(client, boxes[4], "THC", "260.00", vendor="TERMINAL", invoice_number="TH-9")
    assert [f["code"] for f in audit(client)["findings"]] == ["DUPLICATE_CHARGE"]


def test_a_box_of_unknown_route_or_length_is_compared_with_nothing(client: TestClient) -> None:
    """The middle of "unknown" is not a norm: without a route or a length, no box is far above anything."""
    boxes = [landed(client, f"MSCU48219{n:02d}", day, iso_type=None) for n, day in enumerate(YEAR)]
    route(client, boxes)
    for n, box in enumerate(boxes):
        cost(client, box, "DRAYAGE", "1450.00" if n == 4 else "450.00", vendor="T", invoice_number=f"D-{n}")
    assert findings(client, "OUTLIER_CHARGE") == []
    sized = [landed(client, f"TGHU72450{n:02d}", day) for n, day in enumerate(YEAR)]  # no voyage: no route
    for n, box in enumerate(sized):
        cost(client, box, "DRAYAGE", "1450.00" if n == 4 else "450.00", vendor="T", invoice_number=f"E-{n}")
    assert findings(client, "OUTLIER_CHARGE") == []


def test_charges_billed_at_nothing_are_no_norm(client: TestClient) -> None:
    boxes = [landed(client, f"MSCU48219{n:02d}", day) for n, day in enumerate(YEAR)]
    route(client, boxes)
    for n, box in enumerate(boxes):
        cost(client, box, "INSPECTION", "450.00" if n == 4 else "0.00", vendor="T", invoice_number=f"I-{n}")
    assert findings(client, "OUTLIER_CHARGE") == []  # and no error: nothing is "times" zero


# ------------------------------------------------------------------------------- the quality of the figures


def test_an_estimate_still_open_two_months_after_landing_is_judged_on_the_day_of_the_audit(
    client: TestClient,
) -> None:
    old = landed(client, "MSCU4821990", days_ago(70))
    cost(client, old, "CUSTOMS_BROKERAGE", "120.00", status="ESTIMATE", cost_date=days_ago(70))
    fresh = landed(client, "TGHU7245081", days_ago(20))
    cost(client, fresh, "CUSTOMS_BROKERAGE", "120.00", status="ESTIMATE", cost_date=days_ago(20))

    # A period that ended ten days after the old box landed: its estimate is still open *today*.
    closed = audit(client, period_from=days_ago(100), period_to=days_ago(60))
    stale = [f for f in closed["findings"] if f["code"] == "ESTIMATE_NEVER_INVOICED"]
    assert [(f["container_number"], f["amount"], f["params"]["days"]) for f in stale] == [
        ("MSCU4821990", "120.00", "70")
    ]
    assert closed["as_of"] == days_ago(0) and not stale[0]["recoverable"]
    # Twenty days after landing is only a forwarder that has not invoiced yet.
    recent = audit(client, period_from=days_ago(30), period_to=days_ago(0))
    assert [f for f in recent["findings"] if f["code"] == "ESTIMATE_NEVER_INVOICED"] == []


def test_what_the_engine_could_not_place_and_a_duty_without_a_rate_name_their_lines(
    client: TestClient,
) -> None:
    po = client.post(
        "/api/v1/purchase-orders",
        json={"po_number": "PO-X", "currency": "EUR",
              "lines": [{"line_no": 1, "sku": "A", "quantity": "10", "unit_price": "10", "duty_rate": "0.05"},
                        {"line_no": 2, "sku": "B", "quantity": "10", "unit_price": "10"}]},
    ).json()  # fmt: skip
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"}).json()
    client.patch(f"/api/v1/containers/{box['id']}", json={"discharged_at": "2026-03-10T08:00:00Z"})
    loads = [{"po_line_id": ln["id"], "quantity": "10"} for ln in po["lines"]]
    client.put(f"/api/v1/containers/{box['id']}/loads", json=loads)
    cost(client, box["id"], "CUSTOMS_DUTY", "50.00", allocation_method="BY_CIF_VALUE")
    cost(client, box["id"], "OCEAN_FREIGHT", "100.00", allocation_method="BY_WEIGHT")  # no weights anywhere
    codes = {f["code"]: f for f in audit(client)["findings"]}
    assert codes["DUTY_RATE_MISSING"]["params"] == {"lines": "1", "skus": "B", "po_numbers": "PO-X"}
    unplaced = codes["UNALLOCATED_COST"]
    assert (unplaced["amount"], unplaced["params"]["reason"]) == ("100.00", "MISSING_BASIS")
    assert (unplaced["params"]["skus"], unplaced["params"]["po_numbers"]) == ("A, B", "PO-X")
    assert not unplaced["recoverable"] and not codes["DUTY_RATE_MISSING"]["recoverable"]


# ------------------------------------------------------------------------------- the whole document


def test_the_first_page_is_the_sum_of_its_sections_and_the_sections_are_the_reports(
    client: TestClient,
) -> None:
    client.post("/api/v1/organization/sample-data", params={"profile": "history"})
    client.patch("/api/v1/organization", json={"settings": {"assumed_coefficient": "1.10"}})
    period = {"period_from": days_ago(90), "period_to": days_ago(0)}
    body = audit(client, **period)

    head, margins = body["headline"], body["margins"]
    priced = [m for m in margins if m["sale_price"] is not None]
    assert head["priced_skus"] == len(priced) > 0 and head["unpriced_skus"] == len(margins) - len(priced)
    assert Decimal(head["margin_overstated"]) == sum(Decimal(m["approach_costs"]) for m in priced)
    # the gap with the company's own coefficient needs no selling price: every article counts
    assert Decimal(head["gap_vs_assumed"]) == sum(Decimal(m["gap_vs_assumed"]) for m in margins)
    for m in margins:
        fob, landed_value = Decimal(m["fob"]), Decimal(m["landed"])
        assert Decimal(m["approach_costs"]) == landed_value - fob
        assert Decimal(m["gap_vs_assumed"]) == q2(landed_value - fob * Decimal("1.10"))
    for m in priced:
        assert Decimal(m["points_lost"]) == Decimal(m["margin_on_fob_pct"]) - Decimal(m["margin_real_pct"])
    claims = [f for f in body["findings"] if f["recoverable"]]
    assert {f["code"] for f in claims} <= {"DUPLICATE_CHARGE", "ABOVE_QUOTE", "OUTLIER_CHARGE"}
    for confidence in ("sure", "to_check"):
        assert Decimal(head[f"recoverable_{confidence}"]) == sum(
            Decimal(f["amount"]) for f in claims if f["confidence"] == confidence
        )
    assert Decimal(head["demurrage_paid"]) == sum(Decimal(d["paid"]) for d in body["demurrage"])

    assert body["assumed_coefficient"] == "1.1000" and Decimal(body["coefficient"]) > 1
    monthly = client.get("/api/v1/reports/landed-cost", params={"group_by": "month", **period}).json()
    assert [(b["key"], b["landed"]) for b in body["coefficient_by_month"]] == [
        (b["key"], b["landed"]) for b in monthly["buckets"]
    ]
    dnd = client.get("/api/v1/reports/dnd", params={**period, "basis": "arrival"}).json()
    assert [
        (d["container_number"], d["paid"], d["rule_code"], d["days_over"]) for d in body["demurrage"]
    ] == [(ln["container_number"], ln["paid"], ln["rule_code"], ln["days_over"]) for ln in dnd["lines"]]
    assert body["completeness"]["containers"] > 0 and "ecb" in body["completeness"]["fx_sources"]
    assert body["rules"]["outlier_min_basis"] == 4 and "DEMURRAGE" in body["rules"]["not_compared"]
    assert body["as_of"] == days_ago(0)


def test_the_audit_says_which_boxes_it_could_not_compare_or_date_and_by_which_rules_it_went(
    client: TestClient,
) -> None:
    """What the document could not look at is part of what it says, and so are its rules."""
    routed = landed(client, "MSCU4821990", "2026-03-10")
    route(client, [routed])
    landed(client, "TGHU7245081", "2026-03-12", iso_type=None)  # no length, no route: not compared
    po = client.post(
        "/api/v1/purchase-orders", json={"po_number": "PO-UNDATED", "currency": "EUR", "lines": [LINE]}
    ).json()
    loaded = client.post("/api/v1/containers", json={"container_number": "TCLU1234565"}).json()
    loads = [{"po_line_id": po["lines"][0]["id"], "quantity": "100"}]
    assert client.put(f"/api/v1/containers/{loaded['id']}/loads", json=loads).status_code == 200
    charged = client.post("/api/v1/containers", json={"container_number": "CSQU3054383"}).json()
    cost(client, charged["id"], "THC", "100.00")

    body = audit(client)
    completeness = body["completeness"]
    assert (completeness["containers_not_compared"], completeness["containers_without_date"]) == (1, 2)
    assert body["rules"]["duty_explained_pct"] == "20.00"
    assert all(
        body["rules"][flag] is True
        for flag in ("demurrage_by_arrival", "outlier_needs_route_and_size", "same_invoice_never_duplicate")
    )


def test_demurrage_days_are_the_port_s_days_whatever_the_hour_in_utc(client: TestClient) -> None:
    """Unloaded at 00:30 in Le Havre on 10 March, collected at 00:30 on 19 March: seven free days end on
    the 16th, and the box was three days over — not two, as it would be counting in UTC, where both
    instants fall on the evening before."""
    box = landed(client, "MSCU4821990", "2026-03-10")
    route(client, [box])
    patch = {"discharged_at": "2026-03-09T23:30:00Z", "gate_out_at": "2026-03-18T23:30:00Z",
             "empty_returned_at": "2026-03-20T10:00:00Z", "free_days_demurrage": 7}  # fmt: skip
    assert client.patch(f"/api/v1/containers/{box}", json=patch).status_code == 200
    card = {"cost_type": "DEMURRAGE", "amount": "85.00", "currency": "EUR"}
    assert client.post("/api/v1/organization/rate-cards", json=card).status_code == 201
    cost(client, box, "DEMURRAGE", "255.00", vendor="TERMINAL", invoice_number="SUR-1")
    (line,) = [d for d in audit(client)["demurrage"] if d["paid"] == "255.00"]
    assert line["days_over"] == 3


def test_detention_invoiced_after_the_quarter_belongs_to_the_quarter_the_box_arrived_in(
    client: TestClient,
) -> None:
    """Detention is billed once the empty is back, often weeks into the next quarter. The audit counts
    what the boxes that arrived in the period cost; the screen's report stays on invoice dates."""
    box = landed(client, "MSCU4821990", "2026-03-10")
    cost(client, box, "DETENTION", "340.00", vendor="MSC", invoice_number="DET-1", cost_date="2026-04-22")
    march = {"period_from": "2026-03-01", "period_to": "2026-03-31"}
    body = audit(client, **march)
    assert body["headline"]["demurrage_paid"] == "340.00"
    assert [(d["container_number"], d["paid"]) for d in body["demurrage"]] == [("MSCU4821990", "340.00")]
    by_invoice = client.get("/api/v1/reports/dnd", params=march).json()
    by_arrival = client.get("/api/v1/reports/dnd", params={**march, "basis": "arrival"}).json()
    assert (by_invoice["paid"], by_arrival["paid"]) == ("0.00", "340.00")
    april = audit(client, period_from="2026-04-01", period_to="2026-04-30")
    assert april["headline"]["demurrage_paid"] == "0.00"  # counted once, with its box


def test_the_six_month_dataset_carries_the_three_flaws_that_were_put_in_it_and_nothing_untrue(
    client: TestClient,
) -> None:
    client.post("/api/v1/organization/sample-data", params={"profile": "history"})
    body = audit(client, period_from=days_ago(200), period_to=days_ago(0))
    by_code: dict[str, list[dict[str, Any]]] = {}
    for f in body["findings"]:
        by_code.setdefault(f["code"], []).append(f)
    assert set(by_code) == {
        "DUPLICATE_CHARGE", "OUTLIER_CHARGE", "ESTIMATE_NEVER_INVOICED", "DUTY_RATE_MISSING", "ABOVE_QUOTE"
    }  # fmt: skip

    (duplicate,) = by_code["DUPLICATE_CHARGE"]
    assert (duplicate["cost_type"], duplicate["confidence"], duplicate["params"]["same_vendor"]) == (
        "THC",
        "sure",
        "false",
    )
    assert duplicate["amount"] == duplicate["params"]["first_amount"]  # re-billed at cost: the same amount
    (outlier,) = by_code["OUTLIER_CHARGE"]
    assert (outlier["cost_type"], outlier["confidence"], outlier["params"]["size"]) == (
        "DRAYAGE",
        "to_check",
        "40",
    )
    assert outlier["vendor"] == "TRANSPORTS LEMAIRE"  # the largest of the invoices behind the sum
    assert outlier["basis"] >= 4 and Decimal(outlier["params"]["ratio"]) >= 3
    (forgotten,) = by_code["ESTIMATE_NEVER_INVOICED"]
    assert forgotten["cost_type"] == "CUSTOMS_BROKERAGE" and int(forgotten["params"]["days"]) >= 60
    (no_rate,) = by_code["DUTY_RATE_MISSING"]
    assert no_rate["params"]["skus"] == "CHAIN-SNOW-9MM" and no_rate["params"]["po_numbers"].startswith(
        "PO-2026-"
    )
    assert VOYAGES[DUPLICATE_THC_ON].fate == "closed" and VOYAGES[OUTLIER_DRAYAGE_ON].fate == "closed"
    assert VOYAGES[NEVER_INVOICED_ON].arrived_days_ago >= 60

    # Every invoice above its estimate is measured in the currency both were written in: the amount is
    # the price difference at the invoice's own rate, never what the exchange rate did.
    for above in by_code["ABOVE_QUOTE"]:
        actual = client.get(f"/api/v1/costs/{above['cost_ids'][0]}").json()
        estimate = client.get(f"/api/v1/costs/{actual['supersedes_cost_id']}").json()
        assert actual["currency"] == estimate["currency"] == above["params"]["currency"]
        difference = Decimal(actual["amount"]) - Decimal(estimate["amount"])
        assert Decimal(above["amount"]) == q2(difference * Decimal(actual["fx_rate"]))
        assert difference / Decimal(estimate["amount"]) >= Decimal("0.05") and above["confidence"] == "sure"
    surprised = [c for c in client.get("/api/v1/costs").json() if c["supersedes_cost_id"]]
    assert 0 < len(by_code["ABOVE_QUOTE"]) < len(surprised)  # the small ones are left alone

    # No coefficient given: nothing is measured against one.
    assert body["headline"]["gap_vs_assumed"] is None and all(
        m["gap_vs_assumed"] is None for m in body["margins"]
    )
    # The catalogue prices six articles of eight.
    assert (body["headline"]["priced_skus"], body["headline"]["unpriced_skus"]) == (6, 2)
    # The demurrage the dataset paid: 85 euros a day for each day past the last free day.
    paid = [d for d in body["demurrage"] if Decimal(d["paid"]) > 0]
    assert paid and all(Decimal(d["paid"]) == Decimal("85.00") * d["days_over"] for d in paid)


# ------------------------------------------------------------------------------- sent by link


def test_an_audit_can_be_sent_by_link_and_stays_what_it_was(client: TestClient) -> None:
    client.post("/api/v1/organization/sample-data", params={"profile": "history"})
    period = {"period_from": days_ago(200), "period_to": days_ago(0)}
    created = client.post("/api/v1/reports/audit/share", json=period)
    assert created.status_code == 201, created.text
    before = audit(client, **period)
    assert created.json()["landed"] == before["completeness"]["landed"]

    box = client.get("/api/v1/containers").json()[0]["id"]
    cost(client, box, "INSPECTION", "145.00", cost_date=days_ago(0))
    opened = client.post(PUBLIC, json={"token": created.json()["token"]}, headers=NOBODY)
    assert opened.status_code == 200, opened.text
    public = opened.json()
    assert (public["subject_type"], public["report"], public["redacted"]) == ("audit", None, False)
    assert public["audit"]["headline"] == before["headline"]
    assert public["audit"]["completeness"]["landed"] != audit(client, **period)["completeness"]["landed"]
    assert public["audit"]["rules"] == before["rules"] and public["audit"]["coefficient_by_supplier"]
    (listed,) = client.get("/api/v1/reports/audit/shares").json()
    assert (listed["subject_type"], listed["view_count"]) == ("audit", 1)


def test_an_audit_sent_with_names_left_out_stores_none_of_them(client: TestClient, db: Session) -> None:
    """Left out means absent from the stored snapshot, not hidden by the page: every provider, every
    invoice number and every supplier of the dataset is looked for in the row itself."""
    client.post("/api/v1/organization/sample-data", params={"profile": "history"})
    period = {"period_from": days_ago(200), "period_to": days_ago(0)}
    created = client.post("/api/v1/reports/audit/share", json={**period, "redact_vendors": True})
    assert created.status_code == 201, created.text

    stored = db.execute(text("SELECT snapshot::text FROM shared_reports")).scalar_one()
    providers = set(db.execute(text("SELECT vendor FROM costs WHERE vendor IS NOT NULL")).scalars())
    numbers = set(
        db.execute(text("SELECT invoice_number FROM costs WHERE invoice_number IS NOT NULL")).scalars()
    )
    suppliers = set(db.execute(text("SELECT name FROM suppliers")).scalars())
    assert len(providers) >= 4 and numbers and len(suppliers) == 3
    opened = client.post(PUBLIC, json={"token": created.json()["token"]}, headers=NOBODY)
    for name in providers | numbers | suppliers:
        assert name not in stored and name not in opened.text, name

    public = opened.json()["audit"]
    assert opened.json()["redacted"] is True and public["coefficient_by_supplier"] == []
    assert public["findings"] and all(set(f["params"]) <= PUBLIC_PARAMS for f in public["findings"])
    # what the document is about is all there
    assert public["headline"] == audit(client, **period)["headline"] and public["margins"]


def test_the_findings_are_a_file_in_french(client: TestClient) -> None:
    box = landed(client, "MSCU4821990", "2026-03-10")
    cost(client, box, "THC", "262.00", vendor="TRANSDEMO SAS", invoice_number="FA-1")
    cost(client, box, "THC", "260.00", vendor="TERMINAL PORTUAIRE", invoice_number="TH-9")
    res = client.get("/api/v1/exports/audit-findings.csv", params=PERIOD)
    assert res.status_code == 200
    head, row = res.text.lstrip("﻿").splitlines()[:2]
    assert head == (
        "Constat;Certitude;Montant en devise de base;N° conteneur;Type de coût;Prestataire;N° facture;"
        "Conteneurs comparés;Détail"
    )
    assert row == (
        "Prestation facturée deux fois;À vérifier;260,00;MSCU4821990;Manutention portuaire (THC);"
        'TERMINAL PORTUAIRE;TH-9;;"Première facture : FA-1 ; Premier prestataire : TRANSDEMO SAS ; '
        'Premier montant : 262,00 ; Même prestataire : non"'
    )
    english = client.get("/api/v1/exports/audit-findings.csv", params={**PERIOD, "locale": "en"}).text
    assert "first_invoice=FA-1 ; first_vendor=TRANSDEMO SAS ; first_amount=262.00" in english


@pytest.mark.parametrize(
    "bad",
    [
        {"period_from": "2026-04-01", "period_to": "2026-03-01"},
        {"period_from": "0001-01-01", "period_to": "0001-03-31"},
        {"period_from": "2026-01-01", "period_to": "9999-12-31"},
    ],
)
def test_a_period_that_makes_no_sense_is_refused_on_every_route(
    client: TestClient, bad: dict[str, str]
) -> None:
    for res in (
        client.get("/api/v1/reports/audit", params=bad),
        client.post("/api/v1/reports/audit/share", json=bad),
        client.get("/api/v1/exports/audit-findings.csv", params=bad),
    ):
        assert (res.status_code, res.json()["code"]) == (422, "PERIOD_INVALID")


def test_the_assumed_coefficient_is_a_ratio_and_saving_it_keeps_the_other_settings(
    client: TestClient,
) -> None:
    def save(settings: Any) -> Any:
        return client.patch("/api/v1/organization", json={"settings": settings})

    assert save({"tracking_provider_default": "manual"}).status_code == 200
    for bad in (15, "NaN", "Infinity", True, "1,15"):
        res = save({"assumed_coefficient": bad})
        assert (res.status_code, res.json()["code"]) == (422, "ASSUMED_COEFFICIENT_INVALID"), bad
    kept = save({"assumed_coefficient": "1.15"}).json()["settings"]
    assert kept == {"tracking_provider_default": "manual", "assumed_coefficient": "1.15"}
    assert save({"assumed_coefficient": None}).json()["settings"] == {"tracking_provider_default": "manual"}
    assert save(None).json()["settings"] == {"tracking_provider_default": "manual"}
