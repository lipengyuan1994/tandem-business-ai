"""Call-scoped accounting, preserved through routing decorators without mutating shared routers/adapters.

The runner opens the scope. The gateway enters it separately for each provider attempt, including fallbacks.
Standalone gateway probes/tests have no business context and remain outside application budget accounting.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.platform.context import Context

from .gateway.routes import RouteEntry
from .gateway.types import Adapter, ChatRequest, ChatResult
from .reservations import ReservedAdapter

_context: ContextVar[Context | None] = ContextVar("ai_call_budget_context", default=None)
_guard: ContextVar[Callable[[], None] | None] = ContextVar("ai_call_guard", default=None)


@contextmanager
def call_guard(check: Callable[[], None]) -> Iterator[None]:
    token = _guard.set(check)
    try:
        yield
    finally:
        _guard.reset(token)


@contextmanager
def budget_scope(ctx: Context) -> Iterator[None]:
    token = _context.set(ctx)
    try:
        yield
    finally:
        _context.reset(token)


def complete_attempt(adapter: Adapter, entry: RouteEntry, request: ChatRequest) -> ChatResult:
    guard = _guard.get()
    if guard is not None:
        guard()
    ctx = _context.get()
    if ctx is None:
        return adapter.chat(request)
    return ReservedAdapter(ctx, _GuardedAdapter(adapter, guard) if guard else adapter, [entry]).chat(request)


class _GuardedAdapter:
    """Check again after potentially waiting for budget locks, immediately before inference."""

    def __init__(self, inner: Adapter, check: Callable[[], None]) -> None:
        self.name, self.inner, self.check = inner.name, inner, check

    def chat(self, request: ChatRequest) -> ChatResult:
        self.check()
        return self.inner.chat(request)
