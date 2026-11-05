"""Resolve a rule version without attributing old activity to its current grantor."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.models import AuditEvent

from .models import AutonomyRule


@dataclass(frozen=True)
class RuleSnapshot:
    level: str
    granted_by: uuid.UUID


def rule_snapshot(db: Session, ctx: Context, rule_id: uuid.UUID, version: int, action_type: str) -> RuleSnapshot | None:
    rule = db.scalar(
        select(AutonomyRule).where(AutonomyRule.business_id == ctx.business_id, AutonomyRule.id == rule_id)
    )
    if rule is not None and rule.version == version:
        return RuleSnapshot(rule.level, rule.granted_by)
    # Rule saves already emit immutable audit records. Older A0 records predate structured snapshots;
    # match their complete, known summary format rather than guessing from the current rule.
    legacy = {
        f"Autonomy rule {action_type} v{version}: {level}, shadow={shadow}": level
        for level in ("off", "draft", "ask", "act_notify", "act")
        for shadow in (True, False)
    }
    records = db.scalars(
        select(AuditEvent).where(
            AuditEvent.business_id == ctx.business_id,
            AuditEvent.action == "approvals.autonomy_rule.saved",
            AuditEvent.target_type == "autonomy_rule",
            AuditEvent.target_id == rule_id,
        )
    )
    for record in records:
        snapshot = record.changes.get("rule_snapshot", {})
        if snapshot.get("version") == version and record.actor_id is not None:
            return RuleSnapshot(str(snapshot["level"]), record.actor_id)
        if record.summary in legacy and record.actor_id is not None:
            return RuleSnapshot(legacy[record.summary], record.actor_id)
    return None
