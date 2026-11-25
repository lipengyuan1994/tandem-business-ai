"""Sealing of bookkeeping OAuth tokens at rest (JWE dir + A256GCM, key derived from APP_SECRET_KEY).

Rotating APP_SECRET_KEY makes stored tokens unreadable; the connection then asks the owner to reconnect.
Never log a token or a sealed value.
"""

from __future__ import annotations

import hashlib

from joserfc import jwe
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

from app.platform.config import get_settings

_HEADER = {"alg": "dir", "enc": "A256GCM"}


class UnsealError(ValueError):
    pass


def _key() -> OctKey:
    secret = get_settings().app_secret_key.get_secret_value().encode()
    return OctKey.import_key(hashlib.sha256(b"tandem-accounting-token-v1|" + secret).digest())


def seal(value: str) -> str:
    return jwe.encrypt_compact(_HEADER, value.encode(), _key())


def unseal(sealed: str) -> str:
    try:
        plaintext = jwe.decrypt_compact(sealed, _key()).plaintext
    except (JoseError, ValueError) as exc:
        raise UnsealError("stored token cannot be read") from exc
    if plaintext is None:
        raise UnsealError("stored token is empty")
    return plaintext.decode()
