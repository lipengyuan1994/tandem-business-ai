"""The payments relay is a separate service (ADR-022): no shared code; Tandem reaches it only over HTTP."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _is_test(rel: str) -> bool:
    return "/tests/" in rel or rel.endswith("/testing.py") or rel.startswith("tests/")


def test_relay_imports_nothing_from_tandem() -> None:
    problems = [
        f"{path.relative_to(BACKEND)}: {name}"
        for path in sorted((BACKEND / "app" / "relay").rglob("*.py"))
        for name in _imports(path)
        if name == "app" or (name.startswith("app.") and not name.startswith("app.relay"))
    ]
    assert problems == []


def test_tandem_imports_the_relay_only_in_tests() -> None:
    problems: list[str] = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith("app/relay/") or _is_test(rel):
            continue
        problems += [f"{rel}: {name}" for name in _imports(path) if name.startswith("app.relay")]
    assert problems == []
