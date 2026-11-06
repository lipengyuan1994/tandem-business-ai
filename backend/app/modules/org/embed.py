"""Exact embed origins and the dynamic document policy consumed by Caddy."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.platform.config import get_settings
from app.platform.context import Context, system_context
from app.platform.db import transaction
from app.platform.errors import Forbidden
from app.platform.permissions import public_context
from app.platform.registry import get_registry

from . import service

router = APIRouter()


def normalize_origin(value: str, *, development: bool) -> str:
    if len(value) > 300 or any(c.isspace() or c in "*<>\\'\"" for c in value):
        raise ValueError("use an exact HTTPS origin without wildcards")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if port == 0:
            raise ValueError("port must be between 1 and 65535")
        if not host or parsed.username is not None or parsed.password is not None:
            raise ValueError("host required; credentials forbidden")
        if parsed.path or parsed.query or parsed.fragment or value.endswith(("?", "#", ":")):
            raise ValueError("origin must not contain a path, query or fragment")
        if parsed.scheme != "https" and not (development and parsed.scheme == "http" and host == "localhost"):
            raise ValueError("HTTPS required (HTTP localhost is development only)")
        host = host.encode("idna").decode().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|[0-9a-f:]+", host):
            raise ValueError("invalid host")
        if ":" in host:
            host = f"[{host}]"
        suffix = f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else ""
        return f"{parsed.scheme}://{host}{suffix}"
    except (ValueError, UnicodeError) as exc:
        raise ValueError("use an exact HTTPS origin without a path or credentials") from exc


@router.get("/embed-policy", include_in_schema=False)
def embed_policy(path: str = "") -> Response:
    match = re.fullmatch(r"/embed/([a-z0-9-]{3,40})/chat/?(?:\?[^#]*)?", path)
    resolver = get_registry().public_business_resolver
    business_id = resolver(match[1]) if match and resolver else None
    origins: list[str] = []
    if business_id:
        with transaction(system_context(business_id)) as db:
            settings = service.read_settings(db, system_context(business_id))
            if settings.public_chat_enabled:
                # Validate again at delivery so changing dev -> prod cannot retain HTTP permissions.
                for origin in settings.embed_allowed_origins:
                    try:
                        origins.append(normalize_origin(origin, development=get_settings().app_env == "dev"))
                    except ValueError:
                        pass
    ancestors = " ".join(origins) if origins else "'none'"
    csp = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; "
        f"frame-ancestors {ancestors}; base-uri 'self'; form-action 'self'"
    )
    return Response(headers={"X-Embed-CSP": csp, "Content-Security-Policy": csp, "Cache-Control": "no-store"})


class EmbedConfig(BaseModel):
    label: str
    allowed_origins: list[str]


@router.get("/{business_slug}/embed-config", response_model=EmbedConfig)
def config(request: Request, response: Response, ctx: Context = Depends(public_context)) -> EmbedConfig:
    with transaction(ctx) as db:
        settings = service.read_settings(db, ctx)
        origins = settings.embed_allowed_origins
        sent = request.headers.get("origin")
        if sent and sent != get_settings().app_base_url.rstrip("/"):
            if sent not in origins:
                raise Forbidden("org.embed_origin_denied")
            response.headers["Access-Control-Allow-Origin"] = sent
            response.headers["Vary"] = "Origin"
        response.headers["Cache-Control"] = "no-store"
        return EmbedConfig(label=settings.embed_button_label, allowed_origins=origins)
