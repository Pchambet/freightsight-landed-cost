"""GET /search: one box over containers, orders, articles, invoices, suppliers and shipments."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.search.service import fold, squeeze


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    assert client.post("/api/v1/organization/sample-data").status_code == 201
    return client


def hits(client: TestClient, q: str, **params: object) -> list[dict[str, Any]]:
    res = client.get("/api/v1/search", params={"q": q, **params})
    assert res.status_code == 200, res.text
    results: list[dict[str, Any]] = res.json()["results"]
    return results


def test_a_reference_is_compared_without_its_punctuation_and_a_name_without_its_accents() -> None:
    assert squeeze("MSCU 482199-0") == "mscu4821990"
    assert squeeze("po 2026/014") == "po2026014"
    assert fold("Zhèjiang KAIYUAN") == "zhejiang kaiyuan"


def test_a_container_is_found_the_way_it_is_typed_from_memory(seeded: TestClient) -> None:
    first = hits(seeded, "mscu 482")[0]
    assert (first["kind"], first["label"], first["sublabel"]) == ("container", "MSCU4821990", "MEDUSH2604417")
    assert first["id"] is not None


def test_an_order_comes_with_its_supplier(seeded: TestClient) -> None:
    first = hits(seeded, "po2026 014")[0]
    assert (first["kind"], first["label"]) == ("purchase_order", "PO-2026-014")
    assert first["sublabel"] == "Zhejiang Kaiyuan Tyre Co."


def test_an_article_is_found_by_what_it_is_and_has_no_id(seeded: TestClient) -> None:
    (mat,) = [h for h in hits(seeded, "TAPIS") if h["kind"] == "sku"]
    assert (mat["label"], mat["sku"], mat["id"]) == ("MAT-EVA-6040-GY", "MAT-EVA-6040-GY", None)
    assert "Tapis" in str(mat["sublabel"])


def test_accents_do_not_matter_either_way(seeded: TestClient) -> None:
    assert [h["label"] for h in hits(seeded, "zhèjiang")] == ["Zhejiang Kaiyuan Tyre Co."]


def test_the_beginning_of_a_reference_comes_before_a_match_inside_one(seeded: TestClient) -> None:
    # "2026" begins no order number, and sits inside both: they come in their own order
    assert [h["label"] for h in hits(seeded, "2026") if h["kind"] == "purchase_order"] == [
        "PO-2026-014",
        "PO-2026-015",
    ]
    # "tyr" begins the SKU; the order of the same supplier only has it nowhere
    assert hits(seeded, "tyr")[0]["label"] == "TYR-20555R16-91V"
    shipment_first = hits(seeded, "medush2604417")
    assert shipment_first[0]["kind"] == "shipment"


def test_fewer_than_two_characters_is_not_a_question_yet(seeded: TestClient) -> None:
    assert hits(seeded, "m") == []
    assert hits(seeded, "  ") == []
    assert hits(seeded, "") == []


def test_the_limit_is_respected(seeded: TestClient) -> None:
    assert len(hits(seeded, "20", limit=2)) == 2


def test_another_organization_finds_nothing_of_ours(seeded: TestClient) -> None:
    res = seeded.get("/api/v1/search", params={"q": "mscu"}, headers={"X-Org-Id": str(uuid.uuid4())})
    assert res.status_code == 200
    assert res.json()["results"] == []
