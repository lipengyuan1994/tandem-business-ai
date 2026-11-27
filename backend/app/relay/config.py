"""Relay settings, read from `RELAY_*` environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RelaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RELAY_", extra="ignore")

    # Seals each client's shared secret at rest. Rotating it makes every client register again.
    secret_key: SecretStr = Field(min_length=32, repr=False)
    database_url: str = "sqlite:///relay.db"
    # Platform key (a restricted key is enough: Accounts, Account Links, Checkout Sessions, Refunds, Events read).
    # Empty: the relay starts, registers clients, and answers 503 to anything that needs Stripe.
    stripe_secret_key: SecretStr = Field(default=SecretStr(""), repr=False)
    # Signing secret of the Connect webhook endpoint (`/v1/stripe/webhook`, "events on connected accounts").
    stripe_webhook_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    stripe_api_base: str = "https://api.stripe.com"
    # Pin the Stripe API version once the platform account exists; empty uses the account's default version.
    stripe_api_version: str = ""
    # Boston AI Forward's application fee per payment in basis points (100 = 1%). Default 0: no fee. Changing it
    # changes what businesses pay, so it is the owner's decision (open question Q14).
    application_fee_bps: int = Field(default=0, ge=0, le=1000)
    # When set, a Tandem install must present it to register (hosted model); empty lets any install register.
    enrollment_token: SecretStr = Field(default=SecretStr(""), repr=False)
    enrollments_per_hour: int = Field(default=20, ge=1)
    # Hosts that may receive events over plain HTTP or on a private address (Tandem next to the relay), e.g. "api".
    private_callback_hosts: str = ""
    # Seconds between delivery sweeps of waiting events; 0 turns the background sweeper off (tests).
    deliver_interval_seconds: int = Field(default=30, ge=0)

    @property
    def private_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.private_callback_hosts.split(",") if h.strip())

    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_secret_key.get_secret_value())

    @property
    def mode(self) -> str:
        """`live` only for a live key; anything else (test keys, no key) is test mode."""
        key = self.stripe_secret_key.get_secret_value()
        return "live" if key.startswith(("sk_live_", "rk_live_")) else "test"


@lru_cache(maxsize=1)
def get_settings() -> RelaySettings:
    return RelaySettings()  # values come from the environment
