"""Deduplicated budget notices via the notifications public API, including the optional operator outbox."""

from datetime import date

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.modules.notifications import public as notifications
from app.platform.config import get_settings
from app.platform.context import Context

from .budget_models import BudgetNotice
from .permissions import SETTINGS_READ


def notify(db: Session, ctx: Context, scope: str, period: date, threshold: int, body: str) -> None:
    claimed = db.scalar(
        insert(BudgetNotice)
        .values(
            business_id=ctx.business_id,
            scope=scope,
            period=period,
            threshold=threshold,
        )
        .on_conflict_do_nothing()
        .returning(BudgetNotice.id)
    )
    if claimed is None:
        return
    title = f"AI {scope} budget: {threshold}% threshold"
    notifications.notify_users(
        db,
        ctx,
        [u.id for u in identity.list_users_with_permission(db, ctx, SETTINGS_READ)],
        kind=f"ai.budget_{threshold}",
        title=title,
        body=body,
    )
    recipient = get_settings().ai_budget_notification_email
    if recipient:
        notifications.enqueue_message(
            db,
            ctx,
            channel=notifications.email_channel(),
            recipient=str(recipient),
            template_key="conversation_reply",
            variables={"body": f"{title}\n{body}\nBusiness: {ctx.business_id}"},
            idempotency_key=f"ai-budget:{ctx.business_id}:{scope}:{period}:{threshold}",
        )
