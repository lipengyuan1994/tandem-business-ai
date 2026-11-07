"""Synthetic clinic seed helpers (composition root; no network calls)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modules.ai.team_schemas import AgentPatch
from app.modules.ai.team_settings import patch_settings
from app.modules.org import service as org
from app.modules.org.models import Business
from app.modules.org.schemas import BusinessSettings
from app.modules.purchasing.schemas import SupplierIn, SupplierItemIn
from app.modules.purchasing.service_suppliers import create as create_supplier
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import BookingRequest
from app.platform import clock
from app.platform.config import is_regulated
from app.platform.context import Context


def configure(db: Session, ctx: Context, business: dict[str, Any]) -> None:
    import json

    if business.get("synthetic_demo"):
        if is_regulated():
            raise SystemExit("DATA_PROFILE=regulated refuses synthetic demo businesses")
        row = db.get(Business, ctx.business_id)
        assert row is not None
        row.synthetic_demo = True
        row.public_profile = json.loads(business.get("public_profile_json", "{}"))
        settings, version = org.get_settings(db, ctx)
        org.update_settings(
            db,
            ctx,
            BusinessSettings.model_validate(
                {**settings.model_dump(), **json.loads(business.get("settings_json", "{}"))}
            ),
            version,
        )
        patch_settings(db, ctx, "customer_care", AgentPatch(version=0, enabled=True))


def appointment(db: Session, ctx: Context, row: dict[str, Any], keys: Any, zone: ZoneInfo) -> Any:
    from app.seed import _as_of

    if "days_ago" not in row:
        return scheduling.book(
            db,
            ctx,
            BookingRequest(
                customer_id=keys[row["customer_id"]],
                service_id=keys[row["service_id"]],
                resource_id=keys[row["resource_id"]],
                starts_at=datetime.fromisoformat(row["starts_at"]),
                override_hours=True,
            ),
        )
    today = clock.now().astimezone(zone).date()
    day = today - timedelta(days=row["days_ago"])
    while day.weekday() > 4:
        day -= timedelta(days=1)
    start = datetime.combine(day, time(10), zone).astimezone(UTC)
    # History uses the same qualified slot/booking path, at its actual synthetic time.
    with _as_of(start - timedelta(days=1)):
        made = scheduling.book(
            db,
            ctx,
            BookingRequest(
                customer_id=keys[row["customer_id"]],
                service_id=keys[row["service_id"]],
                resource_id=keys[row["resource_id"]],
                starts_at=start,
            ),
        )
    if row["status"] == "completed":
        with _as_of(made.ends_at):
            scheduling.complete(db, ctx, made.id, made.version)
    return made


def supplier(
    db: Session, ctx: Context, keys: Any, suppliers: list[dict[str, Any]], items: list[dict[str, Any]]
) -> None:
    for row in suppliers:
        create_supplier(
            db,
            ctx,
            SupplierIn(
                name=row["name"],
                email=row["email"],
                items=[
                    SupplierItemIn(
                        catalog_item_id=keys[item["catalog_item_id"]],
                        supplier_sku=item["supplier_sku"],
                        last_price_minor=item["last_price_minor"],
                        lead_time_days=item["lead_time_days"],
                    )
                    for item in items
                    if item["supplier_id"] == row["id"]
                ],
            ),
        )
