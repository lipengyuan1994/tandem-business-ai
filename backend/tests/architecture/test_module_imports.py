"""Module boundaries (04-repository-and-conventions.md#module-boundaries-are-tested)."""

from __future__ import annotations

from pathlib import Path

from app.modules.dependencies import DEPENDENCIES
from tests.architecture.checks import violations_for_file

BACKEND = Path(__file__).resolve().parents[2]


def test_no_forbidden_imports_in_codebase() -> None:
    problems: list[str] = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        problems += violations_for_file(rel, path.read_text(encoding="utf-8"), DEPENDENCIES)
    assert problems == []


def test_checker_catches_internal_import_of_another_module() -> None:
    src = "from app.modules.crm.repository import get\n"
    assert violations_for_file("app/modules/scheduling/service.py", src, DEPENDENCIES)


def test_checker_catches_undeclared_dependency() -> None:
    src = "from app.modules.scheduling.public import find_slots\n"  # crm may not depend on scheduling
    assert violations_for_file("app/modules/crm/service.py", src, DEPENDENCIES)


def test_checker_catches_relative_cross_module_import() -> None:
    src = "from ..inventory import repository\n"
    assert violations_for_file("app/modules/scheduling/service.py", src, DEPENDENCIES)


def test_checker_catches_platform_importing_a_module() -> None:
    assert violations_for_file("app/platform/audit.py", "import app.modules.crm.public\n", DEPENDENCIES)


def test_checker_allows_public_import_of_declared_dependency() -> None:
    src = "from app.modules.inventory.public import reserve_stock\nfrom app.modules.inventory import public\n"
    assert violations_for_file("app/modules/scheduling/executors.py", src, DEPENDENCIES) == []


def test_tests_may_use_other_modules_public_and_testing_but_not_internals() -> None:
    ok = "from app.modules.org import public\nfrom app.modules.identity.testing import login_as\n"
    assert violations_for_file("app/modules/audit/tests/test_api.py", ok, DEPENDENCIES) == []
    bad = "from app.modules.org.repository import get_business\n"
    assert violations_for_file("app/modules/audit/tests/test_api.py", bad, DEPENDENCIES)
