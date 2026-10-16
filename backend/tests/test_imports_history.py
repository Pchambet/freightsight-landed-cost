"""Every file that goes into the books, whatever its kind, and every file taken back, leaves a line in
the history: which file, what kind, what it wrote, what it refused, the dates its costs carry — as
codes and counts, which the screen words in either language."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from previews import commit_previewed


def upload(client: TestClient, kind: str, name: str, *lines: str) -> str:
    content = ("﻿" + "\n".join(lines) + "\n").encode()
    res = client.post("/api/v1/imports", data={"kind": kind}, files={"file": (name, content, "text/csv")})
    assert res.status_code == 201, res.text
    job_id: str = res.json()["id"]
    return job_id


def committed(client: TestClient, job_id: str) -> None:
    mapping = client.get(f"/api/v1/imports/{job_id}").json()["mapping"]
    previewed = client.post(f"/api/v1/imports/{job_id}/validate", json={"mapping": mapping})
    assert previewed.status_code == 200, previewed.text
    res = commit_previewed(client, job_id)
    assert res.status_code == 200, res.text


def entry(client: TestClient, job_id: str, action: str) -> dict[str, Any]:
    page = client.get("/api/v1/audit-log", params={"entity_id": job_id}).json()
    (found,) = [e for e in page["entries"] if e["action"] == action]
    assert found["entity_type"] == "import"
    after: dict[str, Any] = found["after"]
    return after


def test_an_order_file_is_in_the_history(client: TestClient) -> None:
    job = upload(
        client,
        "PURCHASE_ORDERS",
        "commandes.csv",
        "N° commande;Fournisseur;Réf;Qté;Prix unitaire",
        "PO-A;Ardent Tyres;A;1000;10,00",
        "PO-A;Ardent Tyres;B;50;4,00",
        "PO-C;Kestrel Tyre Co.;C;10;abc",
    )
    committed(client, job)
    assert entry(client, job, "import.committed") == {
        "kind": "PURCHASE_ORDERS",
        "file_name": "commandes.csv",
        "rows": 3,
        "valid_rows": 2,
        "refused": 1,
        "lines_created": 2,
        "lines_updated": 0,
    }


def test_a_legacy_file_is_in_the_history(client: TestClient) -> None:
    job = upload(
        client,
        "LEGACY_PO_CONTAINER",
        "ancien.csv",
        "po_number,supplier_name,total_value,container_number,allocation_percentage",
        "PO-2026-001,Ardent Tyres France,12500.50,MSCU1234567,100",
    )
    assert client.post(f"/api/v1/imports/{job}/commit", json={}).status_code == 200
    after = entry(client, job, "import.committed")
    assert (after["kind"], after["file_name"], after["rows"], after["valid_rows"], after["refused"]) == (
        "LEGACY_PO_CONTAINER",
        "ancien.csv",
        1,
        1,
        0,
    )
    assert (after["lines_created"], after["lines_updated"]) == (1, 0)


def test_a_tracking_sheet_is_in_the_history(client: TestClient) -> None:
    job = upload(
        client,
        "CONTAINERS",
        "suivi.csv",
        "N° conteneur;Type de conteneur;ETA;Arrivée réelle",
        "MSCU4821994;40HC;08/03/2026;08/03/2026",
        "NOT-A-BOX;40HC;08/03/2026;",
    )
    committed(client, job)
    assert entry(client, job, "import.committed") == {
        "kind": "CONTAINERS",
        "file_name": "suivi.csv",
        "rows": 2,
        "valid_rows": 1,
        "refused": 1,
        "containers_created": 1,
        "containers_updated": 0,
    }


def test_a_box_the_file_contradicts_counts_all_its_rows_as_left_out(client: TestClient) -> None:
    """Two rows of one box that disagree: the box is refused whole, though only one row carries the
    error — the history says how many rows went in, so the rows left out add up."""
    job = upload(
        client,
        "CONTAINERS",
        "suivi.csv",
        "N° conteneur;Type de conteneur;ETA",
        "MSCU4821994;40HC;08/03/2026",
        "MSCU4821994;40HC;09/03/2026",
        "TGHU1234567;40HC;10/03/2026",
    )
    committed(client, job)
    after = entry(client, job, "import.committed")
    assert (after["rows"], after["valid_rows"], after["refused"]) == (3, 1, 1)
    assert after["containers_created"] == 1


def test_a_tariff_is_in_the_history(client: TestClient) -> None:
    header = "Référence;Désignation;Taux de droits"
    committed(client, upload(client, "PRODUCTS", "tarif.csv", header, "TYRE;Pneu;4,5"))
    job = upload(client, "PRODUCTS", "tarif-2.csv", header, "TYRE;Pneu;5", "JACK;Cric;0", ";Sans référence;1")
    committed(client, job)
    assert entry(client, job, "import.committed") == {
        "kind": "PRODUCTS",
        "file_name": "tarif-2.csv",
        "rows": 3,
        "valid_rows": 2,
        "refused": 1,
        "products_created": 1,
        "products_updated": 1,
    }


LEDGER = "Date du coût;Prestataire;N° facture;Libellé;Montant HT;Devise;N° conteneur"


def test_a_costs_file_and_its_undo_are_in_the_history(client: TestClient) -> None:
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821994"})
    assert box.status_code == 201, box.text
    job = upload(
        client,
        "COSTS",
        "grand-livre.csv",
        LEDGER,
        "12/03/2026;TRANSDEMO;F-1;THC;285,00;EUR;MSCU4821994",
        "28/03/2026;TRANSDEMO;F-2;Fret maritime;3 000,00;EUR;MSCU4821994",
        "29/03/2026;TRANSDEMO;F-3;THC;abc;EUR;MSCU4821994",
    )
    committed(client, job)
    after = entry(client, job, "import.committed")
    assert len(after.pop("cost_ids")) == 2
    assert after == {
        "kind": "COSTS",
        "file_name": "grand-livre.csv",
        "rows": 3,
        "valid_rows": 2,
        "refused": 1,
        "costs_skipped": 0,
        "cost_dates": "2026-03-12..2026-03-28",
        "force_duplicates": False,
    }

    assert client.delete(f"/api/v1/imports/{job}").status_code == 200
    undone = entry(client, job, "import.undone")
    assert len(undone.pop("cost_ids")) == 2
    assert undone == {
        "kind": "COSTS",
        "file_name": "grand-livre.csv",
        "cost_dates": "2026-03-12..2026-03-28",
        "estimates_reopened": 0,
    }
