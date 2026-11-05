"""Typed application settings loaded from environment variables (and `.env` in development)."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, SecretStr, model_validator
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

    # Per-process budgets: one API (6 connections) + one worker (6), leaving 88 of
    # PostgreSQL's default 100 for Keycloak, migrations, backups and administration.
    api_db_pool_size: int = Field(default=4, ge=1, le=20)
    api_db_max_overflow: int = Field(default=2, ge=0, le=10)
    worker_db_pool_size: int = Field(default=6, ge=1, le=32)
    worker_db_max_overflow: int = Field(default=0, ge=0, le=10)
    db_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    db_statement_timeout_ms: int = Field(default=30_000, ge=1, le=300_000)
    db_idle_transaction_timeout_ms: int = Field(default=60_000, ge=1, le=300_000)

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
    ai_daily_cost_limit_usd: Decimal = Field(default=Decimal("5.00"), ge=0, decimal_places=6, allow_inf_nan=False)
    ai_monthly_cost_limit_usd: Decimal = Field(default=Decimal("50.00"), ge=0, decimal_places=6, allow_inf_nan=False)
    ai_max_concurrent_calls: int = Field(default=2, ge=1, le=32)
    ai_public_intake_per_hour: int = Field(default=30, ge=0)
    ai_public_intake_max_pending: int = Field(default=30, ge=0)
    ai_public_daily_token_budget_per_business: int = Field(default=200_000, ge=0)
    ai_budget_notification_email: EmailStr | Literal[""] = ""
    # AI layer (docs/roadmap/ai/contracts.md §2). A missing or unreadable routes file disables AI; the app still starts.
    ai_routes_file: str = "/config/ai-routes.yaml"
    ai_fake_script_file: str = ""
    vultr_inference_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    openai_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    gemini_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    worker_concurrency: int = Field(default=4, ge=2, le=32)
    worker_ai_concurrency: int = Field(default=2, ge=1, le=31)
    worker_shutdown_grace_seconds: float = Field(default=100, ge=0, le=300)

    @model_validator(mode="after")
    def validate_worker_capacity(self) -> Settings:
        if self.worker_ai_concurrency >= self.worker_concurrency:
            raise ValueError("WORKER_AI_CONCURRENCY must be less than WORKER_CONCURRENCY")
        return self

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
