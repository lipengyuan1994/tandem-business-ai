from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.modules.org import embed
from app.modules.org.schemas import BusinessSettings
from app.platform.registry import Registry


@pytest.mark.parametrize(
    "value",
    [
        "https://*.example.com",
        "https://example.com/",
        "https://a:b@example.com",
        "https://example.com?x=1",
        "https://example.com#x",
        "http://example.com",
        "https://example.com:99999",
        "https://example.com:0",
        "https://a.example\n.example",
        "https://a.example;bad",
        "https://example.com:",
        "null",
        "https://example.com?",
        "https://example.com#",
    ],
)
def test_invalid_origins(value: str) -> None:
    with pytest.raises(ValidationError):
        BusinessSettings(embed_allowed_origins=[value])


def test_origins_normalization_count_and_development(monkeypatch: pytest.MonkeyPatch) -> None:
    assert BusinessSettings(
        embed_allowed_origins=["https://EXAMPLE.com:443", "https://example.com"]
    ).embed_allowed_origins == ["https://example.com"]
    with pytest.raises(ValidationError):
        BusinessSettings(embed_allowed_origins=[f"https://host{i}.example" for i in range(11)])
    with pytest.raises(ValidationError):
        BusinessSettings(embed_allowed_origins=["http://localhost:3000"])
    assert embed.normalize_origin("http://localhost:3000", development=True) == "http://localhost:3000"
    assert embed.normalize_origin("http://localhost:80", development=True) == "http://localhost"
    assert embed.normalize_origin("http://localhost", development=True) == "http://localhost"
    with pytest.raises(ValueError):
        embed.normalize_origin("http://localhost:*", development=True)


def test_csp_per_business_unknown_and_empty(cycle: Any, garden: Any, client: Any, registry: Registry) -> None:
    staff = client(cycle)
    current = staff.get("/api/v1/org/settings").json()
    current["settings"].update(embed_allowed_origins=["https://shop.example:8443"], embed_button_label="Ask Example")
    assert staff.patch("/api/v1/org/settings", json=current).status_code == 200
    visitor = TestClient(create_app(registry), base_url="https://localhost:8443")
    for slug, ancestors in [
        ("example-cycle-works", "https://shop.example:8443"),
        ("example-garden-store", "'none'"),
        ("no-such-shop", "'none'"),
    ]:
        r = visitor.get(
            "/api/v1/public/embed-policy",
            params={"path": f"/embed/{slug}/chat"},
            headers={"X-Embed-CSP": "frame-ancestors *"},
        )
        assert r.status_code == 200
        assert f"frame-ancestors {ancestors};" in r.headers["content-security-policy"]
        assert r.headers["x-embed-csp"] == r.headers["content-security-policy"]
    good = visitor.get(
        "/api/v1/public/example-cycle-works/embed-config", headers={"Origin": "https://shop.example:8443"}
    )
    assert good.status_code == 200 and good.json()["label"] == "Ask Example"
    assert good.headers["access-control-allow-origin"] == "https://shop.example:8443"
    assert (
        visitor.get(
            "/api/v1/public/example-cycle-works/embed-config", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
