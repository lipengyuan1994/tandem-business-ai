"""Dashboard read tool (PR-01 function): today_summary (contracts §10, integration-map §B).

Sections the acting context may not see are null (the dashboard filters them by permission).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.modules.dashboard import public as dashboard
from app.platform import clock
from app.platform.context import Context

from .base import RunState, Tool
from .common import NoArgs, cite, listing, local_label, redact_contacts, utc
from .inventory import suggestion_view

READ = "org.business.read"
NEXT_APPOINTMENTS = 5


def _today_summary(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    s = dashboard.today_summary(db, ctx)
    data: dict[str, Any] = {"local_date": s.local_date.isoformat(), "timezone": s.timezone}
    if s.agenda is None:
        data["agenda"] = None
    else:
        now = clock.now()
        upcoming = [a for a in s.agenda if a.ends_at >= now and a.status in ("confirmed", "held")]
        data["agenda"] = {
            "count": len(s.agenda),
            "next": [
                {"appointment_id": a.id, "customer_name": a.customer_name, "service_name": a.service_name,
                 "resource_name": a.resource_name, "starts_at": utc(a.starts_at),
                 "local_label": local_label(a.starts_at, a.timezone), "status": a.status}
                for a in upcoming[:NEXT_APPOINTMENTS]
            ],
        }  # fmt: skip
    data["waiting_conversations"] = s.conversations
    data["approvals"] = (
        None
        if s.approvals is None
        else {
            "can_decide": s.approvals.can_decide,
            "top": [
                {"proposal_id": p.id, "title": redact_contacts(p.title, 200), "action_type": p.action_type,
                 "origin_kind": p.origin_kind, "created_at": utc(p.created_at)}
                for p in s.approvals.top[:NEXT_APPOINTMENTS]
            ],
        }
    )  # fmt: skip
    data["low_stock"] = None if s.low_stock is None else listing("items", [suggestion_view(x) for x in s.low_stock])
    data["delivery_issues"] = s.delivery_issues
    cite(state, "today_summary", None, data)
    return data


TOOLS = (
    Tool(
        name="today_summary",
        description="Today at a glance (business-local day): appointment count and the next 5, conversations by "
        "status, pending approvals, low-stock items, failed deliveries. Sections you may not see are null.",
        kind="read", permission=READ, args_model=NoArgs, run=_today_summary,
    ),
)  # fmt: skip
