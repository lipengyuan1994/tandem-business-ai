"""Signatures between this install and the payments relay. Same scheme as app/relay/signing.py (a test checks
that the two agree): `Tandem-Relay-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>." + signed bytes>`.
Requests sign `<METHOD>\\n<path>\\n<nonce>\\n<body>`; forwarded events sign the body. Five minutes of tolerance.
"""

from __future__ import annotations

import hashlib
import hmac

TOLERANCE_SECONDS = 300
CLIENT_HEADER = "Tandem-Relay-Client"
SIGNATURE_HEADER = "Tandem-Relay-Signature"
NONCE_HEADER = "Tandem-Relay-Nonce"


def request_payload(method: str, path: str, nonce: str, body: bytes) -> bytes:
    return f"{method.upper()}\n{path}\n{nonce}\n".encode() + body


def sign(secret: str, timestamp: int, payload: bytes) -> str:
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify(secret: str, header: str, payload: bytes, now: int) -> bool:
    timestamp: int | None = None
    candidates: list[str] = []
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates or abs(now - timestamp) > TOLERANCE_SECONDS:
        return False
    expected = sign(secret, timestamp, payload).split("v1=", 1)[1]
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)
