"""Call-scoped accounting, preserved through routing decorators without mutating shared routers/adapters.

The runner opens the scope. The gateway enters it separately for each provider attempt, including fallbacks.
Standalone gateway probes/tests have no business context and remain outside application budget accounting.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.platform.context import Context

from .gateway.routes import RouteEntry
from .gateway.types import Adapter, ChatRequest, ChatResult, TranscriptionRequest
from .reservations import ReservedAdapter
from .schemas import Policy

_context: ContextVar[Context | None] = ContextVar("ai_call_budget_context", default=None)
_guard: ContextVar[Callable[[], None] | None] = ContextVar("ai_call_guard", default=None)
_policy: ContextVar[Callable[[], Policy] | None] = ContextVar("ai_call_policy", default=None)


@contextmanager
def policy_scope(read: Callable[[], Policy]) -> Iterator[None]:
    """Preserve a fresh policy reader through routing decorators and reset it after this call."""
    token = _policy.set(read)
    try:
        yield
    finally:
        _policy.reset(token)


def attempt_policy(default: Policy) -> Policy:
    read = _policy.get()
    return read() if read is not None else default


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


def complete_attempt(
    adapter: Adapter,
    entry: RouteEntry,
    request: ChatRequest | TranscriptionRequest,
    *,
    check_policy: Callable[[], None] | None = None,
) -> ChatResult:
    guard = _guard.get()
    if guard is not None:
        guard()
    ctx = _context.get()
    if ctx is None:
        if check_policy is not None:
            check_policy()
        return invoke(adapter, request)

    def check() -> None:
        if guard is not None:
            guard()
        if check_policy is not None:
            check_policy()

    return ReservedAdapter(ctx, _GuardedAdapter(adapter, check), [entry]).chat(request)


class _GuardedAdapter:
    """Check again after potentially waiting for budget locks, immediately before inference."""

    def __init__(self, inner: Adapter, check: Callable[[], None]) -> None:
        self.name, self.inner, self.check = inner.name, inner, check

    def chat(self, request: ChatRequest | TranscriptionRequest) -> ChatResult:
        self.check()
        return invoke(self.inner, request)

    transcribe = chat


def invoke(adapter: Adapter, request: ChatRequest | TranscriptionRequest) -> ChatResult:
    if isinstance(request, TranscriptionRequest):
        method = getattr(adapter, "transcribe", None)
        if method is None:
            from .gateway.types import ProviderError

            raise ProviderError("bad_request", "provider does not support transcription")
        result: ChatResult = method(request)
        return result
    return adapter.chat(request)
