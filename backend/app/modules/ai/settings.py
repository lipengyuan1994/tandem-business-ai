"""AI settings (contracts §2), read from `app.platform.config`. Never log or return the key values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.platform.config import get_settings, is_regulated

# The only environment variables a route file's `api_key_env` may name. Anything else (for example
# APP_SECRET_KEY) resolves to "", so a route file can never send another secret to a provider.
API_KEY_ENVS: frozenset[str] = frozenset(
    {"VULTR_INFERENCE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"}
)


@dataclass(frozen=True)
class AiSettings:
    routes_file: Path
    fake_script_file: Path | None
    daily_token_budget_per_business: int
    local_inference_hosts: frozenset[str]
    regulated: bool


def ai_settings() -> AiSettings:
    s = get_settings()
    return AiSettings(
        routes_file=Path(s.ai_routes_file),
        fake_script_file=Path(s.ai_fake_script_file) if s.ai_fake_script_file.strip() else None,
        daily_token_budget_per_business=s.ai_daily_token_budget_per_business,
        local_inference_hosts=s.local_hosts,
        regulated=is_regulated(),
    )


def api_key(env_name: str | None) -> str:
    """Value of a provider key variable; "" when unset, empty or not one of API_KEY_ENVS (provider is skipped)."""
    if env_name is None or env_name not in API_KEY_ENVS:
        return ""
    return str(getattr(get_settings(), env_name.lower()).get_secret_value())


def routes_file_readable() -> bool:
    """True when AI_ROUTES_FILE exists and can be read. Missing or unreadable means `ai.not_configured`."""
    try:
        with ai_settings().routes_file.open("rb") as f:
            f.read(1)
    except OSError:
        return False
    return True
