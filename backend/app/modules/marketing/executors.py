"""marketing.approve_campaign: an approved campaign is queued (simulated e-mail until SMTP is configured)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.notifications import public as notifications
from app.platform.context import Context
from app.platform.errors import Conflict

from . import permissions as P
from . import service
from .schemas import ApproveCampaignPayload

REASON_LABELS = {
    "no_consent": "Excluded: no marketing consent",
    "suppressed": "Excluded: unsubscribed or suppressed",
    "no_contact": "Excluded: no e-mail address",
    "archived": "Excluded: archived customer",
}


def _describe(db: Session, ctx: Context, p: ApproveCampaignPayload) -> list[PreviewLine]:
    campaign = service.get_campaign(db, ctx, p.campaign_id)
    revision = service._revision(db, ctx, p.revision_id)
    snapshot = service.snapshot_of(db, ctx, campaign.submitted_snapshot_id)
    # Say what approval will really do: the channel is chosen by the same rule that queues the messages.
    channel = "E-mail" if notifications.email_channel() == "email" else "E-mail (simulated)"
    lines = [PreviewLine("Campaign", None, campaign.name), PreviewLine("Channel", None, channel)]
    if snapshot is not None:
        lines.append(PreviewLine("Recipients", None, str(snapshot.eligible_count)))
        lines += [
            PreviewLine(REASON_LABELS[reason], None, str(count))
            for reason, count in snapshot.excluded_counts.items()
            if count
        ]
    if revision is not None:
        lines += [
            PreviewLine("Subject", None, revision.subject),
            PreviewLine("Message", None, revision.body_markdown[:300]),
        ]
    return lines


def _validate(db: Session, ctx: Context, p: ApproveCampaignPayload) -> None:
    """The approval applies only to the exact revision and audience that were submitted."""
    campaign = service.get_campaign(db, ctx, p.campaign_id)
    snapshot = service.snapshot_of(db, ctx, campaign.submitted_snapshot_id)
    if (
        campaign.status != "in_review"
        or campaign.current_revision_id != p.revision_id
        or snapshot is None
        or snapshot.hash != p.snapshot_hash
    ):
        raise Conflict("marketing.snapshot_changed", detail="The content or the audience changed after submission")


def _run(db: Session, ctx: Context, p: ApproveCampaignPayload, meta: ExecutionMeta) -> dict[str, Any]:
    return dict(
        service.send_approved(
            db,
            ctx,
            p.campaign_id,
            revision_id=p.revision_id,
            snapshot_hash=p.snapshot_hash,
            proposal_id=meta.proposal_id,
        )
    )


APPROVE = ExecutorSpec(
    "marketing.approve_campaign", ApproveCampaignPayload, P.PROPOSAL_APPROVE, _describe, _validate, _run
)
