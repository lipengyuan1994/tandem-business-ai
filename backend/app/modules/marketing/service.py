"""Segments, audiences, campaigns, suppressions and the unsubscribe token (docs/roadmap/modules/M12-marketing.md).

Eligibility is deterministic SQL and set logic; no AI is involved in deciding who receives a message.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.crm import public as crm
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.modules.scheduling import public as scheduling
from app.platform import audit, clock, crud, events
from app.platform.canonical import sha256_hex
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.errors import Conflict, DomainError, Invalid
from app.platform.events import DomainEventPayload
from app.platform.ids import new_id

from . import permissions as P
from .models import AudienceMember, AudienceSnapshot, Campaign, CampaignRevision, Segment, Suppression
from .schemas import CampaignCreate, CampaignUpdate, SegmentDefinition, SegmentIn

CHANNEL = "email"  # the audience channel (which address and consent apply)
REASONS = ("no_consent", "suppressed", "no_contact", "archived")
OPEN_STATUSES = ("draft", "in_review")


class CampaignSent(DomainEventPayload):
    event_type = "marketing.campaign_sent"
    campaign_id: uuid.UUID
    enqueued: int


# ------------------------------------------------------------------------------------------------ segments
def parse_definition(raw: dict[str, Any]) -> SegmentDefinition:
    try:
        return SegmentDefinition.model_validate(raw)
    except ValidationError as exc:
        raise Invalid("marketing.invalid_segment", detail=str(exc.errors()[0]["msg"])[:200]) from exc


def create_segment(db: Session, ctx: Context, data: SegmentIn) -> Segment:
    ctx.require(P.CAMPAIGN_WRITE)
    assert ctx.business_id is not None
    segment = Segment(
        id=new_id(), business_id=ctx.business_id, name=data.name, definition=data.definition.model_dump(mode="json")
    )
    db.add(segment)
    db.flush()
    audit.record(
        db,
        ctx,
        action="marketing.segment.created",
        target_type="segment",
        target_id=segment.id,
        summary=f"Segment {data.name}",
    )
    return segment


def get_segment(db: Session, ctx: Context, segment_id: uuid.UUID) -> Segment:
    return crud.require_scoped(db, ctx, Segment, segment_id, "marketing.segment_not_found")


def list_segments(db: Session, ctx: Context) -> list[Segment]:
    return list(
        db.scalars(select(Segment).where(Segment.business_id == ctx.business_id).order_by(Segment.name).limit(200))
    )


# ------------------------------------------------------------------------------------------------ audience
@dataclass
class Audience:
    matched: int
    members: list[crm.AudienceCandidate]  # eligible, ordered by name
    excluded: dict[str, int]
    hash: str


def _clause_ids(db: Session, ctx: Context, field: str, op: str, value: Any) -> set[uuid.UUID]:
    if field == "has_appointment_for_service":
        return scheduling.customer_ids_with_service(db, ctx, uuid.UUID(str(value)))
    return crm.customer_ids_where(db, ctx, field, op, value)


def _suppressed_values(db: Session, ctx: Context, values: list[str]) -> set[str]:
    if not values:
        return set()
    return set(
        db.scalars(
            select(Suppression.value_normalized).where(
                Suppression.business_id == ctx.business_id,
                Suppression.channel == CHANNEL,
                Suppression.value_normalized.in_(values),
            )
        )
    )


def _normalized_email(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return crm.normalize_contact("email", value)
    except DomainError:
        return None


def compute_audience(db: Session, ctx: Context, definition: SegmentDefinition) -> Audience:
    """Segment match, then exclusions in a fixed order: archived, no contact, no consent, suppressed."""
    matched_ids: set[uuid.UUID] | None = None
    for clause in definition.all:
        ids = _clause_ids(db, ctx, clause.field, clause.op, clause.value)
        matched_ids = ids if matched_ids is None else matched_ids & ids
    ordered = sorted(matched_ids or set(), key=str)
    candidates = crm.audience_candidates(db, ctx, CHANNEL, ordered)
    normalized = {c.customer_id: _normalized_email(c.value) for c in candidates}
    suppressed = _suppressed_values(db, ctx, [v for v in normalized.values() if v])
    excluded = dict.fromkeys(REASONS, 0)
    eligible: list[crm.AudienceCandidate] = []
    for c in candidates:
        if not c.is_active:
            excluded["archived"] += 1
        elif c.contact_point_id is None or normalized[c.customer_id] is None:
            excluded["no_contact"] += 1
        elif not c.consent_granted:
            excluded["no_consent"] += 1
        elif normalized[c.customer_id] in suppressed:
            excluded["suppressed"] += 1
        else:
            eligible.append(c)
    digest = sha256_hex(
        {
            "channel": CHANNEL,
            "segment": definition.model_dump(mode="json"),
            "members": sorted((str(c.customer_id), str(c.contact_point_id)) for c in eligible),
        }
    )
    return Audience(matched=len(candidates), members=eligible, excluded=excluded, hash=digest)


def preview_audience(db: Session, ctx: Context, segment_id: uuid.UUID) -> Audience:
    ctx.require(P.CAMPAIGN_READ)
    return compute_audience(db, ctx, parse_definition(get_segment(db, ctx, segment_id).definition))


# ------------------------------------------------------------------------------------------------ campaigns
def _revision(db: Session, ctx: Context, revision_id: uuid.UUID | None) -> CampaignRevision | None:
    return crud.get_scoped(db, ctx, CampaignRevision, revision_id) if revision_id else None


def _next_revision(
    db: Session, ctx: Context, campaign: Campaign, subject: str, body: str, kind: str, ai_run_id: uuid.UUID | None
) -> CampaignRevision:
    assert ctx.business_id is not None
    number = (
        db.scalar(select(func.max(CampaignRevision.revision_no)).where(CampaignRevision.campaign_id == campaign.id))
        or 0
    ) + 1
    revision = CampaignRevision(
        id=new_id(),
        business_id=ctx.business_id,
        campaign_id=campaign.id,
        revision_no=number,
        subject=subject,
        body_markdown=body,
        created_by_kind=kind,
        created_by=ctx.user_id,
        ai_run_id=ai_run_id,
    )
    db.add(revision)
    db.flush()
    campaign.current_revision_id = revision.id
    return revision


def create_campaign(db: Session, ctx: Context, data: CampaignCreate) -> Campaign:
    ctx.require(P.CAMPAIGN_WRITE)
    assert ctx.business_id is not None
    get_segment(db, ctx, data.segment_id)
    campaign = Campaign(
        id=new_id(),
        business_id=ctx.business_id,
        name=data.name,
        purpose=data.purpose,
        channel=CHANNEL,
        segment_id=data.segment_id,
        status="draft",
    )
    db.add(campaign)
    db.flush()
    _next_revision(db, ctx, campaign, data.subject, data.body_markdown, "staff", None)
    audit.record(
        db,
        ctx,
        action="marketing.campaign.created",
        target_type="campaign",
        target_id=campaign.id,
        summary=f"Campaign {data.name}",
    )
    db.flush()
    return campaign


def get_campaign(db: Session, ctx: Context, campaign_id: uuid.UUID, *, for_update: bool = False) -> Campaign:
    if for_update:
        campaign = db.scalar(
            select(Campaign)
            .where(
                Campaign.business_id == ctx.business_id,
                Campaign.id == campaign_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if campaign is None:
            from app.platform.errors import NotFound

            raise NotFound("marketing.campaign_not_found")
        return campaign
    return crud.require_scoped(db, ctx, Campaign, campaign_id, "marketing.campaign_not_found")


def list_campaigns(db: Session, ctx: Context) -> list[Campaign]:
    return list(
        db.scalars(
            select(Campaign)
            .where(Campaign.business_id == ctx.business_id)
            .order_by(Campaign.created_at.desc())
            .limit(200)
        )
    )


def revisions_of(db: Session, ctx: Context, campaign_id: uuid.UUID) -> list[CampaignRevision]:
    return list(
        db.scalars(
            select(CampaignRevision)
            .where(CampaignRevision.business_id == ctx.business_id, CampaignRevision.campaign_id == campaign_id)
            .order_by(CampaignRevision.revision_no.desc())
        )
    )


def snapshot_of(db: Session, ctx: Context, snapshot_id: uuid.UUID | None) -> AudienceSnapshot | None:
    return crud.get_scoped(db, ctx, AudienceSnapshot, snapshot_id) if snapshot_id else None


def _editable_campaign(db: Session, ctx: Context, campaign_id: uuid.UUID) -> Campaign:
    # Match execution: proposal first, then campaign. Never wait for a proposal while holding its campaign.
    observed = get_campaign(db, ctx, campaign_id)
    proposal_id = observed.proposal_id
    if observed.status == "in_review" and proposal_id is not None:
        try:
            approvals.cancel_proposal(db, ctx, proposal_id)
        except DomainError:
            pass  # Closed or not ours to cancel; execution still checks the exact submission.
    campaign = get_campaign(db, ctx, campaign_id, for_update=True)
    if campaign.proposal_id != proposal_id:
        raise Conflict("marketing.snapshot_changed")
    return campaign


def _back_to_draft(db: Session, ctx: Context, campaign: Campaign, why: str) -> None:
    """The caller holds proposal then campaign locks; invalidate the old submission."""
    campaign.status, campaign.submitted_snapshot_id, campaign.proposal_id = "draft", None, None
    audit.record(
        db,
        ctx,
        action="marketing.campaign.reopened",
        target_type="campaign",
        target_id=campaign.id,
        summary=f"Back to draft: {why}",
    )


def add_revision(
    db: Session,
    ctx: Context,
    campaign_id: uuid.UUID,
    subject: str,
    body_markdown: str,
    *,
    created_by_kind: str = "staff",
    ai_run_id: uuid.UUID | None = None,
) -> CampaignRevision:
    ctx.require(P.CAMPAIGN_WRITE)
    campaign = _editable_campaign(db, ctx, campaign_id)
    if campaign.status not in OPEN_STATUSES:
        raise Conflict("marketing.campaign_locked", current_status=campaign.status)
    if campaign.status == "in_review":
        _back_to_draft(db, ctx, campaign, "content edited after submission")
    revision = _next_revision(db, ctx, campaign, subject, body_markdown, created_by_kind, ai_run_id)
    campaign.version += 1
    audit.record(
        db,
        ctx,
        action="marketing.campaign.revised",
        target_type="campaign",
        target_id=campaign.id,
        summary=f"Revision {revision.revision_no} by {created_by_kind}",
    )
    return revision


def update_campaign(db: Session, ctx: Context, campaign_id: uuid.UUID, data: CampaignUpdate) -> Campaign:
    ctx.require(P.CAMPAIGN_WRITE)
    campaign = _editable_campaign(db, ctx, campaign_id)
    if campaign.status not in OPEN_STATUSES:
        raise Conflict("marketing.campaign_locked", current_status=campaign.status)
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    if "segment_id" in changes and changes["segment_id"] is not None:
        get_segment(db, ctx, changes["segment_id"])
    if campaign.status == "in_review":
        if campaign.version != data.version:
            raise Conflict("marketing.campaign_version_conflict", current_version=campaign.version)
        _back_to_draft(db, ctx, campaign, "campaign edited after submission")
        campaign.version += 1
        data = data.model_copy(update={"version": campaign.version})
    return crud.update_versioned(
        db, ctx, Campaign, campaign_id, data.version, changes, "marketing.campaign_version_conflict"
    )


def cancel_campaign(db: Session, ctx: Context, campaign_id: uuid.UUID) -> Campaign:
    ctx.require(P.CAMPAIGN_WRITE)
    campaign = _editable_campaign(db, ctx, campaign_id)
    if campaign.status not in OPEN_STATUSES:
        raise Conflict("marketing.campaign_locked", current_status=campaign.status)
    if campaign.status == "in_review":
        _back_to_draft(db, ctx, campaign, "cancelled")
    campaign.status, campaign.version = "cancelled", campaign.version + 1
    audit.record(
        db,
        ctx,
        action="marketing.campaign.cancelled",
        target_type="campaign",
        target_id=campaign.id,
        summary="Cancelled",
    )
    return campaign


def submit_for_review(db: Session, ctx: Context, campaign_id: uuid.UUID) -> Campaign:
    """Freeze today's audience and ask for approval of exactly this content and audience."""
    ctx.require(P.CAMPAIGN_WRITE)
    assert ctx.business_id is not None
    campaign = get_campaign(db, ctx, campaign_id, for_update=True)
    if campaign.status != "draft":
        raise Conflict("marketing.campaign_locked", current_status=campaign.status)
    revision = _revision(db, ctx, campaign.current_revision_id)
    if revision is None:
        raise Invalid("marketing.no_content")
    definition = parse_definition(get_segment(db, ctx, campaign.segment_id).definition)
    audience = compute_audience(db, ctx, definition)
    if not audience.members:
        raise Conflict("marketing.no_eligible_recipients", excluded=audience.excluded)
    snapshot = AudienceSnapshot(
        id=new_id(),
        business_id=ctx.business_id,
        campaign_id=campaign.id,
        segment_definition=definition.model_dump(mode="json"),
        computed_at=clock.now(),
        eligible_count=len(audience.members),
        excluded_counts=audience.excluded,
        hash=audience.hash,
    )
    db.add(snapshot)
    db.flush()
    for member in audience.members:
        assert member.contact_point_id is not None
        db.add(
            AudienceMember(
                id=new_id(),
                business_id=ctx.business_id,
                snapshot_id=snapshot.id,
                customer_id=member.customer_id,
                contact_point_id=member.contact_point_id,
            )
        )
    db.flush()
    # The proposal's validation looks at the campaign, so it must already be in review (and snapshotted).
    campaign.status, campaign.submitted_snapshot_id = "in_review", snapshot.id
    db.flush()
    ref = approvals.create_proposal(
        db,
        ctx,
        action_type="marketing.approve_campaign",
        title=f"Send campaign: {campaign.name}",
        payload={"campaign_id": str(campaign.id), "revision_id": str(revision.id), "snapshot_hash": snapshot.hash},
    )
    campaign.proposal_id = ref.id
    campaign.version += 1
    audit.record(
        db,
        ctx,
        action="marketing.campaign.submitted",
        target_type="campaign",
        target_id=campaign.id,
        summary=f"Submitted for approval: {snapshot.eligible_count} recipients",
        proposal_id=ref.id,
    )
    return campaign


# ------------------------------------------------------------------------------------------------ sending
def unsubscribe_url(db: Session, ctx: Context, customer_id: uuid.UUID, slug: str) -> str:
    assert ctx.business_id is not None
    token = make_token(ctx.business_id, customer_id)
    return f"{get_settings().app_base_url.rstrip('/')}/api/v1/public/{slug}/unsubscribe?token={token}"


def send_approved(
    db: Session,
    ctx: Context,
    campaign_id: uuid.UUID,
    *,
    revision_id: uuid.UUID,
    snapshot_hash: str,
    proposal_id: uuid.UUID,
) -> dict[str, int]:
    """Executor body: re-check consent and suppression for every member immediately before enqueueing its message."""
    campaign = get_campaign(db, ctx, campaign_id, for_update=True)
    snapshot = snapshot_of(db, ctx, campaign.submitted_snapshot_id)
    if (
        campaign.status != "in_review"
        or campaign.proposal_id != proposal_id
        or campaign.current_revision_id != revision_id
        or snapshot is None
        or snapshot.hash != snapshot_hash
    ):
        raise Conflict("marketing.snapshot_changed", current_status=campaign.status)
    revision = _revision(db, ctx, campaign.current_revision_id)
    assert revision is not None
    campaign.status, campaign.approved_snapshot_id = "sending", campaign.submitted_snapshot_id
    db.flush()
    member_ids = list(
        db.scalars(
            select(AudienceMember.customer_id).where(AudienceMember.snapshot_id == campaign.approved_snapshot_id)
        )
    )
    still_eligible = {e.customer_id: e for e in crm.list_marketing_eligible(db, ctx, CHANNEL, member_ids)}
    suppressed = _suppressed_values(
        db, ctx, [v for v in (_normalized_email(e.value) for e in still_eligible.values()) if v]
    )
    brief = org.get_business_brief(db, ctx)
    counts = {"enqueued": 0, "skipped_no_longer_eligible": 0, "skipped_suppressed": 0}
    for customer_id in member_ids:
        current = still_eligible.get(customer_id)
        if current is None:
            counts["skipped_no_longer_eligible"] += 1
        elif _normalized_email(current.value) in suppressed:
            counts["skipped_suppressed"] += 1
        else:
            notifications.enqueue_message(
                db,
                ctx,
                channel=notifications.email_channel(),
                recipient=current.value,
                template_key="marketing_campaign",
                variables={
                    "subject": revision.subject,
                    "body": revision.body_markdown,
                    "business_name": brief.name,
                    "unsubscribe_url": unsubscribe_url(db, ctx, customer_id, brief.slug),
                },
                idempotency_key=f"campaign:{campaign.id}:{customer_id}",
                related_type="campaign",
                related_id=campaign.id,
            )
            counts["enqueued"] += 1
    campaign.status, campaign.sent_at, campaign.version = "sent", clock.now(), campaign.version + 1
    audit.record(
        db,
        ctx,
        action="marketing.campaign.sent",
        target_type="campaign",
        target_id=campaign.id,
        summary=(
            f"Queued {counts['enqueued']} messages; skipped {counts['skipped_no_longer_eligible']} no longer eligible"
            f" and {counts['skipped_suppressed']} suppressed"
        ),
    )
    events.publish(db, ctx, CampaignSent(campaign_id=campaign.id, enqueued=counts["enqueued"]))
    return counts


def on_proposal_closed(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    """A send that was rejected, expired or cancelled returns its campaign to draft (idempotent)."""
    if payload.get("action_type") != "marketing.approve_campaign" or payload.get("outcome") == "succeeded":
        return
    db.execute(
        update(Campaign)
        .where(
            Campaign.business_id == ctx.business_id,
            Campaign.status == "in_review",
            Campaign.proposal_id == uuid.UUID(payload["proposal_id"]),
        )
        .values(status="draft", submitted_snapshot_id=None, proposal_id=None, version=Campaign.version + 1)
    )


# ------------------------------------------------------------------------------------------------ suppressions
def add_suppression(db: Session, ctx: Context, value: str, reason: str) -> Suppression:
    ctx.require(P.CAMPAIGN_WRITE)
    normalized = crm.normalize_contact("email", value)
    return _suppress(db, ctx, normalized, reason)


def _suppress(db: Session, ctx: Context, normalized: str, reason: str) -> Suppression:
    assert ctx.business_id is not None
    db.execute(
        pg_insert(Suppression)
        .values(id=new_id(), business_id=ctx.business_id, channel=CHANNEL, value_normalized=normalized, reason=reason)
        .on_conflict_do_nothing(index_elements=["business_id", "channel", "value_normalized"])
    )
    row = db.scalar(
        select(Suppression).where(
            Suppression.business_id == ctx.business_id,
            Suppression.channel == CHANNEL,
            Suppression.value_normalized == normalized,
        )
    )
    assert row is not None
    audit.record(
        db,
        ctx,
        action="marketing.suppressed",
        target_type="suppression",
        target_id=row.id,
        summary=f"Suppressed ({reason})",
    )
    return row


def list_suppressions(db: Session, ctx: Context) -> list[Suppression]:
    ctx.require(P.CAMPAIGN_READ)
    return list(
        db.scalars(
            select(Suppression)
            .where(Suppression.business_id == ctx.business_id)
            .order_by(Suppression.created_at.desc())
            .limit(500)
        )
    )


# ------------------------------------------------------------------------------------------------ unsubscribe
def make_token(business_id: uuid.UUID, customer_id: uuid.UUID) -> str:
    """Signed, single-purpose, no expiry (an unsubscribe link must keep working)."""
    payload = f"unsub1|{business_id}|{customer_id}|{CHANNEL}".encode()
    key = get_settings().app_secret_key.get_secret_value().encode()
    sig = hmac.new(key, payload, hashlib.sha256).digest()
    return (
        f"{base64.urlsafe_b64encode(payload).decode().rstrip('=')}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"
    )


def read_token(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        key = get_settings().app_secret_key.get_secret_value().encode()
        if not hmac.compare_digest(sig, hmac.new(key, payload, hashlib.sha256).digest()):
            raise ValueError("bad signature")
        purpose, business, customer, channel = payload.decode().split("|")
        if purpose != "unsub1" or channel != CHANNEL:
            raise ValueError("wrong purpose")
        return uuid.UUID(business), uuid.UUID(customer)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Invalid("marketing.invalid_token") from exc


def unsubscribe(db: Session, ctx: Context, token: str) -> None:
    """Withdraw marketing consent and suppress the address. Safe to repeat."""
    business_id, customer_id = read_token(token)
    if business_id != ctx.business_id:
        raise Invalid("marketing.invalid_token")
    value = crm.get_customer_contact(db, ctx, customer_id, "email")
    normalized = _normalized_email(value)
    if normalized:
        _suppress(db, ctx, normalized, "unsubscribed")
    if crm.current_consent(db, ctx, customer_id, CHANNEL, "marketing").status != "withdrawn":
        crm.record_consent(
            db,
            ctx,
            customer_id,
            crm.ConsentIn(
                channel=CHANNEL,
                purpose="marketing",
                status="withdrawn",
                source="unsubscribe_link",
                evidence="Unsubscribe link in a marketing e-mail",
            ),
        )
    audit.record(
        db,
        ctx,
        action="marketing.unsubscribed",
        target_type="customer",
        target_id=customer_id,
        summary="Unsubscribed by link",
    )
