"""Load data/fixtures.json (synthetic demo data) through the module services — task P3-14.

Seeding goes through the same services as the app, so every business rule, audit event and stock calculation applies.
Business IDs are stable (`fixture_id("biz-cycle")`); other rows get fresh IDs and are referenced by fixture key while
seeding. `--reset` deletes the two demo businesses first (owner role; never touches other businesses).

Example Cycle Works also gets suppliers, purchase orders, a quote request and invoices. They are not in fixtures.json,
whose data contract has no purchasing or sales tables; they are described in data/README.md.

This file is a composition root (like main.py): it may call module services directly.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.main import build_registry, load_models
from app.modules.approvals import public as approvals
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import CatalogItemCreate, ServicePartIn
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.crm.schemas import ConsentIn, ContactPointIn, CustomerCreate
from app.modules.identity import public as identity
from app.modules.identity.roles import ROLES
from app.modules.inventory import public as inventory
from app.modules.inventory import service as inventory_service
from app.modules.inventory.schemas import ReorderRuleIn
from app.modules.knowledge import service as knowledge
from app.modules.knowledge.schemas import ArticleCreate
from app.modules.marketing import service as marketing
from app.modules.marketing.schemas import CampaignCreate, SegmentDefinition, SegmentIn
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.modules.org import service as org_service
from app.modules.org.schemas import LocationUpdate, OpeningHoursItem
from app.modules.purchasing import quote_service as purchasing_quotes
from app.modules.purchasing import service as purchasing
from app.modules.purchasing import service_receipts as purchasing_receipts
from app.modules.purchasing import service_suppliers as purchasing_suppliers
from app.modules.purchasing.schemas import (
    POCreate,
    POLineIn,
    ReceiptIn,
    ReceiptLineIn,
    SupplierIn,
    SupplierItemIn,
)
from app.modules.purchasing.schemas_quotes import QuoteLine, RecordSupplierQuote, RequestLine, SendQuoteRequests
from app.modules.sales import service as sales
from app.modules.sales import service_creation as sales_creation
from app.modules.sales.schemas import DocumentCreate, LineIn
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import ResourceIn, RuleIn
from app.platform import clock
from app.platform.config import get_settings, is_regulated
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.ids import fixture_id
from app.platform.models import Base

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures.json"


@dataclass
class Keys:
    """Fixture key -> created UUID, per business."""

    ids: dict[str, uuid.UUID] = field(default_factory=dict)

    def __getitem__(self, key: str) -> uuid.UUID:
        return self.ids[key]


def _ctx(business_id: uuid.UUID, user_id: uuid.UUID | None = None, roles: tuple[str, ...] = ("owner",)) -> Context:
    perms = frozenset().union(*(ROLES[r] for r in roles))
    return Context(
        request_id="seed",
        actor_kind="user" if user_id else "system",
        user_id=user_id,
        business_id=business_id,
        permissions=perms,
    )


def reset(business_ids: list[uuid.UUID], slugs: list[str] | None = None) -> None:
    """Delete everything of the demo businesses, children first (owner role bypasses RLS and append-only grants).

    Businesses are matched by fixture ID or by demo slug (a demo slug may have been bootstrapped by hand in dev).
    """
    load_models()
    url = get_settings().database_migration_url
    if not url:
        raise SystemExit("DATABASE_MIGRATION_URL is required for --reset")
    engine = create_engine(url)
    existing = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        by_slug = conn.execute(
            text("select id from businesses where slug = any(cast(:s as text[]))"), {"s": slugs or []}
        ).scalars()
        ids = sorted({str(b) for b in business_ids} | {str(b) for b in by_slug})
        # Rows that reference the businesses only indirectly.
        conn.execute(
            text(
                "delete from event_deliveries where event_id in "
                "(select id from domain_events where business_id = any(cast(:ids as uuid[])))"
            ),
            {"ids": ids},
        )
        conn.execute(
            text("update sessions set active_business_id = null where active_business_id = any(cast(:ids as uuid[]))"),
            {"ids": ids},
        )
        # Per-business rate-limit counters ("chat-start:<business_id>:<ip>") would otherwise outlive the reset.
        conn.execute(
            text("delete from rate_limits where key like any(cast(:patterns as text[]))"),
            {"patterns": [f"%:{i}:%" for i in ids]},
        )
        for table in reversed(Base.metadata.sorted_tables):
            if table.name not in existing:
                continue
            if table.name == "businesses":
                conn.execute(text("delete from businesses where id = any(cast(:ids as uuid[]))"), {"ids": ids})
            elif "business_id" in table.c:
                conn.execute(
                    text(f'delete from "{table.name}" where business_id = any(cast(:ids as uuid[]))'),  # noqa: S608 - our metadata
                    {"ids": ids},
                )
    engine.dispose()


def _rows(tables: dict[str, list[dict[str, Any]]], name: str, business_key: str) -> list[dict[str, Any]]:
    return [r for r in tables.get(name, []) if r.get("business_id") == business_key]


# ------------------------------------------------------------ procurement and payment showcase (Example Cycle Works)
# Demo data for the quote comparison, the late-order list and the overdue-invoice list (AI-12, AI-15). No date below is
# absolute: every day is counted from the business-local "today" of the seed run, so "late" and "overdue" stay true
# whenever the seed runs (tests freeze the clock, the demo uses the real one).
#
# Three suppliers sell "Brake pads (pair)", each with a different price, lead time and delivery history:
#   Example Cycle Wholesale  $10.50  16 days  cheapest; on time for 1 of its last 3 orders (reliability 0.333)
#   Example Parts Supply     $12.00   3 days  the existing supplier; no completed order yet, so the default 0.8
#   Example Brake Direct     $14.00   2 days  dearest and fastest; on time for 3 of its last 3 orders (1.0)
# The cheapest supplier therefore does not win the comparison (scores: data/README.md).
PARTS_SUPPLY, BRAKE_DIRECT, CYCLE_WHOLESALE = "Example Parts Supply", "Example Brake Direct", "Example Cycle Wholesale"
PADS, CABLES = "item-brake-pads", "item-cable-set"

# The two new suppliers: name, e-mail, notes, items (fixture key, supplier SKU, last price in minor units, lead days).
SHOWCASE_SUPPLIERS = (
    (
        BRAKE_DIRECT,
        "brakes@example.com",
        "Courier delivery within days, at a higher price (synthetic).",
        ((PADS, "BP-DIR-01", 1400, 2),),
    ),
    (
        CYCLE_WHOLESALE,
        "wholesale@example.com",
        "Lowest prices; ships from a central warehouse (synthetic).",
        ((PADS, "BP-WHS-10", 1050, 16), (CABLES, "CS-WHS-05", 1400, 10)),
    ),
)

# Earlier orders of two pairs of brake pads, oldest first: supplier, days ago ordered, days ago received. Expected
# delivery is the order day plus the supplier's lead time: Cycle Wholesale (16 days) was 1 and 4 days late and on time
# once, Brake Direct (2 days) was on time three times. Receiving adds stock, so every delivery is followed by the same
# quantity used on brake services and the demo stock stays at 3 on hand.
PURCHASE_HISTORY = (
    (CYCLE_WHOLESALE, 66, 49),
    (BRAKE_DIRECT, 62, 60),
    (CYCLE_WHOLESALE, 45, 29),
    (BRAKE_DIRECT, 41, 40),
    (CYCLE_WHOLESALE, 26, 6),
    (BRAKE_DIRECT, 19, 17),
)
HISTORY_QUANTITY = Decimal("2")

# The late order: five cable sets, sent 14 days ago; Cycle Wholesale's 10-day lead time made them due 4 days ago.
LATE_ORDER_SUPPLIER, LATE_ORDER_ITEM = CYCLE_WHOLESALE, CABLES
LATE_ORDER_QUANTITY, LATE_ORDER_DAYS_AGO = Decimal("5"), 14

# The open quote request: ten pairs, sent 3 days ago to the three pad suppliers; reply within 7 days, needed in 11.
QUOTE_QUANTITY, QUOTE_SENT_DAYS_AGO, QUOTE_REPLY_IN_DAYS, QUOTE_NEEDED_IN_DAYS = Decimal("10"), 3, 7, 11
QUOTE_SUPPLIERS = (PARTS_SUPPLY, BRAKE_DIRECT, CYCLE_WHOLESALE)
# Answers that came in through the public form (price and lead time are the supplier's): supplier, days ago, local time,
# quantity available, note. Parts Supply has not answered: the demo answers for it on the public page.
QUOTE_ANSWERS = (
    (BRAKE_DIRECT, 2, time(14, 20), 12, "Next-day courier delivery included (synthetic)."),
    (CYCLE_WHOLESALE, 1, time(9, 5), 40, "Delivery included; ships from the warehouse (synthetic)."),
)

# The overdue invoice: two tune-ups for Example Courier Co., issued 24 days ago on net-14 terms, so due 10 days ago.
OVERDUE_CUSTOMER, OVERDUE_SERVICE, OVERDUE_QUANTITY = "cust-courier", "svc-tuneup", Decimal("2")
OVERDUE_ISSUED_DAYS_AGO, OVERDUE_TERMS_DAYS = 24, 14


def _offer(supplier: str, item_key: str) -> tuple[int, int]:
    """Last price (minor units) and lead time (days) of a new showcase supplier for an item."""
    for name, _, _, items in SHOWCASE_SUPPLIERS:
        for key, _, price, lead in items:
            if (name, key) == (supplier, item_key):
                return price, lead
    raise KeyError((supplier, item_key))


def _local(zone: ZoneInfo, day: date, at: time) -> datetime:
    """The UTC instant of a business-local wall-clock time, built from the local date so DST never shifts the day."""
    return datetime.combine(day, at, tzinfo=zone).astimezone(UTC)


@contextmanager
def _as_of(instant: datetime) -> Iterator[None]:
    """Run a block as if it happened at `instant`: stored timestamps and business-local dates follow the clock.

    History needs real past dates (an invoice issued before it fell due, an order sent before it was expected) and the
    services take them from the clock. The clock keeps ticking from `instant`; the previous one (the real clock, or a
    test's frozen clock) is restored afterwards.
    """
    previous = clock._clock
    started = previous()
    clock.set_clock(lambda: instant + (previous() - started))
    try:
        yield
    finally:
        clock.set_clock(previous)


def _seed_showcase(
    db: Session, ctx: Context, k: Keys, supplier_ids: dict[str, uuid.UUID], *, zone: ZoneInfo, currency: str
) -> dict[str, int]:
    """Add the two suppliers, their order history, the late order, the quote request and the overdue invoice."""
    today = clock.now().astimezone(zone).date()
    for name, email, notes, items in SHOWCASE_SUPPLIERS:
        supplier = purchasing_suppliers.create(
            db,
            ctx,
            SupplierIn(
                name=name,
                email=email,
                notes=notes,
                items=[
                    SupplierItemIn(
                        catalog_item_id=k[key], supplier_sku=sku, last_price_minor=price, lead_time_days=lead
                    )
                    for key, sku, price, lead in items
                ],
            ),
        )
        supplier_ids[name] = supplier.id

    for name, ordered, received in PURCHASE_HISTORY:
        ordered_on, received_on = today - timedelta(days=ordered), today - timedelta(days=received)
        with _as_of(_local(zone, ordered_on, time(9, 30))):
            po = purchasing.create(
                db,
                ctx,
                POCreate(
                    supplier_id=supplier_ids[name],
                    notes="Earlier order (synthetic history).",
                    lines=[
                        POLineIn(
                            catalog_item_id=k[PADS], quantity=HISTORY_QUANTITY, unit_cost_minor=_offer(name, PADS)[0]
                        )
                    ],
                ),
            )
            purchasing.transition(db, ctx, po.id, po.version, "approve")
        with _as_of(_local(zone, ordered_on, time(10, 0))):
            purchasing.transition(db, ctx, po.id, po.version, "send")
        with _as_of(_local(zone, received_on, time(12, 0))):
            line = purchasing.detail(db, ctx, po).lines[0]
            purchasing_receipts.receive(
                db,
                ctx,
                po.id,
                ReceiptIn(version=po.version, lines=[ReceiptLineIn(po_line_id=line.id, quantity=HISTORY_QUANTITY)]),
            )
        with _as_of(_local(zone, received_on, time(16, 0))):
            inventory.record_movement(
                db,
                ctx,
                inventory.MovementIn(
                    item_id=k[PADS],
                    quantity=-HISTORY_QUANTITY,
                    reason="consumption",
                    note="Used on brake services (synthetic history).",
                ),
            )

    ordered_on = today - timedelta(days=LATE_ORDER_DAYS_AGO)
    with _as_of(_local(zone, ordered_on, time(9, 30))):
        late = purchasing.create(
            db,
            ctx,
            POCreate(
                supplier_id=supplier_ids[LATE_ORDER_SUPPLIER],
                notes="Cable restock for the winter tune-up season (synthetic).",
                lines=[
                    POLineIn(
                        catalog_item_id=k[LATE_ORDER_ITEM],
                        quantity=LATE_ORDER_QUANTITY,
                        unit_cost_minor=_offer(LATE_ORDER_SUPPLIER, LATE_ORDER_ITEM)[0],
                    )
                ],
            ),
        )
        purchasing.transition(db, ctx, late.id, late.version, "approve")
    with _as_of(_local(zone, ordered_on, time(10, 0))):
        late = purchasing.transition(db, ctx, late.id, late.version, "send")
    if late.expected_delivery_date is None or late.expected_delivery_date >= today:
        raise RuntimeError("seed: the late purchase order is not late")

    _seed_quote_request(db, ctx, k[PADS], supplier_ids, zone=zone, today=today, currency=currency)

    issued_on = today - timedelta(days=OVERDUE_ISSUED_DAYS_AGO)
    with _as_of(_local(zone, issued_on, time(10, 0))):
        invoice = sales_creation.create(
            db,
            ctx,
            "invoice",
            DocumentCreate(
                customer_id=k[OVERDUE_CUSTOMER],
                due_date=issued_on + timedelta(days=OVERDUE_TERMS_DAYS),
                notes="Net 14 days (synthetic).",
                lines=[LineIn(catalog_item_id=k[OVERDUE_SERVICE], quantity=OVERDUE_QUANTITY)],
            ),
        )
        invoice = sales.transition(db, ctx, "invoice", invoice.id, invoice.version, "issue")
    if invoice.due_date is None or invoice.due_date >= today:
        raise RuntimeError("seed: the overdue invoice is not overdue")
    return {
        "suppliers": len(SHOWCASE_SUPPLIERS),
        "purchase_orders": len(PURCHASE_HISTORY) + 1,
        "quote_requests": 1,
        "invoices": 1,
    }


def _seed_quote_request(
    db: Session,
    ctx: Context,
    pads: uuid.UUID,
    supplier_ids: dict[str, uuid.UUID],
    *,
    zone: ZoneInfo,
    today: date,
    currency: str,
) -> None:
    """Send the open request to the three pad suppliers, then record the answers that came in through the form."""
    with _as_of(_local(zone, today - timedelta(days=QUOTE_SENT_DAYS_AGO), time(10, 0))):
        request = purchasing_quotes.send_requests(
            db,
            ctx,
            SendQuoteRequests(
                lines=[RequestLine(item_id=pads, quantity=QUOTE_QUANTITY)],
                supplier_ids=[supplier_ids[name] for name in QUOTE_SUPPLIERS],
                needed_by=today + timedelta(days=QUOTE_NEEDED_IN_DAYS),
                reply_by=today + timedelta(days=QUOTE_REPLY_IN_DAYS),
                message=(
                    "We are stocking up on brake pads before winter. Please quote ten pairs delivered to our "
                    "workshop and tell us when they would arrive (synthetic demo request)."
                ),
            ),
            "seed:example-cycle-works:brake-pads",
        )
    # A supplier answers as a visitor of the public form, like the page the e-mail links to.
    visitor = Context(
        request_id="seed",
        actor_kind="visitor",
        user_id=None,
        business_id=ctx.business_id,
        permissions=frozenset({"public"}),
    )
    for name, days_ago, at, available, note in QUOTE_ANSWERS:
        price, lead = _offer(name, PADS)
        with _as_of(_local(zone, today - timedelta(days=days_ago), at)):
            purchasing_quotes.record_quote(
                db,
                visitor,
                RecordSupplierQuote(
                    quote_request_id=request.id,
                    supplier_id=supplier_ids[name],
                    lines=[QuoteLine(item_id=pads, unit_price_minor=price, quantity_available=Decimal(available))],
                    currency=currency,
                    lead_time_days=lead,
                    valid_until=today + timedelta(days=30),
                    notes=note,
                    source="form",
                ),
                from_form=True,
            )


def seed_business(tables: dict[str, list[dict[str, Any]]], b: dict[str, Any]) -> dict[str, int]:
    if is_regulated() and b.get("synthetic_demo"):
        raise SystemExit("DATA_PROFILE=regulated refuses synthetic demo businesses")
    key = b["id"]
    business_id = fixture_id(key)
    ctx = _ctx(business_id)
    k = Keys()
    counts: dict[str, int] = {}
    with transaction(ctx) as db:
        org.create_business(
            db, ctx, name=b["name"], slug=b["slug"], timezone=b["timezone"], currency=b["currency"], locale=b["locale"]
        )
        settings, settings_version = org_service.get_settings(db, ctx)
        org_service.update_settings(
            db,
            ctx,
            org.BusinessSettings.model_validate(
                {**settings.model_dump(), "self_service_enabled": True, **json.loads(b.get("settings_json", "{}"))}
            ),
            settings_version,
        )
        from app import seed_dental

        seed_dental.configure(db, ctx, b)
        default = org.get_default_location(db, ctx)
        for loc in _rows(tables, "locations", key):
            if loc["is_default"]:
                org_service.update_location(
                    db,
                    ctx,
                    default.id,
                    LocationUpdate(version=default.version, name=loc["name"], timezone=loc["timezone"]),
                )
                k.ids[loc["id"]] = default.id
        hours = [
            OpeningHoursItem(weekday=h["weekday"], opens=h["opens"], closes=h["closes"])
            for h in _rows(tables, "opening_hours", key)
        ]
        org_service.replace_opening_hours(db, ctx, default.id, hours)

        users = {u["id"]: u for u in tables["users"]}
        roles_by_membership: dict[str, list[str]] = {}
        for g in _rows(tables, "role_grants", key):
            roles_by_membership.setdefault(g["membership_id"], []).append(g["role"])
        for m in _rows(tables, "memberships", key):
            u = users[m["user_id"]]
            k.ids[u["id"]] = identity.ensure_user(db, u["email"], u["display_name"])
            identity.ensure_membership(db, ctx, k[u["id"]], roles_by_membership.get(m["id"], []), m.get("title"))
        counts["staff"] = len(_rows(tables, "memberships", key))
        for article in _rows(tables, "kb_articles", key):
            made = knowledge.create(
                db,
                ctx,
                ArticleCreate(
                    **{field: article[field] for field in ("title", "slug", "visibility", "body_markdown")},
                    tags=json.loads(article["tags_json"]),
                ),
            )
            if article["status"] == "published":
                knowledge.publish(db, ctx, made.id, made.version)
        counts["kb_articles"] = len(_rows(tables, "kb_articles", key))

        for rule in _rows(tables, "autonomy_rules", key):
            grantor_ctx = _ctx(business_id, k[rule["granted_by"]], ("owner",))
            rule_data = approvals.RulePut(
                action_type=rule["action_type"],
                level=rule["level"],
                shadow=rule["shadow"],
                daily_cap=rule["daily_cap"],
                active=rule["active"],
                version=0,
            )
            saved_rule = approvals.put_autonomy_rule(db, grantor_ctx, rule_data)
            if not rule["shadow"]:
                # Normal creation always starts shadow. The synthetic fixture explicitly authorizes going live.
                approvals.put_autonomy_rule(
                    db, grantor_ctx, rule_data.model_copy(update={"version": saved_rule.version})
                )

        for item in _rows(tables, "catalog_items", key):
            data = {
                f: item[f]
                for f in (
                    "kind",
                    "sku",
                    "name",
                    "unit",
                    "price_minor",
                    "duration_minutes",
                    "buffer_minutes",
                    "resource_kind",
                    "is_stock_tracked",
                    "is_bookable_online",
                )
            }
            data["required_resources"] = json.loads(item.get("required_resources_json", "[]"))
            data["recall_interval_months"] = item.get("recall_interval_months")
            k.ids[item["id"]] = catalog.create_item(db, ctx, CatalogItemCreate(**data)).id
        parts: dict[str, list[ServicePartIn]] = {}
        for p in _rows(tables, "service_parts", key):
            parts.setdefault(p["service_id"], []).append(
                ServicePartIn(item_id=k[p["item_id"]], quantity=Decimal(str(p["quantity"])))
            )
        for service_key, service_parts in parts.items():
            catalog.replace_parts(db, ctx, k[service_key], service_parts)
        counts["catalog_items"] = len(_rows(tables, "catalog_items", key))

        contacts: dict[str, list[ContactPointIn]] = {}
        for cp in _rows(tables, "contact_points", key):
            contacts.setdefault(cp["customer_id"], []).append(
                ContactPointIn(type=cp["type"], value=cp["value"], is_primary=bool(cp["is_primary"]))
            )
        tags: dict[str, list[str]] = {}
        tag_names = {t["id"]: t["name"] for t in _rows(tables, "tags", key)}
        for ct in _rows(tables, "customer_tags", key):
            tags.setdefault(ct["customer_id"], []).append(tag_names[ct["tag_id"]])
        for c in _rows(tables, "customers", key):
            created = crm.create_customer(
                db,
                ctx,
                CustomerCreate(
                    kind=c["kind"],
                    display_name=c["display_name"],
                    contact_points=contacts.get(c["id"], []),
                    tags=tags.get(c["id"], []),
                ),
            )
            k.ids[c["id"]] = created.id
        for cr in sorted(_rows(tables, "consent_records", key), key=lambda r: r["recorded_at"]):
            crm.record_consent(
                db,
                ctx,
                k[cr["customer_id"]],
                ConsentIn(
                    channel=cr["channel"],
                    purpose=cr["purpose"],
                    status=cr["status"],
                    source=cr["source"],
                    evidence=cr["evidence"],
                ),
            )
        counts["customers"] = len(_rows(tables, "customers", key))

        for sup in _rows(tables, "suppressions", key):
            marketing.add_suppression(db, ctx, sup["value_normalized"], sup["reason"])
        for seg in _rows(tables, "segments", key):
            definition = SegmentDefinition.model_validate(json.loads(seg["definition_json"]))
            k.ids[seg["id"]] = marketing.create_segment(db, ctx, SegmentIn(name=seg["name"], definition=definition)).id
        first_revision = {
            r["campaign_id"]: r for r in _rows(tables, "campaign_revisions", key) if r["revision_no"] == 1
        }
        for camp in _rows(tables, "campaigns", key):
            rev = first_revision[camp["id"]]
            k.ids[camp["id"]] = marketing.create_campaign(
                db,
                ctx,
                CampaignCreate(
                    name=camp["name"],
                    purpose=camp.get("purpose"),
                    segment_id=k[camp["segment_id"]],
                    subject=rev["subject"],
                    body_markdown=rev["body_markdown"],
                ),
            ).id
        counts["campaigns"] = len(_rows(tables, "campaigns", key))

        for mv in sorted(_rows(tables, "stock_movements", key), key=lambda r: r["occurred_at"]):
            inventory.record_movement(
                db,
                ctx,
                inventory.MovementIn(
                    item_id=k[mv["item_id"]],
                    quantity=Decimal(str(mv["quantity"])),
                    reason=mv["reason"],
                    note=mv.get("note"),
                ),
            )
        for rule in _rows(tables, "reorder_rules", key):
            inventory_service.put_rule(
                db,
                ctx,
                k[rule["item_id"]],
                ReorderRuleIn(
                    reorder_point=Decimal(str(rule["reorder_point"])),
                    target_level=Decimal(str(rule["target_level"])),
                    lead_time_days=rule["lead_time_days"],
                ),
            )

        for r in _rows(tables, "resources", key):
            res = scheduling.create_resource(
                db,
                ctx,
                ResourceIn(
                    name=r["name"], kind=r["kind"], user_id=k.ids.get(r["user_id"]) if r.get("user_id") else None
                ),
            )
            k.ids[r["id"]] = res.id
            rules = [
                RuleIn(weekday=x["weekday"], start_time=x["start_time"], end_time=x["end_time"])
                for x in _rows(tables, "availability_rules", key)
                if x["resource_id"] == r["id"]
            ]
            scheduling.replace_rules(db, ctx, res.id, rules)
        for resource in _rows(tables, "resources", key):
            links = [
                k[r["service_id"]]
                for r in _rows(tables, "resource_services", key)
                if r["resource_id"] == resource["id"]
            ]
            if links:
                scheduling.replace_services(db, ctx, k[resource["id"]], links)
        for a in _rows(tables, "appointments", key):
            appt = seed_dental.appointment(db, ctx, a, k, ZoneInfo(b["timezone"]))
            k.ids[a["id"]] = appt.id
        if b["slug"] == "example-dental-clinic":
            seed_dental.supplier(db, ctx, k, _rows(tables, "suppliers", key), _rows(tables, "supplier_items", key))
            counts["suppliers"] = 1
        counts["appointments"] = len(_rows(tables, "appointments", key))

        messages = {m["conversation_id"]: m for m in _rows(tables, "messages", key)}
        for c in _rows(tables, "conversations", key):
            first = messages[c["id"]]
            k.ids[c["id"]] = conversations.start_conversation(
                db,
                ctx,
                channel=c["channel"],
                body=first["body"],
                author_kind=first["author_kind"],
                customer_id=k.ids.get(c["customer_id"]) if c.get("customer_id") else None,
                subject=c.get("subject"),
                visitor_name=c.get("visitor_name"),
                visitor_email=c.get("visitor_email"),
            )
        counts["conversations"] = len(_rows(tables, "conversations", key))

    # Staff-originated proposals are created as their author (separate transaction per author).
    revisions = {r["proposal_id"]: r for r in _rows(tables, "proposal_revisions", key)}
    for p in _rows(tables, "action_proposals", key):
        author = k[p["origin_user_id"]]
        actx = _ctx(business_id, author, ("operator",))
        payload = json.loads(revisions[p["id"]]["payload_json"])
        payload = {f: (str(k[v]) if isinstance(v, str) and v in k.ids else v) for f, v in payload.items()}
        payload.pop("stock_location_id", None)
        with transaction(actx) as db:
            approvals.create_proposal(db, actx, action_type=p["action_type"], payload=payload, title=p["title"])
    counts["proposals"] = len(_rows(tables, "action_proposals", key))
    if b["slug"] == "example-cycle-works":
        with transaction(ctx) as db:
            supplier_ids: dict[str, uuid.UUID] = {}
            for name, email, item_key, sku, price in (
                (PARTS_SUPPLY, "parts@example.com", "item-brake-pads", "BP-01", 1200),
                ("Example Tyre Co.", "tyres@example.com", "item-tube", "TUBE-01", 500),
            ):
                supplier = purchasing_suppliers.create(
                    db,
                    ctx,
                    SupplierIn(
                        name=name,
                        email=email,
                        items=[
                            SupplierItemIn(
                                catalog_item_id=k[item_key],
                                supplier_sku=sku,
                                last_price_minor=price,
                                lead_time_days=3,
                            )
                        ],
                    ),
                )
                if item_key == "item-brake-pads":
                    po = purchasing.create(
                        db,
                        ctx,
                        POCreate(
                            supplier_id=supplier.id,
                            lines=[
                                POLineIn(catalog_item_id=k[item_key], quantity=Decimal("5"), unit_cost_minor=price),
                            ],
                        ),
                    )
                    purchasing.transition(db, ctx, po.id, po.version, "approve")
                supplier_ids[name] = supplier.id
            counts["suppliers"] = 2
            counts["purchase_orders"] = 1
            document = DocumentCreate(
                customer_id=k["cust-ada"],
                lines=[
                    LineIn(catalog_item_id=k["svc-brake"], quantity=Decimal("1")),
                    LineIn(catalog_item_id=k["item-brake-pads"], quantity=Decimal("1")),
                ],
            )
            quote = sales_creation.create(db, ctx, "quote", document)
            sales.transition(db, ctx, "quote", quote.id, quote.version, "send")
            invoice = sales_creation.create(db, ctx, "invoice", document)
            sales.transition(db, ctx, "invoice", invoice.id, invoice.version, "issue")
            counts["invoices"] = 1
            # Procurement and payment showcase (see the constants above `seed_business`).
            added = _seed_showcase(db, ctx, k, supplier_ids, zone=ZoneInfo(b["timezone"]), currency=b["currency"])
            for table, number in added.items():
                counts[table] = counts.get(table, 0) + number
    return counts


def seed(
    path: Path = DEFAULT_FIXTURES, *, do_reset: bool = False, business_keys: set[str] | None = None
) -> dict[str, dict[str, int]]:
    registry = build_registry()
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("synthetic") is not True:
        raise SystemExit("refusing to seed: fixtures are not marked synthetic")
    tables = doc["tables"]
    if business_keys is not None:
        known = {b["id"] for b in tables["businesses"]}
        if not business_keys or not business_keys <= known:
            raise SystemExit("refusing to seed unknown or empty business selection")
        tables["businesses"] = [b for b in tables["businesses"] if b["id"] in business_keys]
    if is_regulated() and any(b.get("synthetic_demo") for b in tables["businesses"]):
        raise SystemExit("DATA_PROFILE=regulated refuses synthetic demo businesses; no data was changed")
    business_ids = [fixture_id(b["id"]) for b in tables["businesses"]]
    if do_reset:
        reset(business_ids, [b["slug"] for b in tables["businesses"]])
    out = {}
    for b in tables["businesses"]:
        resolver = registry.public_business_resolver
        if resolver is not None and resolver(b["slug"]) is not None:
            raise SystemExit(f"{b['slug']} already exists; run with --reset to recreate the demo data")
        # The demo's suppliers and customers are example.com addresses: with SMTP configured a real provider would
        # bounce the mail (and the sender's reputation pays for it). Everything the seed queues is simulated from the
        # start; no row is rewritten afterwards.
        with notifications.simulated_delivery():
            out[b["slug"]] = seed_business(tables, b)
    return out
