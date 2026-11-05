"""A0 API; approvals owns rule policy and Activity, org owns the kill switch."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.approvals import public as approvals
from app.modules.org import public as org
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require

router = APIRouter(prefix="/autonomy")
ERRORS = error_responses(401, 403, 409, 422)


@router.get("/rules", response_model=approvals.RulesOut, responses=ERRORS)
def rules(ctx: Context = Depends(require("org.settings.write"))) -> approvals.RulesOut:
    with transaction(ctx) as db:
        return approvals.list_autonomy_rules(db, ctx)


@router.put("/rules", response_model=approvals.RuleOut, responses=ERRORS)
def save_rule(body: approvals.RulePut, ctx: Context = Depends(require("org.settings.write"))) -> approvals.RuleOut:
    with transaction(ctx) as db:
        rule = approvals.put_autonomy_rule(db, ctx, body)
        return approvals.autonomy_rule_out(db, ctx, rule)


@router.post("/pause", response_model=approvals.PauseOut, responses=ERRORS)
def pause(ctx: Context = Depends(require("org.settings.write"))) -> approvals.PauseOut:
    with transaction(ctx) as db:
        org.set_autonomy_paused(db, ctx, True)
        return approvals.PauseOut(paused=True)


@router.post("/resume", response_model=approvals.PauseOut, responses=ERRORS)
def resume(ctx: Context = Depends(require("org.settings.write"))) -> approvals.PauseOut:
    with transaction(ctx) as db:
        org.set_autonomy_paused(db, ctx, False)
        return approvals.PauseOut(paused=False)


@router.get("/activity", response_model=approvals.ActivityPage, responses=ERRORS)
def activity(
    limit: int = 50, cursor: str | None = None, ctx: Context = Depends(require("approvals.proposal.read"))
) -> approvals.ActivityPage:
    with transaction(ctx) as db:
        return approvals.autonomy_activity(db, ctx, limit=limit, cursor=cursor)
