"""Signed quote1 capabilities, bound to one business, request and supplier. Never log tokens."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import uuid
from datetime import datetime

from app.platform import clock
from app.platform.config import get_settings
from app.platform.errors import Invalid


def make_token(business_id: uuid.UUID, request_id: uuid.UUID, supplier_id: uuid.UUID, expires_at: datetime) -> str:
    payload = f"quote1|{business_id}|{request_id}|{supplier_id}|{int(expires_at.timestamp())}".encode()
    key = get_settings().app_secret_key.get_secret_value().encode()
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signed = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded}.{signed}"


def read_token(token: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    try:
        if len(token) > 600:
            raise ValueError("too long")
        encoded, signed = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        signature = base64.urlsafe_b64decode(signed + "=" * (-len(signed) % 4))
        key = get_settings().app_secret_key.get_secret_value().encode()
        if not hmac.compare_digest(signature, hmac.new(key, payload, hashlib.sha256).digest()):
            raise ValueError("signature")
        purpose, business, request, supplier, expires = payload.decode().split("|")
        if purpose != "quote1" or clock.now().timestamp() >= int(expires):
            raise ValueError("purpose or expiry")
        return uuid.UUID(business), uuid.UUID(request), uuid.UUID(supplier)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise Invalid("purchasing.invalid_quote_token") from exc


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
