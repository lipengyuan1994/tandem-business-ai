"""The route file (`AI_ROUTES_FILE`, YAML): providers, ordered routes per model class, limits. Contracts §6.

Validation errors name the bad key (e.g. `providers.vultr.kind`) so an operator can fix the file. The file holds no
secrets: providers name the environment variable that carries their key (`api_key_env`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]  # PyYAML, locked through uvicorn[standard]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ModelClass = Literal["agent", "fast_extract", "writer", "safety", "grounding", "embed", "stt", "tts"]


class ProviderCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["openai_compat", "anthropic", "fake"]  # fake: refused at startup when APP_ENV=prod
    base_url: str = ""
    api_key_env: str | None = None
    hosted: bool = True
    vendor: Literal["vultr", "external", "local"]
    baa: bool = False  # deprecated compatibility key; grants no routing access (ADR-020 decision 5)


class RouteEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str = Field(min_length=1)
    json_schema: bool = False  # provider proved response_format json_schema for this model (AI-04)
    price_in_per_m: float = Field(default=0.0, ge=0)  # USD per million input tokens
    price_out_per_m: float = Field(default=0.0, ge=0)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=8, ge=1)
    max_input_tokens_per_step: int = Field(default=12_000, ge=1)
    max_output_tokens_per_step: int = Field(default=800, ge=1)
    run_timeout_s: int = Field(default=90, ge=1)
    step_timeout_s: int = Field(default=30, ge=1)


class RoutesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderCfg]
    routes: dict[ModelClass, list[RouteEntry]]  # ordered preference
    limits: Limits = Limits()

    @model_validator(mode="after")
    def _known_providers(self) -> RoutesFile:
        for name, cfg in self.providers.items():
            if not cfg.hosted and cfg.vendor != "local":
                raise ValueError(f"providers.{name}.vendor: hosted: false requires vendor: local")
        for model_class, entries in self.routes.items():
            for i, entry in enumerate(entries):
                if model_class == "grounding" and entry.json_schema:
                    raise ValueError("routes.grounding requires json_object mode (json_schema must be false)")
                if entry.provider not in self.providers:
                    raise ValueError(f"routes.{model_class}.{i}.provider: unknown provider {entry.provider!r}")
        return self


class RoutesFileError(ValueError):
    """The route file cannot be read, is not YAML, or does not match the schema. The message names the bad key."""


def _loc(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(p) for p in loc) or "(root)"


def parse_routes(data: Any, *, source: str = "ai-routes.yaml") -> RoutesFile:
    if not isinstance(data, dict):
        raise RoutesFileError(f"{source}: (root): expected a mapping with providers and routes")
    try:
        return RoutesFile.model_validate(data)
    except ValidationError as exc:
        problems = []
        for err in exc.errors(include_url=False, include_input=False):
            msg = str(err["msg"]).removeprefix("Value error, ")
            # The provider check reports its own key path inside the message.
            problems.append(msg if not err["loc"] and msg.startswith("routes.") else f"{_loc(err['loc'])}: {msg}")
        raise RoutesFileError(f"{source}: " + "; ".join(problems)) from None


def load_routes_file(path: Path) -> RoutesFile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutesFileError(f"{path.name}: cannot be read ({type(exc).__name__})") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise RoutesFileError(f"{path.name}: not valid YAML{where}") from None
    return parse_routes(data, source=path.name)


__all__ = ["Limits", "ModelClass", "ProviderCfg", "RouteEntry", "RoutesFile", "RoutesFileError", "load_routes_file"]
