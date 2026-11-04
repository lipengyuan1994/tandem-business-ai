"""Channel adapters.

Contract: send(message, idempotency_key) -> Delivered | RetryableError | PermanentError | Unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Delivered:
    provider_message_id: str


@dataclass(frozen=True)
class RetryableError:
    error: str


@dataclass(frozen=True)
class PermanentError:
    error: str


@dataclass(frozen=True)
class Unknown:
    """The request may have reached the provider (e.g. timeout after sending). Never retried automatically."""

    error: str


Result = Delivered | RetryableError | PermanentError | Unknown


@dataclass(frozen=True)
class RenderedMessage:
    channel: str
    recipient: str | None
    subject: str | None
    body: str
    html: str | None = None
    reply_to: str | None = None
    unsubscribe_url: str | None = None


class Adapter(Protocol):
    def send(self, message: RenderedMessage, idempotency_key: str) -> Result: ...


class SimulatedAdapter:
    """MVP: always 'delivers'. The UI labels these messages as Simulated."""

    def send(self, message: RenderedMessage, idempotency_key: str) -> Result:
        return Delivered(provider_message_id=f"simulated-{idempotency_key[-24:]}")


class WebChatAdapter:
    """Web chat delivery = the message becomes visible in the visitor's thread (conversations owns the message)."""

    def send(self, message: RenderedMessage, idempotency_key: str) -> Result:
        return Delivered(provider_message_id=f"web-chat-{idempotency_key[-24:]}")


class SmsAdapter:
    def send(self, message: RenderedMessage, idempotency_key: str) -> Result:
        return PermanentError("SMS delivery is not built")


class SmtpAdapter:
    def send(self, message: RenderedMessage, idempotency_key: str) -> Result:
        from .smtp_adapter import SmtpAdapter as Transport

        return Transport().send(message, idempotency_key)


ADAPTERS: dict[str, Adapter] = {
    "simulated": SimulatedAdapter(),
    "web_chat": WebChatAdapter(),
    "email": SmtpAdapter(),
    "sms": SmsAdapter(),
}
