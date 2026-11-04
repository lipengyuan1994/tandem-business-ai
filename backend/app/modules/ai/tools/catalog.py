"""Catalog read tools: list_services, get_service_parts (contracts §10, integration-map §B)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.platform.context import Context

from .base import RunState, Tool
from .common import ToolArgs, cite, listing, money, qty

READ = "catalog.item.read"


class ListServicesArgs(ToolArgs):
    query: str = Field(max_length=100, description="Words of the service name; empty lists all services.")


class ServiceArgs(ToolArgs):
    service_id: uuid.UUID


def _list_services(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ListServicesArgs)
    rows = catalog.search_items(db, ctx, args.query.strip() or None, "service", limit=20)
    data = listing(
        "services",
        [
            {"service_id": s.id, "name": s.name, "duration_minutes": s.duration_minutes,
             "price": money(s.price_minor, s.currency), "is_bookable_online": s.is_bookable_online}
            for s in rows
        ],
    )  # fmt: skip
    cite(state, "list_services", None, data)
    return data


def _get_service_parts(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ServiceArgs)
    req = catalog.get_service_requirements(db, ctx, args.service_id)
    data = {
        "service_id": req.service_id,
        "name": req.name,
        "duration_minutes": req.duration_minutes,
        "buffer_minutes": req.buffer_minutes,
        "resource_kind": req.resource_kind,
        "price": money(req.price_minor, req.currency),
        **listing("parts", [{"item_id": p.item_id, "item_name": p.item_name, "quantity": qty(p.quantity)}
                            for p in req.parts]),
    }  # fmt: skip
    cite(state, "get_service_parts", req.service_id, data)
    return data


TOOLS = (
    Tool(
        name="list_services",
        description="Search the bookable services by name: id, duration, price, whether customers may book online.",
        kind="read", permission=READ, args_model=ListServicesArgs, run=_list_services,
    ),
    Tool(
        name="get_service_parts",
        description="What a service needs: duration, buffer, resource kind, price and the parts it uses "
        "(item ids, names, quantities).",
        kind="read", permission=READ, args_model=ServiceArgs, run=_get_service_parts,
    ),
)  # fmt: skip
