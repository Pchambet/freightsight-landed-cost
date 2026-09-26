"""Which code page the reader picks, on the files French SMEs actually export.

The whole import flow hangs on one column: po_number is the only mandatory field, and in a French
export it is spelled "N° commande". One byte (0xB0) decides whether it reads as "N° commande" or
as "NḞ commande", and the second one maps to nothing.
"""

from __future__ import annotations

import pytest

from app.domain.imports.parsing import parse_table, suggest_mapping
from app.domain.models import ImportKind

# The header of a Sage/EBP/Excel FR export, with every accented byte such a file carries.
FRENCH_HEADER = "N° commande;Fournisseur;Référence;Désignation;Qté;Prix unitaire;N° conteneur"
FRENCH_ROWS = [
    "CF-2026-0147;Société Générale Pneus;PNE-205;Pneu 205/55 R16 été;600;12 500,50;MSCU1234567",
    "CF-2026-0148;Établissements Dufour;JAN-16;Jante alu 16 pouces;250;31,00;CMAU9876543",
]


def french_file(sep: str = ";") -> str:
    return (
        "\r\n".join([FRENCH_HEADER.replace(";", sep), *[r.replace(";", sep) for r in FRENCH_ROWS]]) + "\r\n"
    )


@pytest.mark.parametrize(
    ("label", "content"),
    [
        # The four shapes the audit reproduced, all cp1252, all answered iso8859_14 / mac_latin2
        # by the detector: "N°" became "NḞ" or "Nį" and po_number lost its mapping.
        ("cp1252 semicolon", french_file().encode("cp1252")),
        ("cp1252 comma", french_file(",").encode("cp1252")),
        ("cp1252 tab", french_file("\t").encode("cp1252")),
        ("cp1252 no trailing newline", french_file().strip().encode("cp1252")),
        ("utf-8", french_file().encode()),
        ("utf-8 BOM", "﻿".encode() + french_file().encode()),
        ("mac_roman", french_file().encode("mac_roman")),
        # An old Mac Excel export and a Linux/latin-9 export with the euro sign in a header.
        ("latin-9 euro", french_file().replace("Prix unitaire", "Prix unitaire (€)").encode("iso-8859-15")),
    ],
)
def test_french_headers_survive_every_common_encoding(label: str, content: bytes) -> None:
    table = parse_table(content)
    assert table.columns[0] == "N° commande", f"{label}: {table.columns} ({table.encoding})"
    assert "Référence" in table.columns and "Désignation" in table.columns
    mapping = suggest_mapping(ImportKind.PURCHASE_ORDERS, table.columns)
    assert mapping["po_number"] == "N° commande", f"{label}: {mapping}"
    assert mapping["container_number"] == "N° conteneur"
    assert table.rows[0]["Fournisseur"] == "Société Générale Pneus", label
    assert table.rows[1]["Fournisseur"] == "Établissements Dufour", label


def test_a_central_european_export_is_still_read_by_the_detector() -> None:
    """The shortcut must not swallow a file it cannot read: a Czech export read as cp1252 spells
    "Množství" with a ž, and that is the signal to let charset_normalizer answer."""
    content = (
        "Číslo objednávky;Dodavatel;Zboží;Množství;Jednotková cena\r\n"
        "OBJ-2026-11;Škoda Auto a.s.;DÍL-455;1200;12,50\r\n"
        "OBJ-2026-12;Přerovské strojírny;DÍL-770;800;7,90\r\n"
    ).encode("cp1250")
    table = parse_table(content)
    assert table.columns[0] == "Číslo objednávky", table.columns
    assert table.rows[0]["Dodavatel"] == "Škoda Auto a.s."
    assert table.rows[1]["Dodavatel"] == "Přerovské strojírny"


def test_the_euro_sign_survives_a_latin9_file() -> None:
    content = "Montant (€);Devise\r\n12,50;EUR\r\n".encode("iso-8859-15")
    table = parse_table(content)
    assert table.columns == ["Montant (€)", "Devise"]
