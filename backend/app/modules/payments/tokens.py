"""Signed pay1 capabilities: one invoice's pay link. Never log tokens.

The token names the business, the invoice and the link row, so it can be made again for "Copy pay link" without
storing it, and a reset (a new link row) stops the old one working. Only its hash is stored.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import uuid

from app.platform.config import get_settings
from app.platform.errors import NotFound

PURPOSE = "pay1"


def _key() -> bytes:
    # A key of its own, derived from the install secret, so a pay token can never pass as another capability.
    secret = get_settings().app_secret_key.get_secret_value().encode()
    return hmac.new(secret, b"tandem-payments-pay-link-v1", hashlib.sha256).digest()


def make_token(business_id: uuid.UUID, invoice_id: uuid.UUID, link_id: uuid.UUID) -> str:
    payload = f"{PURPOSE}|{business_id}|{invoice_id}|{link_id}".encode()
    signature = hmac.new(_key(), payload, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signed = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded}.{signed}"


def read_token(token: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(business, invoice, link). Any malformed or forged token is simply not found."""
    try:
        if len(token) > 400:
            raise ValueError("too long")
        encoded, signed = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        signature = base64.urlsafe_b64decode(signed + "=" * (-len(signed) % 4))
        if not hmac.compare_digest(signature, hmac.new(_key(), payload, hashlib.sha256).digest()):
            raise ValueError("signature")
        purpose, business, invoice, link = payload.decode().split("|")
        if purpose != PURPOSE:
            raise ValueError("purpose")
        return uuid.UUID(business), uuid.UUID(invoice), uuid.UUID(link)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise NotFound("payments.link_not_found") from exc


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
