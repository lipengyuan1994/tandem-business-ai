"""Local SMTP protocol and outbox integration; no provider credentials or external network."""

from __future__ import annotations

import asyncio
import base64
import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from typing import Any

import pytest
from aiosmtpd.controller import Controller
from sqlalchemy import Engine, text

from app.modules.notifications import adapters, service
from app.modules.notifications.models import OutboxMessage
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.config import Settings, get_settings
from app.platform.context import system_context
from app.platform.db import transaction


class Mailbox:
    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self.response = "250 accepted"
        self.drop = False
        self.delay = 0.0

    async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:
        self.messages.append(envelope.original_content)
        await asyncio.sleep(self.delay)
        if self.drop:
            server.transport.close()
            await asyncio.sleep(0)
        return self.response


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch, registry: Any) -> Iterator[tuple[Mailbox, str]]:
    mailbox = Mailbox()
    # Reserve a random loopback port; Controller cannot discover port=0 for its readiness probe.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    controller = Controller(mailbox, hostname="127.0.0.1", port=port)
    controller.start()
    url = f"smtp://127.0.0.1:{port}?starttls=false&from=Shop%20Name%20%3Cno-reply@example.com%3E"
    monkeypatch.setattr(get_settings(), "smtp_url", url)
    try:
        yield mailbox, url
    finally:
        controller.stop()


def enqueue(
    *, marketing: bool = False, unsubscribe_url: str = "https://example.com/unsubscribe/token"
) -> tuple[Any, Any]:
    ctx = system_context(make_business())
    with transaction(ctx) as db:
        outbox_id = service.enqueue_message(
            db,
            ctx,
            channel="email",
            recipient="ada@example.com",
            template_key="marketing_campaign" if marketing else "test_message",
            variables={
                "name": "<Ada>",
                "subject": "News",
                "body": "<script>bad</script>",
                "business_name": "Shop",
                "unsubscribe_url": unsubscribe_url,
            },
            idempotency_key="stable-secret-free-key",
        )
    return ctx, outbox_id


def advance_to_retry(ctx: Any, outbox_id: uuid.UUID, freeze_time: Any) -> None:
    """Respect the real retry schedule rather than bypassing the claim's due-time guard."""
    with transaction(ctx) as db:
        message = db.get(OutboxMessage, outbox_id)
        assert message is not None
        # These are manual messages: there is no AI send delay. Only retry backoff applies.
        assert message.not_before is None
        assert (message.status, message.attempts) == ("pending", 1)
        due = message.next_attempt_at
        assert due == clock.now() + service.BACKOFF[0]
    freeze_time(due - timedelta(microseconds=1))
    assert service.deliver(ctx.business_id, outbox_id) == "skipped"
    with transaction(ctx) as db:
        message = db.get(OutboxMessage, outbox_id)
        assert message is not None
        assert (message.status, message.attempts, message.next_attempt_at) == ("pending", 1, due)
    freeze_time(due)


def test_multipart_headers_escaping_and_stable_id(smtp: Any, owner_engine: Engine, freeze_time: Any) -> None:
    freeze_time(datetime(2026, 11, 5, 17, 0, tzinfo=UTC))
    box, _ = smtp
    box.response = "451 try later"
    ctx, oid = enqueue()
    assert service.deliver(ctx.business_id, oid) == "retry"
    box.response = "250 accepted"
    advance_to_retry(ctx, oid, freeze_time)
    assert len(box.messages) == 1  # the early claim did not reach SMTP
    assert service.deliver(ctx.business_id, oid) == "delivered"
    first, second = [BytesParser(policy=policy.default).parsebytes(raw) for raw in box.messages]
    assert first["Message-ID"] == second["Message-ID"]
    assert second["Date"] and str(second["From"]) == "Shop Name <no-reply@example.com>"
    assert second["To"] == "ada@example.com"
    assert second.get_body(preferencelist=("plain",)).get_content().strip() == "Hello <Ada>"
    html = second.get_body(preferencelist=("html",)).get_content()
    assert "&lt;Ada&gt;" in html and "<Ada>" not in html and "<img" not in html
    with owner_engine.connect() as conn:
        assert conn.execute(text("select result from delivery_attempts order by started_at")).scalars().all() == [
            "retry",
            "delivered",
        ]


@pytest.mark.parametrize(
    ("response", "drop", "outcome", "state"),
    [
        ("451 later", False, "retry", "pending"),
        ("550 rejected", False, "failed", "failed"),
        ("250 accepted", True, "unknown", "unknown"),
        ("25", False, "unknown", "unknown"),  # truncated final acknowledgement
        ("garbled", False, "unknown", "unknown"),
        ("354 unexpected", False, "unknown", "unknown"),
        ("251 unexpected", False, "unknown", "unknown"),
        ("399 unexpected", False, "unknown", "unknown"),
        ("600 unexpected", False, "unknown", "unknown"),
    ],
)
def test_smtp_outbox_states(
    smtp: Any, owner_engine: Engine, response: str, drop: bool, outcome: str, state: str
) -> None:
    box, _ = smtp
    box.response, box.drop = response, drop
    ctx, oid = enqueue()
    assert service.deliver(ctx.business_id, oid) == outcome
    with owner_engine.connect() as conn:
        assert conn.execute(text("select status from outbox_messages where id=:id"), {"id": oid}).scalar_one() == state
    if state == "unknown":
        assert service.deliver(ctx.business_id, oid) == "skipped"
        assert len(box.messages) == 1


def test_marketing_unsubscribe(smtp: Any) -> None:
    box, _ = smtp
    # Production token size: two UUIDs in the unsub1 payload and a 32-byte HMAC-SHA256 signature.
    # A synthetic signature suffices here: SMTP transports the URL without validating the token.
    payload = f"unsub1|{uuid.UUID(int=1)}|{uuid.UUID(int=2)}|email".encode()
    token = (
        base64.urlsafe_b64encode(payload).rstrip(b"=") + b"." + base64.urlsafe_b64encode(b"x" * 32).rstrip(b"=")
    ).decode()
    assert len(token) == 159
    url = f"https://app.example.com/api/v1/public/example-cycle-works/unsubscribe?token={token}"
    ctx, oid = enqueue(marketing=True, unsubscribe_url=url)
    assert service.deliver(ctx.business_id, oid) == "delivered"
    headers = box.messages[0].partition(b"\r\n\r\n")[0] + b"\r\n"
    # Assert wire bytes: parsing alone would silently decode illegal RFC 2047 encoded words.
    assert f"List-Unsubscribe: <{url}>\r\n".encode("ascii") in headers
    assert b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n" in headers
    mail = BytesParser(policy=policy.default).parsebytes(box.messages[0])
    assert mail["List-Unsubscribe"] == f"<{url}>"
    assert mail["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_credentials_never_logged(smtp: Any, monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    _, url = smtp
    monkeypatch.setattr(get_settings(), "smtp_url", url.replace("smtp://", "smtp://test-user:private-password@"))
    caplog.set_level("INFO")
    ctx, oid = enqueue()
    assert service.deliver(ctx.business_id, oid) == "failed"  # local server has no AUTH
    assert "test-user" not in caplog.text and "private-password" not in caplog.text
    assert "127.0.0.1" in caplog.text


@pytest.mark.parametrize(
    "url",
    [
        "http://mail.example.com",
        "smtp://bad host:587",
        "smtp://mail.example.com:0",
        "smtp://mail.example.com:70000",
        "smtp://u:%FF@mail.example.com:587",
        "smtp://mail.example.com?from=first@example.com&from=second@example.com",
        "smtps://mail.example.com?starttls=false",
        "smtp://mail.example.com:587?starttls=false",
        "smtp://user@mail.example.com:587",
        "smtp://mail.example.com:abc",
        "smtp://mail.example.com?starttls=maybe",
        "smtp://mail.example.com?from=a%0D%0ABcc:bad@example.com",
        "smtp://u:p@mail.example.com?unknown=1",
    ],
)
def test_invalid_settings_rejected_without_secret(url: str) -> None:
    with pytest.raises(ValueError) as exc:
        Settings(smtp_url=url, smtp_from="no-reply@example.com")
    assert "SMTP" in str(exc.value) and url not in str(exc.value)


def test_sms_refuses() -> None:
    assert isinstance(
        adapters.ADAPTERS["sms"].send(adapters.RenderedMessage("sms", None, None, "x"), "x"), adapters.PermanentError
    )


def test_timeout_after_data_is_unknown(smtp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.notifications import smtp_adapter

    box, _ = smtp
    box.delay = 0.3
    monkeypatch.setattr(smtp_adapter, "TIMEOUT", 0.1)
    ctx, oid = enqueue()
    assert service.deliver(ctx.business_id, oid) == "unknown"


def test_public_business_email_reply_to(smtp: Any, owner_engine: Engine) -> None:
    box, _ = smtp
    ctx, oid = enqueue()
    with owner_engine.begin() as conn:
        conn.execute(
            text("update businesses set public_profile = :profile where id = :id"),
            {"id": ctx.business_id, "profile": '{"email":"shop@example.com"}'},
        )
    assert service.deliver(ctx.business_id, oid) == "delivered"
    mail = BytesParser(policy=policy.default).parsebytes(box.messages[0])
    assert mail["Reply-To"] == "shop@example.com" and mail["List-Unsubscribe"] is None


def test_connection_error_before_data_is_retryable(smtp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import smtplib

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionRefusedError("private-password must not persist")

    monkeypatch.setattr(smtplib, "SMTP", fail)
    ctx, oid = enqueue()
    assert service.deliver(ctx.business_id, oid) == "retry"


@pytest.mark.parametrize(
    ("url", "implicit", "starttls"),
    [
        ("smtp://mail.example.com:587", False, True),
        ("smtp://mail.example.com:465", True, False),
        ("smtps://mail.example.com", True, False),
        ("smtp://localhost:2525?starttls=false", False, False),
    ],
)
def test_transport_configuration(url: str, implicit: bool, starttls: bool) -> None:
    from app.platform.smtp import parse_smtp_url

    config = parse_smtp_url(url, "Shop <no-reply@example.com>")
    assert config and config.implicit_tls == implicit and config.starttls == starttls


@pytest.fixture
def tls_material(tmp_path: Any) -> tuple[Any, Any]:
    """Ephemeral test CA/server certificate; no external service or downloaded certificate."""
    import subprocess

    cert, key, config = tmp_path / "cert.pem", tmp_path / "key.pem", tmp_path / "openssl.cnf"
    config.write_text(
        "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n"
        "[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n"
    )
    subprocess.run(  # noqa: S603 - fixed system tool, ephemeral synthetic test certificate
        [
            "/usr/bin/openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-config",
            str(config),
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@pytest.mark.parametrize("implicit", [False, True])
def test_real_tls_verification_and_delivery(
    tls_material: Any, monkeypatch: pytest.MonkeyPatch, registry: Any, implicit: bool, freeze_time: Any
) -> None:
    import ssl

    from app.modules.notifications import smtp_adapter

    freeze_time(datetime(2026, 11, 5, 17, 0, tzinfo=UTC))
    cert, key = tls_material
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    box = Mailbox()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    controller = Controller(
        box,
        hostname="127.0.0.1",
        port=port,
        **({"ssl_context": server_context} if implicit else {"tls_context": server_context, "require_starttls": True}),
    )
    controller.start()
    scheme = "smtps" if implicit else "smtp"
    monkeypatch.setattr(get_settings(), "smtp_url", f"{scheme}://127.0.0.1:{port}?from=no-reply@example.com")
    try:
        ctx, oid = enqueue()
        # An unknown certificate must be rejected before any message reaches the server.
        assert service.deliver(ctx.business_id, oid) == "retry"
        assert box.messages == []
        advance_to_retry(ctx, oid, freeze_time)
        assert box.messages == []  # an early claim cannot bypass TLS verification or backoff
        # Trust only the ephemeral test CA, preserving hostname verification and CERT_REQUIRED.
        context = ssl.create_default_context(cafile=str(cert))
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        monkeypatch.setattr(smtp_adapter.ssl, "create_default_context", lambda: context)
        assert service.deliver(ctx.business_id, oid) == "delivered"
        assert len(box.messages) == 1
    finally:
        controller.stop()
