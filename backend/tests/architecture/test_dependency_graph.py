"""The declared graph is acyclic and matches the roadmap table (02-architecture.md)."""

from __future__ import annotations

import re
from pathlib import Path

from app.modules.dependencies import DEPENDENCIES, MODULES
from tests.architecture.checks import find_cycle

ROADMAP = Path(__file__).resolve().parents[3] / "docs" / "roadmap" / "02-architecture.md"


def test_dependency_graph_is_acyclic() -> None:
    assert find_cycle(DEPENDENCIES) is None
    position = {m: i for i, m in enumerate(MODULES)}
    assert all(position[d] < position[m] for m, ds in DEPENDENCIES.items() for d in ds)


def test_cycle_detector_finds_cycles() -> None:
    assert find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) == ["a", "b", "c", "a"]


def test_graph_matches_roadmap_table() -> None:
    table: dict[str, frozenset[str]] = {}
    for m in re.finditer(r"^\| `(\w+)`(?: \(v1\))? \| ([^|]+) \| \d \|$", ROADMAP.read_text(encoding="utf-8"), re.M):
        deps = m.group(2).strip()
        table[m.group(1)] = frozenset() if deps == "—" else frozenset(d.strip() for d in deps.split(","))
    assert table == DEPENDENCIES
