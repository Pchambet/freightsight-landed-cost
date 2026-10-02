"""The README's worked example is the engine's output, not a hand-typed table.

Runs `scripts/worked_example.py` in-process (pure engine, no database) and checks three things: the
"FreightSight default" column equals the unit costs `test_demo_story.py` pins through the API, every
policy reconciles to the same container total, and the README block is byte-for-byte what the script
prints today.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from decimal import Decimal
from functools import cache
from pathlib import Path
from types import ModuleType

BACKEND = Path(__file__).resolve().parent.parent
README = BACKEND.parent / "README.md"


@cache
def _example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worked_example", BACKEND / "scripts" / "worked_example.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def test_default_policy_matches_the_figures_the_api_returns() -> None:
    ex = _example()
    default = next(p for p in ex.POLICIES if p.name == "FreightSight default")
    assert ex.unit_landed_costs(default) == {
        "TYR-20555R16-91V": Decimal("17.9750"),
        "MAT-EVA-6040-GY": Decimal("26.1550"),
    }


def test_every_policy_reconciles_to_the_same_container_total() -> None:
    ex = _example()
    assert {ex.container_total(p) for p in ex.POLICIES} == {Decimal("26558.79")}


def test_readme_block_is_what_the_script_prints() -> None:
    block = re.search(
        r"<!-- worked-example:start -->\n(.*?)<!-- worked-example:end -->", README.read_text(), re.DOTALL
    )
    assert block is not None, "README.md lost its worked-example markers"
    assert block.group(1) == _example().markdown(), (
        "README worked example is stale: paste the output of `uv run python scripts/worked_example.py`"
    )
