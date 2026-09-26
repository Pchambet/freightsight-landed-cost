"""What each kind of import reads, and the header each column carries in either language.

A template a person downloads is written with these headers, and the application's own exports use
the French ones: each header is an alias of its field (`parsing.TARGET_ALIASES`), so a file written
from either maps itself — a test holds every kind to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import ImportKind


@dataclass(frozen=True)
class Field:
    name: str
    header_fr: str
    header_en: str
    required: bool = False


#: The kinds in the order a quarter is loaded: the catalogue lends its rates only to order lines written
#: after it, costs need the boxes and orders they land on. The old one-row-per-order format comes last.
KIND_ORDER: tuple[ImportKind, ...] = (
    ImportKind.PRODUCTS,
    ImportKind.PURCHASE_ORDERS,
    ImportKind.CONTAINERS,
    ImportKind.COSTS,
    ImportKind.LEGACY_PO_CONTAINER,
)

FIELDS: dict[ImportKind, tuple[Field, ...]] = {
    ImportKind.PURCHASE_ORDERS: (
        Field("po_number", "N° commande", "PO number", required=True),
        Field("supplier_name", "Fournisseur", "Supplier"),
        Field("currency", "Devise", "Currency"),
        Field("order_date", "Date de commande", "Order date"),
        Field("incoterm", "Incoterm", "Incoterm"),
        Field("line_no", "N° ligne", "Line number"),
        Field("sku", "Référence", "SKU"),
        Field("description", "Désignation", "Description"),
        Field("quantity", "Quantité", "Quantity", required=True),
        Field("unit_price", "Prix unitaire", "Unit price", required=True),
        Field("unit_weight_kg", "Poids unitaire (kg)", "Unit weight (kg)"),
        Field("unit_volume_cbm", "Volume unitaire (m³)", "Unit volume (m3)"),
        Field("hs_code", "Code SH", "HS code"),
        Field("duty_rate", "Taux de droits", "Duty rate"),
        Field("container_number", "N° conteneur", "Container number"),
        Field("container_quantity", "Quantité dans le conteneur", "Quantity in container"),
    ),
    ImportKind.CONTAINERS: (
        Field("container_number", "N° conteneur", "Container number", required=True),
        Field("iso_type", "Type de conteneur", "Container type"),
        Field("po_numbers", "N° commandes", "PO numbers"),
        Field("shipment_reference", "N° B/L", "B/L number"),
        Field("carrier", "Compagnie maritime", "Shipping line"),
        Field("origin_port", "Port de chargement", "Port of loading"),
        Field("destination_port", "Port de déchargement", "Port of discharge"),
        Field("etd", "ETD", "ETD"),
        Field("eta", "ETA", "ETA"),
        Field("ata", "Arrivée réelle", "Actual arrival"),
        Field("discharged_at", "Déchargé le", "Discharged on"),
        Field("gate_out_at", "Sorti du terminal le", "Gate out on"),
        Field("empty_returned_at", "Vide restitué le", "Empty returned on"),
    ),
    ImportKind.COSTS: (
        Field("cost_date", "Date du coût", "Cost date", required=True),
        Field("vendor", "Prestataire", "Vendor"),
        Field("invoice_number", "N° facture", "Invoice number"),
        Field("label", "Libellé", "Label"),
        Field("cost_type", "Type de coût", "Cost type"),
        Field("amount", "Montant HT", "Amount excl. VAT", required=True),
        Field("currency", "Devise", "Currency"),
        Field("container_number", "N° conteneur", "Container number"),
        Field("shipment_reference", "N° B/L", "B/L number"),
        Field("po_number", "N° commande", "PO number"),
        # A purchase journal as the accounts export it: one line per account, the charge in the debit,
        # the supplier's total and the refunds in the credit. The account says which line is a charge.
        Field("account", "Compte", "Account"),
        Field("credit", "Crédit", "Credit"),
    ),
    ImportKind.PRODUCTS: (
        Field("sku", "Référence", "SKU", required=True),
        Field("description", "Désignation", "Description"),
        Field("hs_code", "Code SH", "HS code"),
        Field("duty_rate", "Taux de droits", "Duty rate"),
        Field("unit_weight_kg", "Poids unitaire (kg)", "Unit weight (kg)"),
        Field("unit_volume_cbm", "Volume unitaire (m³)", "Unit volume (m3)"),
        Field("sale_price", "Prix de vente HT", "Sale price excl. VAT"),
        Field("sale_currency", "Devise de vente", "Sale currency"),
    ),
    ImportKind.LEGACY_PO_CONTAINER: (
        Field("po_number", "N° commande", "PO number", required=True),
        Field("supplier_name", "Fournisseur", "Supplier"),
        Field("total_value", "Valeur totale", "Total value"),
        Field("container_number", "N° conteneur", "Container number"),
        Field("allocation_percentage", "Part (%)", "Share (%)"),
    ),
}

#: Groups of fields of which at least one must be mapped: a cost lands on a box, a bill of lading or an
#: order — whichever column says it, or its label.
REQUIRED_ONE_OF: dict[ImportKind, tuple[tuple[str, ...], ...]] = {
    ImportKind.COSTS: (("container_number", "shipment_reference", "po_number", "label"),),
}


def required_fields(kind: ImportKind) -> list[str]:
    return [field.name for field in FIELDS[kind] if field.required]
