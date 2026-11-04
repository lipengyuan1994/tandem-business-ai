"""org registers the public slug resolver used by platform.public_context."""

from __future__ import annotations

from typing import Any

from app.modules.org.registration import resolve_public_business


def test_public_slug_lookup_works_without_tenant(business: Any) -> None:
    assert resolve_public_business("example-cycle-works") == business
    assert resolve_public_business("missing-slug") is None
