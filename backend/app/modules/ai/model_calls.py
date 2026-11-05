"""Call-scoped accounting, preserved through routing decorators without mutating shared routers/adapters.

The runner opens the scope. The gateway enters it separately for each provider attempt, including fallbacks.
Standalone gateway probes/tests have no business context and remain outside application budget accounting.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.platform.context import Context

from .gateway.routes import RouteEntry
from .gateway.types import Adapter, ChatRequest, ChatResult
from .reservations import ReservedAdapter

_context: ContextVar[Context | None] = ContextVar("ai_call_budget_context", default=None)


@contextmanager
def budget_scope(ctx: Context) -> Iterator[None]:
    token = _context.set(ctx)
    try:
        yield
    finally:
        _context.reset(token)


def complete_attempt(adapter: Adapter, entry: RouteEntry, request: ChatRequest) -> ChatResult:
    ctx = _context.get()
    if ctx is None:
        return adapter.chat(request)
    return ReservedAdapter(ctx, adapter, [entry]).chat(request)
