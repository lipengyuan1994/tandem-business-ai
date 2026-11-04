"""Typed application settings loaded from environment variables (and `.env` in development)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .smtp import parse_smtp_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), extra="ignore", case_sensitive=False, hide_input_in_errors=True
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    app_base_url: str = "https://localhost:8443"
    app_secret_key: SecretStr = Field(min_length=32)
    app_version: str = "dev"
    # Written by the backup container; mounted read-only into the api container (docs/runbooks/backup-restore.md).
    backup_status_file: str = "/backup-state/backup-status.json"

    database_url: str
    database_migration_url: str | None = None

    data_profile: Literal["standard", "regulated"] = "standard"
    regulated_host_baa: str = Field(default="", max_length=200)
    regulated_backup_baa: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_regulated(self) -> Settings:
        self.regulated_host_baa = self.regulated_host_baa.strip()
        self.regulated_backup_baa = self.regulated_backup_baa.strip()
        if self.data_profile == "regulated" and not (self.regulated_host_baa and self.regulated_backup_baa):
            raise ValueError(
                "DATA_PROFILE=regulated requires non-empty REGULATED_HOST_BAA and REGULATED_BACKUP_BAA "
                "(max 200 characters each)"
            )
        return self

    @property
    def staff_session_hours(self) -> int:
        return 8 if self.data_profile == "regulated" else self.session_ttl_hours

    @property
    def staff_idle_minutes(self) -> int:
        return 15 if self.data_profile == "regulated" else self.session_idle_minutes

    session_ttl_hours: int = Field(default=12, ge=1, le=24)
    session_idle_minutes: int = Field(default=60, ge=5, le=480)

    oidc_issuer: str = ""
    oidc_internal_issuer_url: str = ""
    oidc_client_id: str = "smb-app"
    oidc_client_secret: SecretStr = SecretStr("")
    keycloak_admin_client_id: str = "smb-admin"
    keycloak_admin_client_secret: SecretStr = SecretStr("")

    smtp_url: str = Field(default="", repr=False)
    smtp_from: str = ""

    @model_validator(mode="after")
    def validate_smtp(self) -> Settings:
        parse_smtp_url(self.smtp_url, self.smtp_from)
        return self

    egress_allowlist: str = ""
    local_inference_hosts: str = ""
    ai_daily_token_budget_per_business: int = 2_000_000
    # AI layer (docs/roadmap/ai/contracts.md §2). A missing or unreadable routes file disables AI; the app still starts.
    ai_routes_file: str = "/config/ai-routes.yaml"
    ai_fake_script_file: str = ""
    vultr_inference_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    openai_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    gemini_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    worker_concurrency: int = Field(default=4, ge=1, le=32)
    log_level: str = "info"

    @property
    def egress_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.egress_allowlist.split(",") if h.strip())

    @property
    def local_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower().rstrip(".") for h in self.local_inference_hosts.split(",") if h.strip())

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once. Fails fast at startup if required values are missing."""
    return Settings()  # values come from the environment


def is_regulated() -> bool:
    """Read-only install-wide policy; business settings cannot override it."""
    return get_settings().data_profile == "regulated"
