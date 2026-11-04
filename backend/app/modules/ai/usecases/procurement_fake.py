"""Procurement-only bindings for the literal dev fake script; never interpret live model replies as templates."""

from __future__ import annotations

import json
import re
from typing import Any

from ..gateway.fake import ScriptFileAdapter
from ..gateway.router import Router, load_router
from ..gateway.routes import ModelClass, RouteEntry
from ..gateway.types import ChatRequest, ChatResult, ProviderError
from ..schemas import Policy


class _ScriptRouter(Router):
    def __init__(self, delegate: Router, use_case: str, bindings: dict[str, Any]) -> None:
        self.delegate = delegate
        self.limits = delegate.limits
        self.use_case, self.bindings = use_case, bindings
        self.step = 0

    def allowed(self, model_class: ModelClass, policy: Policy) -> list[RouteEntry]:
        return self.delegate.allowed(model_class, policy)

    def complete(self, model_class: ModelClass, policy: Policy, req: ChatRequest) -> tuple[ChatResult, RouteEntry]:
        entries = self.delegate.allowed(model_class, policy)
        if entries and all(isinstance(self.delegate.adapters[e.provider], ScriptFileAdapter) for e in entries):
            req = req.model_copy(update={"use_case": "procurement"})
        result, entry = self.delegate.complete(model_class, policy, req)
        if isinstance(self.delegate.adapters[entry.provider], ScriptFileAdapter):
            try:
                value = json.loads(result.text)
                if isinstance(value, dict) and "procurement" in value:
                    value = value[self.use_case]
                if isinstance(value, dict) and set(value) == {"steps"}:
                    # A single replay plan avoids the shared adapter's cursor interleaving concurrent agent runs.
                    value = value["steps"][self.step]
                    self.step += 1
                value = self._bind(value)
            except (ValueError, KeyError, IndexError, TypeError):
                raise ProviderError("bad_request", "invalid procurement fake template") from None
            result = result.model_copy(update={"text": json.dumps(value, default=str)})
        return result, entry

    def _bind(self, value: Any) -> Any:
        if isinstance(value, str):
            match = re.fullmatch(r"\{\{([a-z_]+)\}\}", value)
            return self.bindings[match[1]] if match else value
        if isinstance(value, list):
            return [self._bind(item) for item in value]
        if isinstance(value, dict):
            return {key: self._bind(item) for key, item in value.items()}
        return value


def script_router(router: Router | None, use_case: str, bindings: dict[str, Any]) -> Router | None:
    """Select a flow inside the procurement script and fill exact placeholders from server facts.

    Run recording still uses use_case=procurement. Ordinary FakeGateway and live adapters are returned unchanged.
    Production startup already refuses the script-file fake provider, as required by the gateway contract.
    """
    selected = router or load_router()
    if selected is not None and any(isinstance(a, ScriptFileAdapter) for a in selected.adapters.values()):
        return _ScriptRouter(selected, use_case, bindings)
    return selected
