"""Identifiers: UUIDv7 for new rows (RFC 9562), deterministic UUIDv5 for fixture keys."""

from __future__ import annotations

import os
import time
import uuid

# Constant namespace so fixture keys map to the same UUID everywhere (seed, tests, evals).
FIXTURE_NAMESPACE = uuid.UUID("8f2b8a52-4f39-4c55-9a8e-6a1d1f5c2b10")


def new_id() -> uuid.UUID:
    """Time-ordered UUIDv7: 48-bit Unix milliseconds, version 7, RFC 4122 variant, 74 random bits."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits, we use 74
    rand_a = (rand >> 62) & 0xFFF  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def fixture_id(key: str) -> uuid.UUID:
    """Stable UUID for a fixture key such as 'cust-ada'."""
    return uuid.uuid5(FIXTURE_NAMESPACE, key)
