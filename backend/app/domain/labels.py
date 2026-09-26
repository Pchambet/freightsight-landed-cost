"""French words for what leaves the application as a file or a document: the CSV exports and the
labels written into the customer's ERP.

A CSV opened in Excel by a CFO is a screen like any other: `unit_landed_cost` and `OCEAN_FREIGHT`
are no more readable there than in the application. With `locale=fr` the header row and the coded
cells are written in French; with `locale=en` the file keeps its snake_case names and its codes,
which is what a script reading it back wants.

The value labels are the ones the screens use (`frontend/messages/fr.json`, namespace `domain`):
`tests/test_export_labels.py` compares the two word for word, so a label reworded on one side
cannot quietly diverge on the other.
"""

from __future__ import annotations

HEADERS_FR: dict[str, str] = {
    "period": "Période",
    "period_status": "État de la période",
    "finding": "Constat",
    "confidence": "Certitude",
    "basis": "Conteneurs comparés",
    "details": "Détail",
    "cost_basis": "Base du coût",
    "closed_at": "Clôturé le",
    "containers": "Conteneurs",
    "drift_reason": "Origine de l'écart",
    "frozen_landed": "Coût de revient clôturé",
    "live_landed": "Coût de revient à ce jour",
    "difference": "Écart depuis la clôture",
    "changed_at": "Modifié le",
    "row_type": "Type de ligne",
    "container_number": "N° conteneur",
    "arrival_date": "Date d'arrivée",
    "shipment_reference": "Expédition",
    "supplier": "Fournisseur",
    "po_number": "N° commande",
    "po_numbers": "N° commandes",
    "line_no": "N° ligne",
    "sku": "Référence",
    "description": "Désignation",
    "quantity": "Quantité",
    "fob": "FOB",
    "allocated": "Frais ventilés",
    "import_vat": "TVA import (hors coût de revient)",
    "landed": "Coût de revient rendu",
    "unit_landed_cost": "Coût de revient unitaire",
    "unallocated": "Non ventilé",
    "unallocated_reason": "Raison du non-ventilé",
    "period_basis": "Date retenue pour la période",
    "status": "Statut",
    "cost_type": "Type de coût",
    "scope": "Périmètre",
    "target": "Cible",
    "vendor": "Prestataire",
    "invoice_number": "N° facture",
    "cost_date": "Date du coût",
    "amount": "Montant",
    "currency": "Devise",
    "fx_rate": "Taux de change",
    "amount_base": "Montant en devise de base",
    "allocation_method": "Méthode de ventilation",
    "replaces_estimate": "Remplace un estimé",
    "order_date": "Date de commande",
    "incoterm": "Incoterm",
    "unit_price": "Prix unitaire",
    "unit_weight_kg": "Poids unitaire (kg)",
    "unit_volume_cbm": "Volume unitaire (m³)",
    "hs_code": "Code SH",
    "duty_rate": "Taux de droits",
    "carrier_scac": "Compagnie (SCAC)",
    "milestone": "Étape",
    "tracking_state": "Suivi",
    "eta": "ETA",
    "discharged_at": "Déchargé le",
    "last_free_day": "Dernier jour franc",
    "gate_out_at": "Sorti du terminal le",
    "detention_deadline": "Échéance de détention",
    "empty_returned_at": "Vide restitué le",
    "dnd_risk": "Risque surestaries / détention",
    "load_count": "Lignes chargées",
}

COST_TYPE_FR: dict[str, str] = {
    "OCEAN_FREIGHT": "Fret maritime",
    "AIR_FREIGHT": "Fret aérien",
    "INSURANCE": "Assurance",
    "ORIGIN_CHARGES": "Frais à l'origine",
    "THC": "Manutention portuaire (THC)",
    "BL_FEE": "Frais de connaissement",
    "CUSTOMS_DUTY": "Droits de douane",
    "CUSTOMS_BROKERAGE": "Frais de dédouanement",
    "IMPORT_VAT": "TVA import (récupérable)",
    "DRAYAGE": "Camionnage / livraison",
    "DEMURRAGE": "Surestaries",
    "DETENTION": "Détention",
    "WAREHOUSING": "Entreposage",
    "INSPECTION": "Inspection",
    "BANK_FEES": "Frais bancaires",
    "OTHER": "Autre",
}

SCOPE_FR: dict[str, str] = {
    "SHIPMENT": "Expédition",
    "CONTAINER": "Conteneur",
    "PO": "Commande",
    "PO_LINE": "Ligne de commande",
}

METHOD_FR: dict[str, str] = {
    "BY_VALUE": "À la valeur",
    "BY_WEIGHT": "Au poids",
    "BY_VOLUME": "Au volume",
    "BY_QUANTITY": "À la quantité",
    "MANUAL": "Manuel (%)",
    "BY_CIF_VALUE": "À la valeur CIF",
    "BY_THEORETICAL_DUTY": "Aux droits théoriques",
}

MILESTONE_FR: dict[str, str] = {
    "BOOKED": "Réservé",
    "GATE_IN_FULL_ORIGIN": "Entrée terminal (origine)",
    "LOADED": "Chargé sur le navire",
    "VESSEL_DEPARTED": "Navire parti",
    "VESSEL_ARRIVED": "Navire arrivé",
    "DISCHARGED": "Déchargé",
    "AVAILABLE_FOR_PICKUP": "Disponible à l'enlèvement",
    "GATE_OUT_FULL": "Sortie terminal (plein)",
    "DELIVERED": "Livré",
    "GATE_IN_EMPTY_RETURN": "Vide restitué",
    "GATE_OUT_EMPTY_ORIGIN": "Vide sorti du dépôt (origine)",
    "TRANSSHIPMENT_ARRIVED": "Arrivé au port de transbordement",
    "TRANSSHIPMENT_DISCHARGED": "Déchargé (transbordement)",
    "TRANSSHIPMENT_LOADED": "Rechargé (transbordement)",
    "TRANSSHIPMENT_DEPARTED": "Reparti du transbordement",
    "RAIL_LOADED": "Chargé sur rail",
    "RAIL_DEPARTED": "Train parti",
    "RAIL_ARRIVED": "Train arrivé",
    "RAIL_UNLOADED": "Déchargé du rail",
    "UNKNOWN": "Événement non reconnu",
}

RISK_FR: dict[str, str] = {
    "NONE": "Aucun risque",
    "LOW": "Faible",
    "MEDIUM": "Moyen",
    "HIGH": "Élevé",
    "INCURRING": "Surestaries en cours",
}

STATUS_FR: dict[str, str] = {"ESTIMATE": "Estimé", "ACTUAL": "Réel"}
TRACKING_STATE_FR: dict[str, str] = {
    "UNTRACKED": "Non suivi",
    "MANUAL": "Manuel",
    "PENDING": "En attente",
    "ACTIVE": "Actif",
    "FAILED": "En échec",
    "ENDED": "Terminé",
}
ROW_TYPE_FR: dict[str, str] = {"LINE": "Ligne", "UNALLOCATED": "Non ventilé"}
PERIOD_BASIS_FR: dict[str, str] = {"arrival_date": "date d'arrivée", "cost_date": "date du coût"}

#: Why a container's landed cost is no longer the one its month was closed at.
DRIFT_REASON_FR = {
    "costs": "Coûts ou chargements modifiés",
    "left_period": "Date d'arrivée modifiée : sorti du mois",
    "joined_period": "Date d'arrivée modifiée : entré dans le mois",
}

#: The audit's findings, and how sure each one is.
FINDING_FR = {
    "DUPLICATE_CHARGE": "Prestation facturée deux fois",
    # "Estimé", not "cotation": the estimate is often the company's own rate card, and a forwarder
    # told it billed above a quote it never gave stops reading there.
    "ABOVE_QUOTE": "Facture au-dessus de l'estimé",
    "OUTLIER_CHARGE": "Montant hors norme pour la route",
    "ESTIMATE_NEVER_INVOICED": "Estimé jamais facturé",
    "UNALLOCATED_COST": "Coût non ventilé",
    "DUTY_RATE_MISSING": "Droits ventilés sans taux sur une ligne",
    "DOUBLE_ENTRY": "Facture saisie deux fois",
}
#: How sure the rule is — not what to do about it: an invoice above its estimate is a fact, whether the
#: surcharge turns out to be owed or not.
CONFIDENCE_FR = {"sure": "Établi", "to_check": "À vérifier"}
#: The moving parts of a finding, as the "Détail" column of the findings file names them.
FINDING_PARAM_FR = {
    "first_invoice": "Première facture",
    "first_vendor": "Premier prestataire",
    "first_amount": "Premier montant",
    "same_vendor": "Même prestataire",
    "quoted": "Estimé",
    "invoiced": "Facturé",
    "currency": "Devise",
    "share_pct": "Écart en %",
    "fx_effect": "Effet de change",
    "amount": "Facturé",
    "median": "Médiane de la route",
    "ratio": "Multiple de la médiane",
    "route": "Route",
    "size": "Longueur du conteneur (pieds)",
    "days": "Jours depuis l'arrivée",
    "landed_on": "Arrivé le",
    "reason": "Raison",
    "lines": "Lignes sans taux",
    "skus": "Références",
    "po_numbers": "Commandes",
}
#: The params that are decimal numbers: written with the decimal comma, like every other cell.
DECIMAL_PARAMS = frozenset(
    {"first_amount", "quoted", "invoiced", "share_pct", "fx_effect", "amount", "median", "ratio"}
)
YES_NO_FR = {"true": "oui", "false": "non"}
#: Why the engine could not spread a cost (its `Unallocated.code`).
UNALLOCATED_REASON_FR = {
    "MISSING_BASIS": "Base de ventilation manquante (poids, volume ou valeur)",
    "MISSING_DUTY_RATE": "Taux de droits manquant",
    "INVALID_MANUAL_SPLIT": "Répartition manuelle invalide",
    "NO_TARGET": "Aucun chargement visé",
    "ALL_WEIGHTS_ZERO": "Toutes les bases de ventilation sont nulles",
    "ALLOCATION_ERROR": "Ventilation impossible",
}


def finding_details_fr(params: dict[str, str]) -> str:
    """A finding's params as the French file shows them: « Cotation : 2890,68 ; Facturé : 3199,95 »."""
    shown = []
    for key, value in params.items():
        if not value:
            continue  # an invoice without a number is not "Première facture : "
        if key in DECIMAL_PARAMS:
            value = value.replace(".", ",")
        elif key == "reason":
            value = UNALLOCATED_REASON_FR.get(value, value)
        elif key == "same_vendor":
            value = YES_NO_FR.get(value, value)
        shown.append(f"{FINDING_PARAM_FR.get(key, key)} : {value}")
    return " ; ".join(shown)


#: Which column holds which vocabulary. Translating by column, never by value: a supplier called
#: "MANUAL" or a part number "OTHER" is somebody's data and stays as typed.
VALUES_FR: dict[str, dict[str, str]] = {
    "finding": FINDING_FR,
    "confidence": CONFIDENCE_FR,
    "period_status": {"open": "Ouverte", "closed": "Clôturée"},
    "cost_basis": {"last": "Dernier arrivage", "average": "Moyenne pondérée"},
    "drift_reason": DRIFT_REASON_FR,
    "cost_type": COST_TYPE_FR,
    "scope": SCOPE_FR,
    "allocation_method": METHOD_FR,
    "milestone": MILESTONE_FR,
    "dnd_risk": RISK_FR,
    "status": STATUS_FR,
    "tracking_state": TRACKING_STATE_FR,
    "row_type": ROW_TYPE_FR,
    "period_basis": PERIOD_BASIS_FR,
}


def header_fr(name: str) -> str:
    """The French title of a column; a cost-type column (one per type in the landed-cost file) gets
    the cost type's own label, and a name nobody listed stays as it is rather than disappear."""
    return HEADERS_FR.get(name) or COST_TYPE_FR.get(name) or name
