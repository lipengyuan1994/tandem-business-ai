"""Router: picks provider and model per model class from the route file, honours the routing policy, falls back on
transient errors (contracts §6, architecture §8). Nothing ever falls back to a provider the policy excludes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform.config import get_settings
from app.platform.context import Context

from ..model_calls import attempt_policy, complete_attempt
from ..schemas import Policy
from ..settings import API_KEY_ENVS, ai_settings, api_key
from .anthropic import AnthropicAdapter
from .fake import ScriptFileAdapter
from .local_address import Resolver, resolve_host, verify_local_url
from .openai_compat import OpenAICompatAdapter
from .routes import Limits, ModelClass, ProviderCfg, RouteEntry, RoutesFile, load_routes_file
from .types import Adapter, ChatRequest, ChatResult, JsonMode, ProviderError, TranscriptionRequest

log = structlog.get_logger(__name__)
_monotonic = time.monotonic


class PolicyBlocked(Exception):
    """No route for this model class is allowed by the policy (or has a usable provider)."""

    def __init__(self, model_class: str, policy: str) -> None:
        super().__init__(f"no allowed route for {model_class} under policy {policy}")
        self.model_class = model_class
        self.policy = policy


class FakeProviderInProduction(RuntimeError):
    """A route file with a `fake` provider was loaded with APP_ENV=prod."""


def policy_allows(cfg: ProviderCfg, policy: Policy) -> bool:
    if policy == "regulated":
        return not cfg.hosted and cfg.vendor == "local"
    if cfg.kind == "fake":  # dev and e2e only; refused in production
        return True
    if policy == "hosted_vultr":
        return cfg.vendor in ("vultr", "local")
    if policy == "hosted_any":
        return True
    if policy == "strict_private":
        return not cfg.hosted
    return False  # unknown policy: fail closed


def effective_policy(db: Session, ctx: Context) -> Policy:
    """`regulated` when DATA_PROFILE=regulated (forced for every business), else the business's `ai_policy`."""
    if ai_settings().regulated:
        return "regulated"
    return org.get_settings(db, ctx).ai_policy


def json_mode_for(entry: RouteEntry, req: ChatRequest) -> JsonMode:
    if req.use_case == "dictation_polish":
        return "json_object"
    if req.json_schema is None:
        return "text"
    return "json_schema" if entry.json_schema else "json_object"


class Router:
    def __init__(self, routes: RoutesFile, adapters: dict[str, Adapter], *, resolver: Resolver | None = None) -> None:
        self.routes = routes
        self.adapters = adapters
        self.limits: Limits = routes.limits
        self._resolver = resolver or resolve_host
        self._local_lock = threading.Lock()
        self._regulated_local: set[str] | None = None
        if ai_settings().regulated:
            self._verify_regulated_providers()

    def _verify_regulated_providers(self) -> set[str]:
        with self._local_lock:
            if self._regulated_local is None:
                allowed: set[str] = set()
                for name, cfg in self.routes.providers.items():
                    if "baa" in cfg.model_fields_set:
                        log.warning("ai_routes_baa_ignored", provider=name)
                    if cfg.hosted or cfg.vendor != "local":
                        continue
                    valid, host = verify_local_url(cfg.base_url, self._resolver)
                    if valid:
                        allowed.add(name)
                    else:
                        log.warning("ai_local_provider_public_address", host=host)
                self._regulated_local = allowed
            return self._regulated_local

    def _allows(self, entry: RouteEntry, policy: Policy) -> bool:
        if entry.provider not in self.adapters or not policy_allows(self.routes.providers[entry.provider], policy):
            return False
        return policy != "regulated" or entry.provider in self._verify_regulated_providers()

    def allowed(self, model_class: ModelClass, policy: Policy) -> list[RouteEntry]:
        """Entries of this class, in order, whose provider has an adapter (key present) and passes the policy."""
        return [
            entry
            for entry in self.routes.routes.get(model_class, [])
            if self._allows(entry, policy)
            and (model_class != "stt" or hasattr(self.adapters[entry.provider], "transcribe"))
        ]

    def complete(self, model_class: ModelClass, policy: Policy, req: ChatRequest) -> tuple[ChatResult, RouteEntry]:
        return self._complete(model_class, policy, req)

    def transcribe(self, policy: Policy, req: TranscriptionRequest) -> tuple[ChatResult, RouteEntry]:
        return self._complete("stt", policy, req)

    def _complete(
        self, model_class: ModelClass, policy: Policy, req: ChatRequest | TranscriptionRequest
    ) -> tuple[ChatResult, RouteEntry]:
        """Fill model and json_mode per entry; try allowed entries in order; on a transient ProviderError try the next;
        non-transient → raise it. No allowed entry → PolicyBlocked. All failed → ProviderError("unavailable")."""
        entries = self.routes.routes.get(model_class, [])
        last: ProviderError | None = None
        started = _monotonic()
        deadline = started + req.timeout_s
        for index, entry in enumerate(entries):
            policy = attempt_policy(policy)
            if not self._allows(entry, policy) or entry not in self.allowed(model_class, policy):
                continue
            remaining = deadline - (started if index == 0 else _monotonic())
            if remaining <= 0:
                raise ProviderError("timeout", "request deadline exceeded")
            attempt = req.model_copy(
                update={
                    "model": entry.model,
                    "json_mode": json_mode_for(entry, req) if isinstance(req, ChatRequest) else "text",
                    "timeout_s": remaining,
                }
            )

            def check_policy(entry: RouteEntry = entry, policy: Policy = policy) -> None:
                current = attempt_policy(policy)
                if not self._allows(entry, current):
                    raise PolicyBlocked(model_class, current)

            try:
                result = complete_attempt(self.adapters[entry.provider], entry, attempt, check_policy=check_policy)
                # A call already in flight cannot be recalled. Discard its result if policy tightened meanwhile.
                check_policy()
            except PolicyBlocked:
                continue
            except ProviderError as exc:
                if not self._allows(entry, attempt_policy(policy)):
                    continue
                if not exc.transient:
                    raise
                log.warning(
                    "ai_route_fallback", model_class=model_class, provider=entry.provider, model=entry.model,
                    kind=exc.kind,
                )  # fmt: skip
                last = exc
                continue
            if _monotonic() >= deadline:
                raise ProviderError("timeout", "request deadline exceeded")
            return result, entry
        policy = attempt_policy(policy)
        if not self.allowed(model_class, policy):
            raise PolicyBlocked(model_class, policy)
        raise ProviderError(
            "unavailable",
            f"all {len(self.allowed(model_class, policy))} route(s) failed",
            last.retry_after_s if last is not None else None,
        )


def build_adapters(routes: RoutesFile) -> dict[str, Adapter]:
    """One adapter per provider whose key is present. `hosted: false` → local client. `fake` refused in prod."""
    settings = ai_settings()
    adapters: dict[str, Adapter] = {}
    for name, cfg in routes.providers.items():
        if cfg.kind == "fake":
            if get_settings().is_prod:
                raise FakeProviderInProduction(f"provider {name!r} has kind fake, which is refused when APP_ENV=prod")
            if settings.fake_script_file is None:
                log.warning("ai_provider_skipped", provider=name, reason="AI_FAKE_SCRIPT_FILE is empty")
                continue
            adapters[name] = ScriptFileAdapter(name, settings.fake_script_file)
            continue
        key = api_key(cfg.api_key_env) if cfg.api_key_env else ""
        if cfg.api_key_env and not key:
            log.info("ai_provider_skipped", provider=name, reason="key variable is empty")
            continue
        if cfg.kind == "anthropic":
            adapters[name] = AnthropicAdapter(name, cfg.base_url, key or None)
        else:
            adapters[name] = OpenAICompatAdapter(name, cfg.base_url, key or None, local=not cfg.hosted)
    return adapters


# ------------------------------------------------------------------------------------------- process cache
_lock = threading.Lock()
_cache: tuple[Any, Router | None] | None = None
_override: Router | None = None


def _fingerprint() -> Any:
    """Everything the built router depends on, so a changed file or setting rebuilds it."""
    s = ai_settings()

    def stat(path: Any) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except (OSError, AttributeError):
            return None
        return (st.st_mtime_ns, st.st_size)

    return (
        str(s.routes_file),
        stat(s.routes_file),
        str(s.fake_script_file),
        stat(s.fake_script_file),
        tuple(sorted(e for e in API_KEY_ENVS if api_key(e))),
        tuple(sorted(s.local_inference_hosts)),
        get_settings().app_env,
        s.regulated,
    )


def load_router() -> Router | None:
    """Cached per process; None when the routes file is missing or invalid (AI disabled, `ai.not_configured`).
    Raises FakeProviderInProduction for a `fake` provider with APP_ENV=prod."""
    global _cache
    if _override is not None:
        return _override
    key = _fingerprint()
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        router: Router | None = None
        if key[1] is not None:
            try:
                routes = load_routes_file(ai_settings().routes_file)
                router = Router(routes, build_adapters(routes))
            except ValueError as exc:  # RoutesFileError, or an invalid fake script; FakeProviderInProduction propagates
                log.error("ai_routes_invalid", error=str(exc))
        _cache = (key, router)
        return router


def reset_router_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def set_router_for_tests(router: Router | None) -> None:
    """`load_router` returns this router while it is set; None clears it."""
    global _override
    _override = router


def check_routes_at_startup() -> None:
    """Refuse production fakes and emit regulated policy/address warnings before serving."""
    if get_settings().is_prod or ai_settings().regulated:
        load_router()


__all__ = [
    "FakeProviderInProduction",
    "PolicyBlocked",
    "Router",
    "check_routes_at_startup",
    "effective_policy",
    "load_router",
    "policy_allows",
    "reset_router_cache",
    "set_router_for_tests",
]
