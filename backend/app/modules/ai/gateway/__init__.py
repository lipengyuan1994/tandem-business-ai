"""Model gateway: provider-agnostic chat types, provider adapters, the router and test doubles (contracts §5, §6)."""

from .types import (
    TRANSIENT_KINDS,
    Adapter,
    ChatMessage,
    ChatRequest,
    ChatResult,
    JsonMode,
    ProviderError,
    ProviderErrorKind,
    Usage,
)

__all__ = [
    "TRANSIENT_KINDS",
    "Adapter",
    "ChatMessage",
    "ChatRequest",
    "ChatResult",
    "JsonMode",
    "ProviderError",
    "ProviderErrorKind",
    "Usage",
]
