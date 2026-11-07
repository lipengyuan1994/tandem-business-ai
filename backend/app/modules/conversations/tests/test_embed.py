from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.org.testing import make_business
from app.platform.registry import Registry


def test_embed_token_no_cookie_origin_and_first_party_cookie_unchanged(registry: Registry) -> None:
    make_business("example-cycle-works")
    make_business("example-garden-store")
    client = TestClient(create_app(registry), base_url="https://localhost:8443")
    base = "/api/v1/public/example-cycle-works"
    body = {"name": "Ben Example", "email": "ben@example.com", "message": "Hello"}
    assert client.post(base + "/embed/chat", json=body).status_code == 403
    assert client.post(base + "/embed/chat", json=body, headers={"Origin": "https://shop.example"}).status_code == 403
    r = client.post(base + "/embed/chat", json=body, headers={"Origin": "https://localhost:8443"})
    assert r.status_code == 201 and "set-cookie" not in r.headers
    token = r.headers["x-embed-token"]
    assert token not in r.text
    assert client.get(base + "/embed/chat").status_code == 403
    assert client.get(base + "/embed/chat", headers={"X-Embed-Token": token}).status_code == 200
    assert (
        client.get("/api/v1/public/example-garden-store/embed/chat", headers={"X-Embed-Token": token}).status_code
        == 403
    )
    assert (
        client.post(
            base + "/embed/chat/messages", json={"body": "Another"}, headers={"X-Embed-Token": token}
        ).status_code
        == 403
    )
    headers = {"X-Embed-Token": token, "Origin": "https://localhost:8443"}
    assert client.post(base + "/embed/chat/messages", json={"body": "Another"}, headers=headers).status_code == 201
    assert client.post(base + "/embed/chat/handoff", headers=headers).status_code == 200
    r = client.post(base + "/chat", json={**body, "name": f"Example {uuid.uuid4()}"})
    cookie = r.headers["set-cookie"]
    assert all(
        v in cookie
        for v in ["smb_visitor=", "SameSite=lax", "Secure", "HttpOnly", "Path=/api/v1/public/example-cycle-works/"]
    )
