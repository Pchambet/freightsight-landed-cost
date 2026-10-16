from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"

PO_LINES_CSV = (
    "﻿N° commande;Fournisseur;Réf;Qté;Prix unitaire;Poids;Volume;Conteneur;Qté conteneur\n"
    "PO-A;Ardent Tyres;A;1000;10,00;2;0,01;MSCU1234567;600\n"
    "PO-A;Ardent Tyres;A;1000;10,00;2;0,01;CMAU9876543;400\n"
    "PO-B;Volta Pneus;B;500;20,00;5;0,02;MSCU1234567;500\n"
    "PO-C;Kestrel Tyre Co.;C;10;abc;;;;\n"
).encode()


def upload(client: TestClient, content: bytes, kind: str, name: str = "file.csv") -> dict[str, Any]:
    res = client.post("/api/v1/imports", data={"kind": kind}, files={"file": (name, content, "text/csv")})
    assert res.status_code == 201, res.text
    return dict(res.json())


def test_legacy_import_validate_then_commit(client: TestClient) -> None:
    job = upload(client, (FIXTURES / "legacy_po_container.csv").read_bytes(), "LEGACY_PO_CONTAINER")
    assert job["status"] == "PARSED" and job["row_count"] == 3
    assert job["mapping"]["po_number"] == "po_number"

    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"]})
    assert res.status_code == 200, res.text
    report = res.json()["report"]
    assert res.json()["status"] == "VALIDATED"
    assert report["valid_rows"] == 3 and report["errors"] == []
    assert (
        report["purchase_orders_created"] == 3
        and report["containers_created"] == 2
        and report["loads_created"] == 3
    )
    assert [w["code"] for w in report["warnings"]] == ["DEPRECATED_COLUMN"]
    # dry run wrote nothing
    assert client.get("/api/v1/containers").json() == []

    res = client.post(f"/api/v1/imports/{job['id']}/commit", json={})
    assert res.status_code == 200 and res.json()["status"] == "DONE"
    containers = {c["container_number"]: c for c in client.get("/api/v1/containers").json()}
    assert set(containers) == {"MSCU1234567", "CMAU9876543"}
    assert containers["MSCU1234567"]["fob_base"] == "20900.50"

    # a second commit is refused; a re-upload of the same file is a no-op
    assert client.post(f"/api/v1/imports/{job['id']}/commit", json={}).status_code == 409
    job2 = upload(client, (FIXTURES / "legacy_po_container.csv").read_bytes(), "LEGACY_PO_CONTAINER")
    res = client.post(f"/api/v1/imports/{job2['id']}/commit", json={})
    assert res.json()["report"]["purchase_orders_created"] == 0
    assert res.json()["report"]["loads_created"] == 0


def test_po_lines_import_with_mapping_and_row_errors(client: TestClient) -> None:
    job = upload(client, PO_LINES_CSV, "PURCHASE_ORDERS", "export_excel.csv")
    assert job["encoding"] == "utf-8" and job["delimiter"] == ";"
    mapping = job["mapping"]
    assert mapping["po_number"] == "N° commande" and mapping["container_quantity"] == "Qté conteneur"

    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": mapping})
    report = res.json()["report"]
    assert report["valid_rows"] == 3
    assert [(e["row"], e["field"], e["code"]) for e in report["errors"]] == [
        (5, "unit_price", "NOT_A_NUMBER")
    ]
    assert (
        report["purchase_orders_created"] == 2
        and report["lines_created"] == 2
        and report["loads_created"] == 3
    )

    res = client.post(f"/api/v1/imports/{job['id']}/commit", json={"on_error": "abort"})
    assert res.status_code == 422 and res.json()["code"] == "IMPORT_ABORTED"
    res = client.post(f"/api/v1/imports/{job['id']}/commit", json={"on_error": "skip_rows"})
    assert res.status_code == 200, res.text

    pos = {p["po_number"]: p for p in client.get("/api/v1/purchase-orders").json()}
    assert set(pos) == {"PO-A", "PO-B"}
    assert pos["PO-A"]["container_numbers"] == ["CMAU9876543", "MSCU1234567"]
    assert pos["PO-A"]["supplier_name"] == "Ardent Tyres"

    # the mapping is remembered for the same header
    job3 = upload(client, PO_LINES_CSV, "PURCHASE_ORDERS")
    assert job3["mapping"] == mapping


def test_mapping_missing_required_field(client: TestClient) -> None:
    job = upload(client, b"xyz,qty,price\nPO-1,1,2\n", "PURCHASE_ORDERS")
    assert job["mapping"] == {"quantity": "qty", "unit_price": "price"}
    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"]})
    assert res.status_code == 422 and res.json()["code"] == "MAPPING_INCOMPLETE"
    res = client.post(
        f"/api/v1/imports/{job['id']}/validate", json={"mapping": {**job["mapping"], "po_number": "xyz"}}
    )
    assert res.status_code == 200 and res.json()["report"]["valid_rows"] == 1


def test_no_valid_rows_is_422(client: TestClient) -> None:
    job = upload(client, b"po_number,quantity,unit_price\n,1,2\n,3,4\n", "PURCHASE_ORDERS")
    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"]})
    assert res.status_code == 422 and res.json()["code"] == "NO_VALID_ROWS"
    assert len(res.json()["report"]["errors"]) == 2


def test_over_allocation_in_file_is_a_row_error(client: TestClient) -> None:
    content = (
        b"po_number,quantity,unit_price,container_number,container_quantity\n"
        b"PO-1,100,1,MSCU1234567,80\n"
        b"PO-1,100,1,CMAU9876543,30\n"
    )
    job = upload(client, content, "PURCHASE_ORDERS")
    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"]})
    report = res.json()["report"]
    assert report["valid_rows"] == 1
    assert report["errors"][0]["code"] == "OVER_ALLOCATED" and report["errors"][0]["row"] == 3


def test_a_reimport_without_a_column_keeps_what_was_typed_by_hand(client: TestClient) -> None:
    """Weights, volumes, HS codes and duty rates are enrichment: a file (or an ERP) that does not
    carry them must not blank them, or the freight split by weight silently collapses at the next
    sync. What the order itself says still follows the source."""
    first = b"po_number;sku;quantity;unit_price;unit_weight_kg;duty_rate\nPO-W;W;100;10,00;2,5;0,045\n"
    job = upload(client, first, "PURCHASE_ORDERS", "with_weights.csv")
    res = client.post(
        f"/api/v1/imports/{job['id']}/commit", json={"mapping": job["mapping"], "on_error": "abort"}
    )
    assert res.status_code == 200, res.text

    second = b"po_number;sku;quantity;unit_price\nPO-W;W;120;11,00\n"
    job2 = upload(client, second, "PURCHASE_ORDERS", "without_weights.csv")
    res = client.post(
        f"/api/v1/imports/{job2['id']}/commit", json={"mapping": job2["mapping"], "on_error": "abort"}
    )
    assert res.status_code == 200, res.text

    (po,) = [p for p in client.get("/api/v1/purchase-orders").json() if p["po_number"] == "PO-W"]
    (line,) = client.get(f"/api/v1/purchase-orders/{po['id']}").json()["lines"]
    assert line["quantity"].startswith("120") and line["unit_price"].startswith("11")
    assert line["unit_weight_kg"].startswith("2.5") and line["duty_rate"].startswith("0.045")

    # a value in the file still wins over the old one
    third = b"po_number;sku;quantity;unit_price;unit_weight_kg\nPO-W;W;120;11,00;3\n"
    job3 = upload(client, third, "PURCHASE_ORDERS", "new_weight.csv")
    assert (
        client.post(
            f"/api/v1/imports/{job3['id']}/commit", json={"mapping": job3["mapping"], "on_error": "abort"}
        ).status_code
        == 200
    )
    (line,) = client.get(f"/api/v1/purchase-orders/{po['id']}").json()["lines"]
    assert line["unit_weight_kg"].startswith("3")


def test_a_comma_after_a_lone_zero_is_a_decimal_point() -> None:
    """French files put three decimals on rates and volumes. "0,045" is four and a half percent,
    not forty-five; "0,012" is twelve litres, not twelve cubic metres. The genuinely ambiguous
    "12,500" stays the English twelve thousand five hundred, and two commas are thousands."""
    from decimal import Decimal

    from app.domain.imports.parsing import parse_decimal

    assert parse_decimal("0,045") == Decimal("0.045")
    assert parse_decimal("-0,125") == Decimal("-0.125")
    assert parse_decimal(",5") == Decimal("0.5")
    assert parse_decimal("12,500") == Decimal("12500")
    assert parse_decimal("1,234,567") == Decimal("1234567")
    assert parse_decimal("12 500,50") == Decimal("12500.50")
    assert parse_decimal("12.500,50") == Decimal("12500.50")
    assert parse_decimal("4,5 %") == Decimal("4.5")


IMPORTS_FIXTURES = FIXTURES / "imports"


def validate(client: TestClient, job: dict[str, Any], **mapping: str) -> dict[str, Any]:
    payload = {**job["mapping"], **mapping}
    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": payload})
    assert res.status_code == 200, res.text
    return dict(res.json()["report"])


def codes(issues: list[dict[str, Any]]) -> list[str]:
    return [i["code"] for i in issues]


def test_a_sage_export_is_read_whole_title_block_totals_row_and_french_decimals(
    client: TestClient,
) -> None:
    """The Sage 100 shape of the brief: a title and a date stamp above the header, a totals row
    below the last line, cp1252, and weights written "7,250". Each of those used to cost either
    the whole file (no po_number mapped, no header found) or a factor of a thousand on a weight."""
    job = upload(
        client, (IMPORTS_FIXTURES / "sage100_commandes.csv").read_bytes(), "PURCHASE_ORDERS", "sage.csv"
    )
    assert job["encoding"] == "cp1252" and job["mapping"]["po_number"] == "N° commande"

    report = validate(client, job)
    assert report["header_row"] == 3 and report["totals_row_ignored"] is True
    assert report["row_count"] == 3 and report["valid_rows"] == 3 and report["errors"] == []
    # the totals row is not counted as a line, and its "1 250" is not a quantity
    assert report["purchase_orders_created"] == 2 and report["lines_created"] == 3

    warnings = {w["code"]: w for w in report["warnings"]}
    assert warnings["HEADER_ROW_DETECTED"]["params"] == {"line": 3}
    assert "TOTALS_ROW_IGNORED" in warnings
    # « Poids (kg) » may be one tyre or the whole line: left for a person to say, and said so
    unmapped = [(w["field"], w["params"]) for w in report["warnings"] if w["code"] == "UNIT_COLUMN_UNMAPPED"]
    assert unmapped == [
        ("unit_weight_kg", {"column": "Poids (kg)"}),
        ("unit_volume_cbm", {"column": "Volume (m3)"}),
    ]

    # the person says per unit
    report = validate(client, job, unit_weight_kg="Poids (kg)", unit_volume_cbm="Volume (m3)")
    warnings = {w["code"]: w for w in report["warnings"]}
    assert warnings["DECIMAL_COMMA_ASSUMED"]["params"] == {
        "column": "Poids (kg)",
        "example": "7,250",
        "comma": "decimal",
    }
    assert warnings["UNIT_COLUMN_AMBIGUOUS"]["field"] in ("unit_weight_kg", "unit_volume_cbm")
    assert "UNIT_COLUMN_UNMAPPED" not in warnings

    res = client.post(f"/api/v1/imports/{job['id']}/commit", json={"on_error": "abort"})
    assert res.status_code == 200, res.text
    (po,) = [p for p in client.get("/api/v1/purchase-orders").json() if p["po_number"] == "CF-2026-0147"]
    lines = {ln["sku"]: ln for ln in client.get(f"/api/v1/purchase-orders/{po['id']}").json()["lines"]}
    assert lines["PNE-205-55"]["unit_weight_kg"] == "7.2500"  # not 7 250 kg
    assert lines["PNE-205-55"]["unit_price"] == "42.9000"
    assert lines["PNE-205-55"]["unit_volume_cbm"] == "0.0420"


def test_an_odoo_nested_export_keeps_the_lines_under_each_order(client: TestClient) -> None:
    """Odoo writes the order number once, on the first line of the order. Seven lines out of eight
    of a real export carry only a SKU, and every one of them was failing on REQUIRED."""
    job = upload(
        client, (IMPORTS_FIXTURES / "odoo_lignes_commande.csv").read_bytes(), "PURCHASE_ORDERS", "odoo.csv"
    )
    report = validate(client, job)
    assert report["valid_rows"] == 5 and report["errors"] == []
    assert report["forward_filled_rows"] == 3
    assert report["purchase_orders_created"] == 2 and report["lines_created"] == 5
    filled = next(w for w in report["warnings"] if w["code"] == "PO_NUMBER_FORWARD_FILLED")
    assert filled["params"] == {"rows": 3, "column": "Commande"}

    res = client.post(f"/api/v1/imports/{job['id']}/commit", json={"on_error": "abort"})
    assert res.status_code == 200, res.text
    pos = {p["po_number"]: p for p in client.get("/api/v1/purchase-orders").json()}
    assert pos["P00042"]["supplier_name"] == "Shenzhen Feng Trading"
    assert pos["P00042"]["currency"] == "USD"  # carried down with the order number
    lines = client.get(f"/api/v1/purchase-orders/{pos['P00042']['id']}").json()["lines"]
    assert [ln["sku"] for ln in lines] == ["SKU-A", "SKU-B", "SKU-C"]


def test_a_row_with_neither_order_number_nor_article_is_still_an_error(client: TestClient) -> None:
    content = b"po_number;sku;quantity;unit_price\nPO-1;A;10;1,00\n;;5;2,00\n"
    job = upload(client, content, "PURCHASE_ORDERS")
    report = validate(client, job)
    assert report["valid_rows"] == 1 and report["forward_filled_rows"] == 0
    assert [(e["row"], e["code"]) for e in report["errors"]] == [(3, "REQUIRED")]


def test_the_same_sku_twice_on_one_order_keeps_both_quantities(client: TestClient) -> None:
    """Two receipts of the same article at two prices. The second row used to be absorbed by the
    first — its quantity and price never written, and the row still counted as valid."""
    content = b"po_number;sku;quantity;unit_price\nPO-D;A;100;10,00\nPO-D;A;50;12,00\n"
    job = upload(client, content, "PURCHASE_ORDERS", "twice.csv")
    report = validate(client, job)
    assert report["valid_rows"] == 2 and report["lines_created"] == 2
    warning = next(w for w in report["warnings"] if w["code"] == "DUPLICATE_SKU_NEW_LINE")
    assert warning["params"] == {"po_number": "PO-D", "sku": "A", "line_no": 2}

    res = client.post(f"/api/v1/imports/{job['id']}/commit", json={"on_error": "abort"})
    assert res.status_code == 200, res.text
    (po,) = [p for p in client.get("/api/v1/purchase-orders").json() if p["po_number"] == "PO-D"]
    lines = client.get(f"/api/v1/purchase-orders/{po['id']}").json()["lines"]
    assert [(ln["line_no"], ln["quantity"], ln["unit_price"]) for ln in lines] == [
        (1, "100.0000", "10.0000"),
        (2, "50.0000", "12.0000"),
    ]

    # and the same file again writes the same two lines rather than piling up new ones
    again = upload(client, content, "PURCHASE_ORDERS", "twice.csv")
    res = client.post(f"/api/v1/imports/{again['id']}/commit", json={"on_error": "abort"})
    assert res.json()["report"]["lines_created"] == 0
    assert len(client.get(f"/api/v1/purchase-orders/{po['id']}").json()["lines"]) == 2


def test_the_same_line_said_twice_without_another_container_is_flagged(client: TestClient) -> None:
    """Two identical rows without line_no read as one line repeated for a second container; with no
    container, or the same one, that left 1 000 € of FOB out of the order without a word."""
    content = b"po_number;sku;quantity;unit_price\nPO-E;A;100;10,00\nPO-E;A;100;10,00\n"
    report = validate(client, upload(client, content, "PURCHASE_ORDERS", "same.csv"))
    assert report["lines_created"] == 1
    (warning,) = [w for w in report["warnings"] if w["code"] == "DUPLICATE_SKU_SAME_VALUES"]
    assert warning["row"] == 3 and warning["params"] == {"po_number": "PO-E", "sku": "A", "line_no": 1}

    same_box = (
        b"po_number;sku;quantity;unit_price;container_number\n"
        b"PO-F;A;100;10,00;MSCU1234567\nPO-F;A;100;10,00;MSCU1234567\n"
    )
    report = validate(client, upload(client, same_box, "PURCHASE_ORDERS", "same_box.csv"))
    assert "DUPLICATE_SKU_SAME_VALUES" in codes(report["warnings"])

    # the shape it was meant for: one order line repeated once per container
    per_box = (
        b"po_number;sku;quantity;unit_price;container_number;container_quantity\n"
        b"PO-G;A;100;10,00;MSCU1234567;60\nPO-G;A;100;10,00;CMAU9876543;40\n"
    )
    report = validate(client, upload(client, per_box, "PURCHASE_ORDERS", "per_box.csv"))
    assert report["lines_created"] == 1 and report["loads_created"] == 2
    assert "DUPLICATE_SKU_SAME_VALUES" not in codes(report["warnings"])


def test_a_line_total_weight_or_volume_is_divided_by_the_line_quantity(client: TestClient) -> None:
    """Two lines of 50 kg each, 100 and 10 pieces: read as unit weights, a 3 000 € freight split by
    weight went 2 727,27 / 272,73 instead of 1 500 / 1 500. Mapped as the line's total, it is one
    piece's weight that is stored."""
    content = (
        b"po_number;sku;quantity;unit_price;Poids total (kg);Volume total (m3)\n"
        b"PO-T;A;100;10,00;50;1,2\nPO-T;B;10;10,00;50;0,3\nPO-T;C;3;10,00;50;\n"
    )
    job = upload(client, content, "PURCHASE_ORDERS", "totals.csv")
    assert job["mapping"]["line_weight_kg"] == "Poids total (kg)"
    assert job["mapping"]["line_volume_cbm"] == "Volume total (m3)"
    res = client.post(
        f"/api/v1/imports/{job['id']}/commit", json={"mapping": job["mapping"], "on_error": "abort"}
    )
    assert res.status_code == 200, res.text
    (po,) = [p for p in client.get("/api/v1/purchase-orders").json() if p["po_number"] == "PO-T"]
    lines = {ln["sku"]: ln for ln in client.get(f"/api/v1/purchase-orders/{po['id']}").json()["lines"]}
    assert (lines["A"]["unit_weight_kg"], lines["A"]["unit_volume_cbm"]) == ("0.5000", "0.0120")
    assert (lines["B"]["unit_weight_kg"], lines["B"]["unit_volume_cbm"]) == ("5.0000", "0.0300")
    assert (lines["C"]["unit_weight_kg"], lines["C"]["unit_volume_cbm"]) == ("16.6667", None)

    # one dimension is said once: per unit or for the line, not both
    again = upload(client, content, "PURCHASE_ORDERS", "totals.csv")
    both = {**again["mapping"], "unit_weight_kg": "Poids total (kg)"}
    res = client.post(f"/api/v1/imports/{again['id']}/validate", json={"mapping": both})
    assert res.status_code == 422 and res.json()["code"] == "MAPPING_UNIT_AND_LINE_TOTAL"
    assert res.json()["dimension"] == "weight"


def test_a_currency_that_changes_inside_one_order_is_refused(client: TestClient) -> None:
    content = b"po_number;currency;sku;quantity;unit_price\nPO-X;USD;A;10;1,00\nPO-X;CNY;B;20;2,00\n"
    job = upload(client, content, "PURCHASE_ORDERS")
    report = validate(client, job)
    assert report["valid_rows"] == 1
    (error,) = report["errors"]
    assert error["code"] == "CURRENCY_MISMATCH" and error["row"] == 3
    assert error["params"] == {"po_number": "PO-X", "po_currency": "USD", "row_currency": "CNY"}


def test_a_negative_quantity_says_returns_are_not_supported(client: TestClient) -> None:
    content = b"po_number;sku;quantity;unit_price\nPO-R;A;-5;10,00\nPO-R;B;5;10,00\n"
    job = upload(client, content, "PURCHASE_ORDERS")
    report = validate(client, job)
    assert report["valid_rows"] == 1
    (error,) = report["errors"]
    assert error["code"] == "RETURN_NOT_SUPPORTED" and error["field"] == "quantity"
    assert error["params"] == {"value": "-5"}


def test_a_foreign_order_says_which_rate_of_which_date_and_from_where(client: TestClient) -> None:
    """ "Default rate applied" with neither the rate nor the date was the most worrying line of the
    report for a CFO, and the one they could not check. And the rate itself was 1: a 40 000 $ order
    counted as 40 000 EUR, every share by value skewed with it. The import now applies the ECB rate
    of the order date, as the purchase-order screen does, and says so."""
    job = upload(client, (IMPORTS_FIXTURES / "ebp_commandes.csv").read_bytes(), "PURCHASE_ORDERS", "ebp.csv")
    assert job["mapping"]["po_number"] == "N° cde" and "unit_weight_kg" not in job["mapping"]
    report = validate(client, job)
    assert report["valid_rows"] == 3
    assert not [w for w in report["warnings"] if w["code"] == "FX_RATE_DEFAULTED"]
    fx = next(w for w in report["warnings"] if w["code"] == "FX_RATE_APPLIED")
    assert fx["field"] == "currency" and fx["params"]["currency"] == "USD"
    assert fx["params"]["base_currency"] == "EUR" and Decimal(fx["params"]["rate"]) == Decimal("0.92")
    assert fx["params"]["source"].startswith("ecb") and len(fx["params"]["rate_date"]) == 10


def test_an_order_no_rate_is_known_for_keeps_its_rate_missing_and_its_figures_say_so(
    client: TestClient,
) -> None:
    """When no rate could be had — the ECB down, a currency it does not quote — the order was written
    at 1:1 for good, its dollars read as euros in every landed cost after it. Its rate stays missing
    now: the order says so, a cost split by value on its box refuses to guess, and its lines say why
    their value is not counted."""
    content = (
        b"po_number;currency;order_date;sku;quantity;unit_price;container_number\n"
        b"PO-XTS;XTS;2026-09-10;A;10;100,00;MSCU1234567\n"
        b"PO-EUR;EUR;2026-09-10;B;10;100,00;MSCU1234567\n"
    )
    job = upload(client, content, "PURCHASE_ORDERS")
    report = validate(client, job)
    assert "FX_RATE_DEFAULTED" not in codes(report["warnings"])
    (fx,) = [w for w in report["warnings"] if w["code"] == "FX_RATE_MISSING"]
    assert fx["params"] == {"currency": "XTS", "base_currency": "EUR", "rate_date": "2026-09-10"}
    res = client.post(f"/api/v1/imports/{job['id']}/commit", json={"on_error": "abort"})
    assert res.status_code == 200, res.text

    pos = {p["po_number"]: p for p in client.get("/api/v1/purchase-orders").json()}
    assert pos["PO-XTS"]["fob_base"] is None and pos["PO-EUR"]["fob_base"] == "1000.00"
    assert client.get(f"/api/v1/purchase-orders/{pos['PO-XTS']['id']}").json()["fx_rate"] is None

    (box,) = client.get("/api/v1/containers").json()
    body = {"scope": "CONTAINER", "target_id": box["id"], "cost_type": "DRAYAGE", "amount": "300.00",
            "currency": "EUR", "cost_date": "2026-09-12", "allocation_method": "BY_VALUE"}  # fmt: skip
    assert client.post("/api/v1/costs", json=body).status_code == 201
    landed = client.get(f"/api/v1/landed-costs/containers/{box['id']}").json()
    assert {ln["po_number"]: ln["fob_missing"] for ln in landed["lines"]} == {
        "PO-XTS": "FX_RATE_MISSING",
        "PO-EUR": None,
    }
    # never 1:1, nor the other order's value passed off as the box's
    assert landed["totals"]["fob"] is None and landed["fob_missing_lines"] == 1
    assert [(w["code"], w["amount_base"]) for w in landed["warnings"]] == [("MISSING_FX_RATE", "300.00")]

    # the rate a person sets on the order is the one every figure then uses
    patched = client.patch(f"/api/v1/purchase-orders/{pos['PO-XTS']['id']}", json={"fx_rate": "0.5"})
    assert patched.status_code == 200, patched.text
    landed = client.get(f"/api/v1/landed-costs/containers/{box['id']}").json()
    assert landed["totals"]["fob"] == "1500.00" and landed["warnings"] == []


def test_an_erp_sync_is_labelled_as_a_sync_not_as_a_dropped_file(
    client: TestClient, db: Any, org_id: Any
) -> None:
    """The nightly Odoo sync writes a CSV of its own; in the imports list it looked exactly like a
    file someone had dropped by hand."""
    from uuid import UUID

    from app.domain.models import ErpConnection, ErpKind, ErpSyncRun, ErpSyncStatus

    synced = upload(client, PO_LINES_CSV, "PURCHASE_ORDERS", "odoo-sync.csv")
    dropped = upload(client, PO_LINES_CSV, "PURCHASE_ORDERS", "export_excel.csv")
    connection = ErpConnection(
        org_id=org_id,
        kind=ErpKind.ODOO,
        url="https://erp.example.test",
        database="fstest",
        login="admin",
        api_key_sealed=b"sealed",
    )
    db.add(connection)
    db.flush()
    db.add(
        ErpSyncRun(
            org_id=org_id,
            connection_id=connection.id,
            status=ErpSyncStatus.SUCCEEDED,
            import_job_id=UUID(synced["id"]),
            detail={},
        )
    )
    db.flush()

    sources = {i["id"]: i["source"] for i in client.get("/api/v1/imports").json()}
    assert sources[synced["id"]] == "erp_sync"
    assert sources[dropped["id"]] == "upload"
    assert client.get(f"/api/v1/imports/{synced['id']}").json()["source"] == "erp_sync"


def test_an_order_file_s_rate_with_its_per_cent_sign_is_a_per_cent(client: TestClient) -> None:
    content = "N° commande;Réf;Qté;Prix unitaire;Taux de droits\nPO-R;A;10;5;1 %\nPO-R;B;10;5;4,5\n".encode()
    job = upload(client, content, "PURCHASE_ORDERS")
    res = client.post(f"/api/v1/imports/{job['id']}/commit", json={"mapping": job["mapping"]})
    assert res.status_code == 200, res.text
    (order,) = client.get("/api/v1/purchase-orders").json()
    lines = client.get(f"/api/v1/purchase-orders/{order['id']}").json()["lines"]
    assert {line["sku"]: Decimal(line["duty_rate"]) for line in lines} == {
        "A": Decimal("0.01"),  # « 1 % » — not a whole: a hundred per cent of duty on an article
        "B": Decimal("0.045"),
    }


def test_a_file_that_is_not_a_workbook_answers_a_code_not_a_500(client: TestClient) -> None:
    """An old .xls renamed .xlsx — common with SME users — raised BadZipFile straight into a 500."""
    res = client.post(
        "/api/v1/imports",
        data={"kind": "PURCHASE_ORDERS"},
        files={"file": ("ledger.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512, "text/csv")},
    )
    assert res.status_code == 422 and res.json()["code"] == "XLSX_UNREADABLE"


def test_a_file_over_a_cap_is_refused_with_the_cap(client: TestClient, monkeypatch: Any) -> None:
    from app.domain.imports import parsing

    monkeypatch.setattr(parsing, "MAX_ROWS", 2)
    res = client.post(
        "/api/v1/imports",
        data={"kind": "PURCHASE_ORDERS"},
        files={"file": ("big.csv", b"po_number;sku\nPO-1;A\nPO-2;B\n", "text/csv")},
    )
    assert res.status_code == 422
    assert res.json()["code"] == "TOO_MANY_ROWS" and res.json()["limit"] == 2


def test_an_upload_is_parsed_off_the_event_loop() -> None:
    """Parsing a large file inside an `async def` route blocked the one event loop for every tenant."""
    import inspect

    from app.api.v1.imports import create_import

    assert not inspect.iscoroutinefunction(create_import)
