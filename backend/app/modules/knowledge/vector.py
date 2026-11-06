"""Minimal pgvector wire type; no Python pgvector dependency or connection-global adapters."""

import math
from collections.abc import Callable
from typing import Any

from sqlalchemy.engine import Dialect
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[list[float]]):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector(1024)"

    def bind_processor(self, dialect: Dialect) -> Callable[[list[float] | None], str | None]:
        def process(value: list[float] | None) -> str | None:
            if value is None:
                return None
            if len(value) != 1024 or any(not math.isfinite(v) or abs(v) > 3.4e38 for v in value) or not any(value):
                raise ValueError("expected 1024 finite nonzero embedding coordinates")
            return "[" + ",".join(str(float(v)) for v in value) + "]"

        return process

    def result_processor(self, dialect: Dialect, coltype: object) -> Callable[[Any], list[float] | None]:
        def process(value: Any) -> list[float] | None:
            if value is None:
                return None
            return [float(v) for v in value.strip("[]").split(",")] if isinstance(value, str) else list(value)

        return process
