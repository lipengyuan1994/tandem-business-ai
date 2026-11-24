"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests


@pytest.fixture(autouse=True)
def _clear_fake_ai_router() -> Iterator[None]:
    """Clear a fake AI router a test installed with set_router_for_tests(); many never clear it, and a leftover one
    made later tests see AI as configured (test_ai_module's not-configured checks after test_ai_team)."""
    yield
    set_router_for_tests(None)
    reset_router_cache()
