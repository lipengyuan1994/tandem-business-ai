"""A harmless executor for tests and for trying the approvals UI in development (never registered in prod).

Registered when APP_ENV=test, or APP_ENV=dev with DEV_TEST_EXECUTORS=1. It writes an audit event and, when asked,
a row in the test-only `test_items` table; it can fail with a business error or crash, to exercise the engine.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform import audit
from app.platform.context import Context
from app.platform.errors import Conflict
from app.platform.ids import new_id
from app.platform.registry import ExecutionMeta, ExecutorSpec, PreviewLine

ACTION_TYPE = "testing.noop_write"
APPROVE_PERMISSION = "crm.proposal.approve"  # held by owner/admin/operator, so the dev UI can approve it


class NoopPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    write_test_item: bool = False
    fail: bool = False
    crash: bool = False


def describe(db: Session, ctx: Context, p: NoopPayload) -> list[PreviewLine]:
    return [PreviewLine(label="Test item", before=None, after=p.name)]


def validate(db: Session, ctx: Context, p: NoopPayload) -> None:
    return None


def run(db: Session, ctx: Context, p: NoopPayload, meta: ExecutionMeta) -> dict[str, Any]:
    item_id = new_id()
    if p.write_test_item:
        db.execute(
            text("insert into test_items (id, business_id, name) values (:i, :b, :n)"),
            {"i": item_id, "b": ctx.business_id, "n": p.name},
        )
    audit.record(
        db,
        ctx,
        action="testing.noop.executed",
        target_type="test_item",
        target_id=item_id,
        summary=f"Test executor ran: {p.name}",
    )
    if p.fail:
        raise Conflict("testing.business_rule_failed", detail="asked to fail")
    if p.crash:
        raise RuntimeError("simulated crash after writing")
    return {"test_item_id": str(item_id)}


SPEC = ExecutorSpec(
    action_type=ACTION_TYPE,
    payload_model=NoopPayload,
    approve_permission=APPROVE_PERMISSION,
    describe=describe,
    validate=validate,
    run=run,
)
