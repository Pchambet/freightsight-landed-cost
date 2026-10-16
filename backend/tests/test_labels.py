"""The French CSV says what the screens say: same column vocabulary, same labels, word for word."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.domain import labels
from app.domain.models import (
    AllocationMethod,
    ContainerMilestone,
    CostScope,
    CostStatus,
    CostType,
    DndRisk,
    TrackingState,
)

MESSAGES = Path(__file__).resolve().parents[2] / "frontend" / "messages" / "fr.json"


@pytest.mark.skipif(not MESSAGES.exists(), reason="the front-end messages are not in this checkout")
@pytest.mark.parametrize(
    ("ours", "namespace"),
    [
        (labels.COST_TYPE_FR, "costType"),
        (labels.SCOPE_FR, "scope"),
        (labels.METHOD_FR, "method"),
        (labels.MILESTONE_FR, "milestone"),
        (labels.RISK_FR, "risk"),
        (labels.FINDING_FR, "finding"),
        (labels.CONFIDENCE_FR, "confidence"),
    ],
)
def test_the_csv_uses_the_same_words_as_the_screens(ours: dict[str, str], namespace: str) -> None:
    screens = json.loads(MESSAGES.read_text(encoding="utf-8"))["domain"][namespace]
    assert ours == screens


def test_every_code_a_cell_can_hold_has_a_french_label() -> None:
    assert {c.value for c in CostType} <= set(labels.COST_TYPE_FR)
    assert {c.value for c in CostScope} <= set(labels.SCOPE_FR)
    assert {c.value for c in AllocationMethod} <= set(labels.METHOD_FR)
    assert {c.value for c in ContainerMilestone} <= set(labels.MILESTONE_FR)
    assert {c.value for c in DndRisk} <= set(labels.RISK_FR)
    assert {c.value for c in CostStatus} <= set(labels.STATUS_FR)
    assert {c.value for c in TrackingState} <= set(labels.TRACKING_STATE_FR)


def test_every_reason_the_engine_leaves_a_cost_unallocated_has_a_french_label() -> None:
    """The audit's French file names the reason, not the code: « Raison : Taux de change manquant »."""
    engine_source = (Path(labels.__file__).parent / "costing" / "engine.py").read_text()
    codes = set(re.findall(r'code = "([A-Z_]+)"', engine_source))
    assert {"MISSING_DUTY_RATE", "MISSING_FX_RATE"} <= codes
    assert codes <= set(labels.UNALLOCATED_REASON_FR)
    assert labels.finding_details_fr({"reason": "MISSING_FX_RATE"}) == "Raison : Taux de change manquant"


def test_a_cost_type_column_is_titled_like_the_cost_type_and_an_unknown_name_stays() -> None:
    assert labels.header_fr("OCEAN_FREIGHT") == "Fret maritime"
    assert labels.header_fr("unit_landed_cost") == "Coût de revient unitaire"
    assert labels.header_fr("something_new") == "something_new"
