"""Signatures between Tandem installs and the relay (the same scheme is implemented in app.modules.payments.relay).

Header `Tandem-Relay-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>`. The signed bytes are `<t>.` followed by:
- for a request from Tandem to the relay: `<METHOD>\n<path>\n<nonce>\n<body>`;
- for an event from the relay to Tandem: the body.
Signatures older or newer than five minutes are refused; the relay also refuses a request nonce seen before.
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
    timestamp, signatures = parse(header)
    if timestamp is None or not signatures or abs(now - timestamp) > TOLERANCE_SECONDS:
        return False
    expected = sign(secret, timestamp, payload).split("v1=", 1)[1]
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


def parse(header: str) -> tuple[int | None, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            signatures.append(value)
    return timestamp, signatures
