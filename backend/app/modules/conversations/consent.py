"""Server-owned wording and promotion of explicit request consent; never marketing consent."""

from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform.context import Context

from .models import Conversation

CONTACT_CONSENT_LABEL = "You may contact me about this request by e-mail."
CONTACT_CONSENT_TEXT = f"v1: {CONTACT_CONSENT_LABEL}"


def promote_contact_consent(db: Session, ctx: Context, conv: Conversation) -> None:
    if conv.customer_id is None or not conv.visitor_email or conv.contact_consent_at is None:
        return
    if not conv.contact_consent_text:
        return
    crm.record_request_contact_consent(
        db,
        ctx,
        conv.customer_id,
        email=conv.visitor_email,
        recorded_at=conv.contact_consent_at,
        evidence=(
            f"Public chat {conv.contact_consent_at.isoformat()}: {conv.contact_consent_text} (conversation {conv.id})"
        ),
    )
