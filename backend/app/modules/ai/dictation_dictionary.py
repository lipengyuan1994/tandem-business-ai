"""Permission-scoped names only. Screen entities precede recency terms under the 200-term cap."""

import uuid

from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.crm import public as crm
from app.modules.identity import public as identity
from app.modules.knowledge import public as knowledge
from app.modules.purchasing import public as purchasing
from app.platform.context import Context


def dictionary(db: Session, ctx: Context, entity_ids: list[uuid.UUID]) -> list[str]:
    selected: list[str] = []
    recent: list[str] = []
    ids = set(entity_ids)
    if "catalog.item.read" in ctx.permissions:
        for item in catalog.get_items(db, ctx, ids).values() if ids else []:
            selected.extend([item.name, item.sku or ""])
        for item in catalog.search_items(db, ctx, None, None, limit=50):
            recent.extend([item.name, item.sku or ""])
    if "purchasing.po.read" in ctx.permissions:
        if ids:
            selected.extend(purchasing.dictation_names(db, ctx, ids, only_selected=True))
        recent.extend(purchasing.dictation_names(db, ctx, ids))
    if "identity.membership.read" in ctx.permissions:
        if ids:
            selected.extend(identity.dictation_names(db, ctx, ids, only_selected=True))
        recent.extend(identity.dictation_names(db, ctx, ids))
    if ids and "crm.customer.read" in ctx.permissions:
        selected.extend(crm.customer_names(db, ctx, ids).values())
    if ids and "conversations.thread.read" in ctx.permissions:
        selected.extend(knowledge.dictation_names(db, ctx, ids))
    return list(dict.fromkeys(t.strip()[:200] for t in [*selected, *recent] if t.strip()))[:200]
