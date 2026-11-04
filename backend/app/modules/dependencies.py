"""Declared module dependency graph (02-architecture.md). Enforced by tests/architecture.

A module may import `app.platform.*` and `app.modules.<dep>.public` for each dep listed here, nothing else.
It may subscribe only to events of modules it may depend on. The graph must stay acyclic.
Order of MODULES is the registration order (dependencies first).
"""

from __future__ import annotations

DEPENDENCIES: dict[str, frozenset[str]] = {
    "org": frozenset(),
    "identity": frozenset({"org"}),
    "audit": frozenset({"identity"}),
    "approvals": frozenset({"org", "identity"}),
    "notifications": frozenset({"org", "identity", "approvals"}),
    "files": frozenset(),
    "crm": frozenset({"approvals"}),
    "catalog": frozenset({"org", "approvals"}),
    "inventory": frozenset({"org", "catalog", "approvals"}),
    "conversations": frozenset({"org", "crm", "approvals", "notifications"}),
    "scheduling": frozenset({"org", "crm", "catalog", "inventory", "conversations", "approvals", "notifications"}),
    "marketing": frozenset({"org", "crm", "catalog", "scheduling", "approvals", "notifications"}),
    "sales": frozenset({"org", "crm", "catalog", "inventory", "scheduling", "approvals", "notifications"}),
    "purchasing": frozenset({"org", "catalog", "inventory", "approvals", "notifications"}),
    "dashboard": frozenset({"org", "crm", "scheduling", "inventory", "conversations", "approvals", "notifications"}),
    "ai": frozenset(
        {
            "org",
            "identity",
            "crm",
            "catalog",
            "scheduling",
            "inventory",
            "conversations",
            "marketing",
            "approvals",
            "sales",
            "purchasing",
            "dashboard",
            "notifications",
        }
    ),
    "voice": frozenset({"ai", "conversations"}),
    "admin_ops": frozenset({"org", "identity", "files", "notifications", "crm"}),
}


def topological_order() -> list[str]:
    """Modules ordered so that every dependency comes before its dependants. Raises on cycles."""
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(name: str) -> None:
        if state.get(name) == 1:
            raise ValueError(f"dependency cycle through {name}")
        if state.get(name) == 2:
            return
        state[name] = 1
        for dep in sorted(DEPENDENCIES[name]):
            visit(dep)
        state[name] = 2
        order.append(name)

    for module in sorted(DEPENDENCIES):
        visit(module)
    return order


MODULES: list[str] = topological_order()
