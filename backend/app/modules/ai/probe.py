"""Manual, opt-in catalog/JSON protocol probe. Never loads .env or executes model-requested tools.

From backend/: AI_LIVE=1 .venv/bin/python -m app.modules.ai.probe --provider vultr
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.platform.http import client

from .agent.protocol import AGENT_STEP_ADAPTER, ToolCallStep, agent_step_json_schema
from .gateway.openai_compat import OpenAICompatAdapter, error_for_status
from .gateway.routes import load_routes_file
from .gateway.types import Adapter, ChatMessage, ChatRequest, JsonMode, ProviderError
from .settings import API_KEY_ENVS

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_REPORT_DIR = REPO_ROOT / "backend/evals/reports"
ROUTES_PATH = REPO_ROOT / "deploy/config/ai-routes.example.yaml"
NON_CHAT = re.compile(
    r"embed|rerank|whisper|speech|audio|tts|stt|chatterbox|\bauk\b|image|video|"
    r"content[-_ ]?safety|guard|moderation|diffusion|\bflux\b",
    re.IGNORECASE,
)


class ProbeFinal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: str
    message_to_staff: str


class ProbeRow(BaseModel):
    model: str
    probe: Literal["final", "tool_call"]
    json_mode: JsonMode
    schema_valid: bool = False
    expected_output: bool = False
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_kind: str | None = None


def select_models(catalog: list[dict[str, Any]], models: str | None) -> list[str]:
    """Explicit names must exist in the catalog; otherwise filter non-chat IDs and task metadata."""
    ids = list(dict.fromkeys(item["id"] for item in catalog))
    if models is not None:
        selected = list(dict.fromkeys(name.strip() for name in models.split(",") if name.strip()))
        if not selected or any(name not in ids for name in selected):
            raise ValueError("--models must name models present in the catalog")
        return selected
    selected = []
    for item in catalog:
        # Features such as image input or safety support do not make a text-output chat model non-chat.
        metadata: dict[str, Any] = {k: item[k] for k in ("type", "task", "model_type", "pipeline_tag") if k in item}
        architecture = item.get("architecture")
        if isinstance(architecture, dict):
            metadata["output_modalities"] = architecture.get("output_modalities", [])
        if "output_modalities" in item:
            metadata["output_modalities"] = item["output_modalities"]
        if not NON_CHAT.search(item["id"] + " " + json.dumps(metadata)) and item["id"] not in selected:
            selected.append(item["id"])
    return selected


def fetch_catalog(adapter: OpenAICompatAdapter, api_key: str) -> list[dict[str, Any]]:
    """Reuse the adapter's guarded HTTP client; never expose headers or raw error bodies."""
    response = adapter._http().get(
        f"{adapter.base_url}/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=httpx.Timeout(60.0, connect=5.0),
    )
    error = error_for_status(response.status_code, response.headers.get("retry-after"))
    if error is not None:
        raise error
    data = response.json()
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not all(
        isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] for item in items
    ):
        raise ValueError("invalid catalog")
    # Only retain catalog metadata, never response/request headers or arbitrary provider error details.
    fields = {
        "id",
        "owned_by",
        "type",
        "task",
        "model_type",
        "pipeline_tag",
        "architecture",
        "output_modalities",
        "supported_parameters",
        "capabilities",
    }
    catalog = [
        {k: v for k, v in item.items() if k in fields or any(s in k.lower() for s in ("price", "pricing", "feature"))}
        for item in items
    ]
    # Defense in depth if a provider echoes the credential in otherwise printable catalog metadata.
    encoded_key = json.dumps(api_key)[1:-1]
    return json.loads(json.dumps(catalog).replace(encoded_key, "[REDACTED]"))  # type: ignore[no-any-return]


def probe_models(adapter: Adapter, models: list[str]) -> list[ProbeRow]:
    """Exactly three independent calls per model: two small JSON probes and one agent-step probe."""
    rows = []
    cases: list[tuple[Literal["final", "tool_call"], JsonMode]] = [
        ("final", "json_schema"),
        ("final", "json_object"),
        ("tool_call", "json_object"),
    ]
    for model in models:
        for kind, mode in cases:
            tool = kind == "tool_call"
            prompt = (
                'Return one JSON agent step calling tool list_services with arguments {"query":"brake"}. '
                'Use kind "tool_call", tool "list_services", arguments {"query":"brake"}, and a short reason. '
                "Do not return a final answer."
                if tool
                else 'Return exactly this JSON object: {"kind":"final","message_to_staff":"ok"}.'
            )
            req = ChatRequest(
                model=model,
                messages=[ChatMessage(role="user", content=prompt)],
                json_schema=agent_step_json_schema() if tool else ProbeFinal.model_json_schema(),
                json_mode=mode,
                timeout_s=60.0,
                max_output_tokens=128,
                temperature=0.0,
            )
            row = ProbeRow(model=model, probe=kind, json_mode=mode)
            started = time.monotonic()
            try:
                result = adapter.chat(req)
                row.latency_ms = result.latency_ms
                row.input_tokens = result.usage.input_tokens
                row.output_tokens = result.usage.output_tokens
                if tool:
                    step = AGENT_STEP_ADAPTER.validate_json(result.text, strict=True)
                    row.expected_output = (
                        isinstance(step, ToolCallStep)
                        and step.tool == "list_services"
                        and step.arguments == {"query": "brake"}
                    )
                else:
                    final = ProbeFinal.model_validate_json(result.text)
                    row.expected_output = final.kind == "final" and final.message_to_staff == "ok"
                row.schema_valid = True
            except ValidationError:
                row.error_kind = "validation_error"
            except ProviderError as exc:
                row.error_kind = exc.kind
                row.latency_ms = int((time.monotonic() - started) * 1000)
            rows.append(row)
    return rows


def write_report(
    directory: Path,
    *,
    provider: str,
    base_url: str,
    catalog: list[dict[str, Any]],
    rows: list[ProbeRow],
    now: datetime | None = None,
) -> tuple[Path, Path]:
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    date = stamp.date().isoformat()
    host = urlsplit(base_url).hostname
    report = {
        "date": date,
        "timestamp_utc": stamp.isoformat(),
        "provider": provider,
        "base_url_host": host,
        "catalog": catalog,
        "results": [row.model_dump() for row in rows],
    }
    lines = [
        f"# Catalog probe — {date}",
        "",
        f"UTC: {stamp.isoformat()}",
        f"Provider: {provider}",
        f"Base URL host: {host}",
        "",
        "Tool-call probe uses JSON agent steps; tools are never executed.",
        "",
        "| Model | Probe | JSON mode | Schema valid | Expected output | Latency ms |"
        " Input tokens | Output tokens | Error kind |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        values = [
            row.model,
            row.probe,
            row.json_mode,
            "yes" if row.schema_valid else "no",
            "yes" if row.expected_output else "no",
            row.latency_ms,
            row.input_tokens,
            row.output_tokens,
            row.error_kind,
        ]
        lines.append(
            "| "
            + " | ".join(
                "—" if v is None else str(v).replace("|", "\\|").replace("\n", " ").replace("\r", " ") for v in values
            )
            + " |"
        )
    lines += [
        "",
        "Catalog (including any published prices/features):",
        "",
        "```json",
        json.dumps(catalog, indent=2, ensure_ascii=True),
        "```",
        "",
    ]
    directory.mkdir(parents=True, exist_ok=True)
    markdown = directory / f"{date}-probe.md"
    machine = directory / f"{date}-probe.json"
    markdown.write_text("\n".join(lines), encoding="utf-8")
    machine.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return markdown, machine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="vultr")
    parser.add_argument("--models", help="Comma-separated catalog IDs (overrides the text-model filter)")
    parser.add_argument("--base-url", help="Override the selected provider's base URL")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if os.environ.get("AI_LIVE") != "1":
        print("Refusing live probe: explicitly set AI_LIVE=1.", file=sys.stderr)
        return 2
    adapter = None
    try:
        cfg = load_routes_file(ROUTES_PATH).providers.get(args.provider)
        if cfg is None or cfg.kind != "openai_compat" or cfg.api_key_env not in API_KEY_ENVS:
            raise ValueError("unsupported provider")
        key = os.environ.get(cfg.api_key_env, "")
        if not key.strip():
            print("Probe refused: provider key is missing from the environment.", file=sys.stderr)
            return 2
        base_url = args.base_url or cfg.base_url
        url = urlsplit(base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("invalid base URL")
        # Explicit allowlist avoids get_settings(), whose development defaults read .env.
        hosts = [
            h.strip() for h in os.environ.get("EGRESS_ALLOWLIST", "api.vultrinference.com").split(",") if h.strip()
        ]
        adapter = OpenAICompatAdapter(args.provider, base_url, key)
        adapter._client_factory = lambda: client(allowlist=hosts)
        catalog = fetch_catalog(adapter, key)
        print(json.dumps(catalog, indent=2, ensure_ascii=True))
        models = select_models(catalog, args.models)
        if not models:
            raise ValueError("no candidate models")
        rows = probe_models(adapter, models)
        write_report(
            args.out or DEFAULT_REPORT_DIR, provider=args.provider, base_url=base_url, catalog=catalog, rows=rows
        )
    except Exception as exc:
        # Never format exception messages/tracebacks: HTTP exceptions can contain request headers.
        kind = exc.kind if isinstance(exc, ProviderError) else "probe_error"
        print(f"Probe failed ({kind}); check configuration, catalog and connectivity.", file=sys.stderr)
        return 2
    finally:
        if adapter is not None and adapter._client is not None:
            adapter._client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
