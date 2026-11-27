"""Client secrets at rest: JWE dir + A256GCM with a key derived from RELAY_SECRET_KEY."""

from __future__ import annotations

import hashlib

from joserfc import jwe
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

_HEADER = {"alg": "dir", "enc": "A256GCM"}


class UnsealError(ValueError):
    pass


def _key(secret_key: str) -> OctKey:
    return OctKey.import_key(hashlib.sha256(b"tandem-relay-client-secret-v1|" + secret_key.encode()).digest())


def seal(secret_key: str, value: str) -> str:
    return jwe.encrypt_compact(_HEADER, value.encode(), _key(secret_key))


def unseal(secret_key: str, sealed: str) -> str:
    try:
        plaintext = jwe.decrypt_compact(sealed, _key(secret_key)).plaintext
    except (JoseError, ValueError) as exc:
        raise UnsealError("stored client secret cannot be read") from exc
    if plaintext is None:
        raise UnsealError("stored client secret is empty")
    return plaintext.decode()
