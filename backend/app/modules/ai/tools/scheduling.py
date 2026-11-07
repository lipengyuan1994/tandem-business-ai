"""Scheduling read tools: find_slots, list_appointments (contracts §10, integration-map §B).

Free slots come from `scheduling.find_slots` only; the model chooses among them. `find_slots` looks at most 14 days
ahead of `earliest` and returns at most 10 slots.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from pydantic import AwareDatetime, BaseModel, field_validator
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.modules.scheduling import public as scheduling
from app.platform import clock
from app.platform.context import Context

from .base import RunState, Tool
from .common import ToolArgs, cite, listing, local_label, utc

READ = "scheduling.appointment.read"
MAX_SLOT_WINDOW = timedelta(days=14)
MAX_SLOTS = 10


class BoundedWindow(ToolArgs):
    @field_validator("earliest", "latest", "start", "end", check_fields=False)
    @classmethod
    def near_now(cls, value: Any) -> Any:
        if abs(value - clock.now()) > timedelta(days=366):
            raise ValueError("date must be within one year of now")
        return value


class FindSlotsArgs(BoundedWindow):
    service_id: uuid.UUID
    earliest: AwareDatetime
    latest: AwareDatetime
    resource_id: uuid.UUID | None = None


class ListAppointmentsArgs(BoundedWindow):
    start: AwareDatetime
    end: AwareDatetime
    customer_id: uuid.UUID | None = None


def _find_slots(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, FindSlotsArgs)
    latest = min(args.latest, args.earliest + MAX_SLOT_WINDOW)
    query = scheduling.SlotQuery(
        service_id=args.service_id, earliest=args.earliest, latest=latest, resource_id=args.resource_id,
        limit=MAX_SLOTS,
    )  # fmt: skip
    slots = scheduling.find_slots(db, ctx, query)
    data: dict[str, Any] = {
        "service_id": args.service_id,
        "earliest": utc(args.earliest),
        "latest": utc(latest),
        **listing(
            "slots",
            [
                {"starts_at": utc(s.starts_at), "ends_at": utc(s.ends_at), "resource_id": s.resource_id,
                 "resource_name": s.resource_name, "local_label": s.local_label,
                 "resources": [r.model_dump(mode="json") for r in s.resources]}
                for s in slots
            ],
            MAX_SLOTS,
        ),
    }  # fmt: skip
    if latest < args.latest:
        data["window_capped"] = True
    cite(state, "find_slots", args.service_id, data)
    return data


def _list_appointments(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ListAppointmentsArgs)
    if args.customer_id is not None:
        crm.get_customer_summary(db, ctx, args.customer_id)  # not-found for unknown or other businesses' customers
    rows = scheduling.list_appointments(db, ctx, start=args.start, end=args.end, customer_id=args.customer_id)
    data = listing(
        "appointments",
        [
            {"appointment_id": a.id, "customer_id": a.customer_id, "customer_name": a.customer_name,
             "service_id": a.service_id, "service_name": a.service_name, "resource_id": a.resource_id,
             "resource_name": a.resource_name, "starts_at": utc(a.starts_at), "ends_at": utc(a.ends_at),
             "local_label": local_label(a.starts_at, a.timezone), "status": a.status}
            for a in rows
        ],
    )  # fmt: skip
    cite(state, "list_appointments", args.customer_id, data)
    return data


TOOLS = (
    Tool(
        name="find_slots",
        description="Free appointment slots for a service between two ISO datetimes with time zone (window at most "
        "14 days; at most 10 slots), optionally for one resource. Times are UTC plus a business-local label.",
        kind="read", permission=READ, args_model=FindSlotsArgs, run=_find_slots,
    ),
    Tool(
        name="list_appointments",
        description="Appointments between two ISO datetimes with time zone, optionally for one customer (at most "
        "20).",
        kind="read", permission=READ, args_model=ListAppointmentsArgs, run=_list_appointments,
    ),
)  # fmt: skip
