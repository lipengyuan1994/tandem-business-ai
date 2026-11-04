"""Marketing: export, and erasure of an erased person's traces with a permanent do-not-email entry (M18)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.errors import Invalid
from app.platform.export import dump_models
from app.platform.registry import ErasureTarget

from .models import AudienceMember, AudienceSnapshot, Campaign, CampaignRevision, Segment, Suppression
from .service import _suppress


def export(db: Session, ctx: Context) -> dict[str, list[dict[str, Any]]]:
    return dump_models(db, ctx, [Segment, Campaign, CampaignRevision, AudienceSnapshot, AudienceMember, Suppression])


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Runs before crm blanks the addresses: each e-mail address gets an `erased` suppression, so a later import of
    the same address is never mailed. Campaign text and saved audiences lose the person's names and addresses."""
    suppressed = _suppress_addresses(db, ctx, target, promote=False)["suppressions_added"]
    needles = target.needles
    purposes = db.execute(
        update(Campaign)
        .where(
            Campaign.business_id == ctx.business_id,
            Campaign.purpose.is_distinct_from(func.redact_text(Campaign.purpose, needles)),
        )
        .values(purpose=func.redact_text(Campaign.purpose, needles))
    )
    segments = db.execute(
        update(Segment)
        .where(
            Segment.business_id == ctx.business_id,
            Segment.definition.is_distinct_from(func.redact_jsonb(Segment.definition, needles)),
        )
        .values(definition=func.redact_jsonb(Segment.definition, needles))
    )
    revisions = int(db.scalar(select(func.erase_redact_campaign_revisions(ctx.business_id, needles))) or 0)
    return {
        "suppressions_added": suppressed,
        "campaign_purposes_redacted": rows_affected(purposes),
        "segments_redacted": rows_affected(segments),
        "campaign_revisions_redacted": revisions,
    }


def restrict(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    return _suppress_addresses(db, ctx, target, promote=True)


def _suppress_addresses(db: Session, ctx: Context, target: ErasureTarget, *, promote: bool) -> dict[str, int]:
    suppressed = 0
    for email in target.emails:
        try:
            normalized = crm.normalize_contact("email", email)
        except Invalid:
            continue
        _suppress(db, ctx, normalized, "erased")
        if promote:
            # Suppressions are append-only for smb_app. A narrow tenant-guarded function can only strengthen a reason.
            db.scalar(select(func.restrict_mark_erased_suppression(ctx.business_id, normalized)))
        suppressed += 1
    return {"suppressions_added": suppressed}
