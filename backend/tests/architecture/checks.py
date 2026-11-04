"""Pure checking functions used by the architecture tests (kept separate so tests can prove they catch violations)."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

COMPOSITION_ROOTS = {"app/main.py", "app/worker.py", "app/cli.py", "app/seed.py", "app/demo_logins.py"}


def _absolute(module: str | None, level: int, file_module: str) -> str:
    if level == 0:
        return module or ""
    base = file_module.split(".")[:-level]
    return ".".join([*base, module] if module else base)


def imports_of(source: str, file_module: str) -> Iterator[str]:
    """Yield absolute dotted names imported by `source` (a file whose dotted name is `file_module`)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(node.module, node.level, file_module)
            for alias in node.names:
                yield f"{base}.{alias.name}"


def violations_for_file(rel_path: str, source: str, deps: dict[str, frozenset[str]]) -> list[str]:
    """Rules from 02-architecture.md: platform never imports modules; a module imports only its own code,
    platform, and `public` of declared dependencies."""
    if rel_path in COMPOSITION_ROOTS:
        return []
    parts = Path(rel_path).with_suffix("").parts
    file_module = ".".join(parts if parts[-1] != "__init__" else (*parts[:-1], "__init__"))
    problems: list[str] = []
    owner = parts[2] if len(parts) > 2 and parts[:2] == ("app", "modules") else None
    is_test = "tests" in parts
    for name in imports_of(source, file_module):
        if not name.startswith("app.modules"):
            continue
        if parts[:2] == ("app", "platform"):
            problems.append(f"{rel_path}: platform must not import {name}")
            continue
        target = name.split(".")
        if len(target) < 3 or owner is None:
            if owner is not None and name != "app.modules.dependencies":
                problems.append(f"{rel_path}: imports {name}")
            continue
        other = target[2]
        if other == owner:
            continue
        if is_test and len(target) >= 4 and target[3] in ("public", "testing"):
            continue  # tests may build fixtures through any module's public API or test helpers
        if other not in deps.get(owner, frozenset()):
            problems.append(f"{rel_path}: module '{owner}' may not depend on '{other}' ({name})")
        elif len(target) < 4 or target[3] != "public":
            problems.append(f"{rel_path}: must import only {'.'.join(target[:3])}.public, not {name}")
    return problems


def find_cycle(deps: dict[str, Iterable[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        if state.get(n) == 1:
            return [*stack[stack.index(n) :], n]
        if state.get(n) == 2:
            return None
        state[n] = 1
        stack.append(n)
        for d in deps.get(n, ()):
            if (cycle := visit(d)) is not None:
                return cycle
        stack.pop()
        state[n] = 2
        return None

    for node in deps:
        if (cycle := visit(node)) is not None:
            return cycle
    return None
