"""Hold and propose tools: bookings, replies, customers, reorders (AI-07; contracts §10, integration-map §C/§D/§F).

The only direct write is `hold_slot` (a reversible 30-minute scheduling hold for an existing customer). Everything else
creates a proposal that a person approves. Ids and numbers the model must not invent are filled in here: the hold's
service, resource and time come from the hold; a new customer's contacts from `RunState.server_facts`; reorder
quantities from `inventory.list_below_reorder`. Idempotency per trigger comes from the framework (`ai_effects`).

Run-scoped state used: `RunState.holds` (holds placed in this run), `RunState.read_conversation_ids` (only those get
replies), `RunState.server_facts` keys `visitor_name`, `visitor_email`, `visitor_phone` (never shown to the model).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.catalog import public as catalog
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.modules.scheduling import public as scheduling
from app.platform import clock
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid, NotFound

from .. import effects
from .base import RunState, Tool
from .common import ToolArgs, local_label, qty, utc
from .proposing import PlainText, gather, note, propose, run_conversation, tool_result

PROPOSE = "ai.assistant.use"
HOLD = "scheduling.appointment.write"
BOOKING = "scheduling.book_appointment"


# ----------------------------------------------------------------------------------------------- hold_slot
class HoldSlotArgs(ToolArgs):
    service_id: uuid.UUID
    resource_id: uuid.UUID
    starts_at: AwareDatetime
    customer_id: uuid.UUID

    @field_validator("starts_at")
    @classmethod
    def bounded_start(cls, value: datetime) -> datetime:
        if abs(value - clock.now()) > timedelta(days=366):
            raise ValueError("date must be within one year of now")
        return value


def _hold_slot(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, HoldSlotArgs)
    customer = crm.get_customer_summary(db, ctx, args.customer_id)  # existing customers only (not-found otherwise)
    hold = scheduling.place_hold(
        db, ctx, service_id=args.service_id, resource_id=args.resource_id, starts_at=args.starts_at,
        customer_id=customer.id,
    )  # fmt: skip
    timezone = org.get_business_brief(db, ctx).timezone
    return {
        "hold_id": hold.hold_id, "starts_at": utc(hold.starts_at), "ends_at": utc(hold.ends_at),
        "local_label": local_label(hold.starts_at, timezone), "hold_expires_at": utc(hold.hold_expires_at),
    }  # fmt: skip


# ----------------------------------------------------------------------------------------------- propose_booking
class ReplyArgs(ToolArgs):
    conversation_id: uuid.UUID
    body: PlainText = Field(max_length=4000)


class ProposeBookingArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("hold_id", "customer_id", "service_id", "resource_id", "starts_at")
    hold_id: uuid.UUID | None = Field(default=None, description="Existing customer: the hold from hold_slot.")
    customer_id: uuid.UUID | None = Field(default=None, description="Existing customer (must match the hold).")
    service_id: uuid.UUID | None = Field(default=None, description="New customer: the service of the chosen slot.")
    resource_id: uuid.UUID | None = Field(default=None, description="New customer: the slot's resource.")
    starts_at: AwareDatetime | None = Field(
        default=None, description="New customer: the slot's start (from find_slots)."
    )

    @field_validator("starts_at")
    @classmethod
    def bounded_start(cls, value: datetime | None) -> datetime | None:
        if value is not None and abs(value - clock.now()) > timedelta(days=366):
            raise ValueError("date must be within one year of now")
        return value

    notes: PlainText | None = Field(default=None, max_length=2000)
    reply: ReplyArgs | None = Field(default=None, description="Reply to send when the booking is approved.")


def _one_booking_per_run(db: Session, ctx: Context, state: RunState) -> None:
    mine = {e.ref_id for e in effects.effects_for_trigger(db, ctx, state.trigger_key) if e.kind == "proposal"}
    mine.update(state.proposals.values())
    if mine and any(p.id in mine for p in approvals.find_open_proposals(db, ctx, action_types=[BOOKING])):
        raise Conflict("ai.one_booking_per_run", detail="this run already proposed a booking")


def _check_reply(state: RunState, reply: ReplyArgs | None) -> dict[str, Any] | None:
    if reply is None:
        return None
    if reply.conversation_id not in state.read_conversation_ids:
        raise Conflict("ai.conversation_not_read", detail="read the conversation with get_conversation first")
    return {"conversation_id": str(reply.conversation_id), "body": reply.body}


def _held_booking(db: Session, ctx: Context, args: ProposeBookingArgs, state: RunState) -> tuple[dict[str, Any], ...]:
    assert args.hold_id is not None
    if args.hold_id not in state.holds:
        raise Conflict("ai.hold_not_in_run", detail="use the hold_id that hold_slot returned in this run")
    try:
        hold = scheduling.get_appointment(db, ctx, args.hold_id)
    except NotFound as exc:
        raise Conflict("ai.hold_not_in_run", detail="hold not found") from exc
    if hold.status != "held" or hold.hold_expires_at is None or hold.hold_expires_at <= clock.now():
        raise Conflict("ai.hold_expired", detail="the hold expired or was released; call hold_slot again")
    if hold.proposal_id is not None:
        raise Conflict("ai.hold_not_available", detail=f"hold is {hold.status}")
    given = (args.customer_id, args.service_id, args.resource_id, args.starts_at)
    actual = (hold.customer_id, hold.service_id, hold.resource_id, hold.starts_at)
    if any(g is not None and g != a for g, a in zip(given, actual, strict=True)):
        raise Conflict("ai.hold_mismatch", detail="customer, service, resource or time differ from the hold")
    payload = {
        "customer": {"id": str(hold.customer_id)}, "service_id": str(hold.service_id),
        "resource_id": str(hold.resource_id), "starts_at": utc(hold.starts_at), "hold_id": str(hold.id),
    }  # fmt: skip
    title = f"Book {hold.service_name} for {hold.customer_name}, {local_label(hold.starts_at, hold.timezone)}"
    facts = {"hold_id": hold.id, "customer": hold.customer_name, "service": hold.service_name,
             "resource": hold.resource_name, "starts_at": utc(hold.starts_at),
             "hold_expires_at": utc(hold.hold_expires_at)}  # fmt: skip
    return payload, {"title": title, "facts": facts, "ids": [hold.id, hold.customer_id, hold.service_id]}


def _slot_offered(db: Session, ctx: Context, service: catalog.ServiceRequirements, resource: uuid.UUID,
                  starts_at: datetime) -> scheduling.SlotOut:  # fmt: skip
    span = timedelta(minutes=service.duration_minutes + service.buffer_minutes + 1)
    query = scheduling.SlotQuery(service_id=service.service_id, earliest=starts_at, latest=starts_at + span,
                                 resource_id=resource, limit=10)  # fmt: skip
    for slot in scheduling.find_slots(db, ctx, query):
        if slot.starts_at == starts_at and slot.resource_id == resource:
            return slot
    raise Conflict("scheduling.slot_unavailable", detail="choose a slot returned by find_slots")


def visitor_customer(state: RunState) -> dict[str, Any]:
    """CustomerCreate for the run's visitor, from server facts only (no consents, nothing from the model)."""
    facts = state.server_facts
    name = str(facts.get("visitor_name") or "").strip()
    if not name:
        raise Conflict("ai.no_visitor_details", detail="no visitor details for a new customer")
    points: list[dict[str, Any]] = []
    for kind in ("email", "phone"):
        if value := str(facts.get(f"visitor_{kind}") or "").strip():
            points.append({"type": kind, "value": value, "is_primary": True})
    return {"kind": "person", "display_name": name[:200], "contact_points": points}


def _new_customer_booking(
    db: Session, ctx: Context, args: ProposeBookingArgs, state: RunState
) -> tuple[dict[str, Any], ...]:
    if args.customer_id is not None:
        raise Conflict("ai.hold_required", detail="an existing customer needs hold_slot first, then its hold_id")
    if args.service_id is None or args.resource_id is None or args.starts_at is None:
        raise Invalid("ai.slot_required", detail="give service_id, resource_id and starts_at of a find_slots slot")
    new = visitor_customer(state)
    service = catalog.get_service_requirements(db, ctx, args.service_id)
    slot = _slot_offered(db, ctx, service, args.resource_id, args.starts_at)
    payload = {
        "customer": {"new": new}, "service_id": str(service.service_id), "resource_id": str(slot.resource_id),
        "starts_at": utc(slot.starts_at),
    }  # fmt: skip
    title = f"Book {service.name} for {new['display_name']} (new customer), {slot.local_label}"
    facts = {"customer": f"{new['display_name']} (new)", "service": service.name, "resource": slot.resource_name,
             "starts_at": utc(slot.starts_at), "slot": "not held"}  # fmt: skip
    return payload, {"title": title, "facts": facts, "ids": [service.service_id], "not_held": True}


def _propose_booking(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposeBookingArgs)
    _one_booking_per_run(db, ctx, state)
    reply = _check_reply(state, args.reply)
    build = _held_booking if args.hold_id is not None else _new_customer_booking
    payload, info = build(db, ctx, args, state)
    if args.notes is not None:
        payload["notes"] = args.notes
    if reply is not None:
        payload["reply"] = reply
    own = [tool_result("propose_booking", args.hold_id, info["facts"])]
    if info.get("not_held"):
        own.append(note("Slot not held: new customer. Availability is re-checked at approval."))
    conversation = args.reply.conversation_id if args.reply else run_conversation(db, ctx, state)
    # The service's parts, so stock checks on them (check_stock) are cited too.
    parts = catalog.get_service_requirements(db, ctx, uuid.UUID(payload["service_id"])).parts
    evidence = gather(db, ctx, state, own, [*info["ids"], conversation, *(p.item_id for p in parts)])
    data = propose(db, ctx, action_type=BOOKING, payload=payload, title=info["title"], evidence=evidence,
                   conversation_id=conversation)  # fmt: skip
    if args.hold_id is not None:
        scheduling.bind_hold(db, ctx, args.hold_id, data["proposal_id"])  # same transaction as the proposal
    data["slot_held"] = args.hold_id is not None
    return data


# ----------------------------------------------------------------------------------------------- propose_reply
class ProposeReplyArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("conversation_id",)
    conversation_id: uuid.UUID
    text: PlainText = Field(max_length=4000)


CHANNELS = {"web_chat": "web chat", "email": "email", "phone_log": "phone", "walk_in": "walk-in", "sms": "SMS"}


def _propose_reply(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposeReplyArgs)
    if args.conversation_id not in state.read_conversation_ids:
        raise Conflict("ai.conversation_not_read", detail="read the conversation with get_conversation first")
    thread = conversations.get_thread(db, ctx, args.conversation_id, last_n=5)
    who = thread.visitor_name or "visitor"
    if thread.customer_id is not None:
        ctx.require("crm.customer.read")
        who = crm.customer_names(db, ctx, {thread.customer_id}).get(thread.customer_id, who)
    title = f"Reply to {who} ({CHANNELS.get(thread.channel, thread.channel.replace('_', ' '))})"
    payload = {"conversation_id": str(args.conversation_id), "body": args.text}
    facts = {"conversation_id": thread.conversation_id, "channel": thread.channel, "status": thread.status,
             "messages": len(thread.messages)}  # fmt: skip
    own = [tool_result("propose_reply", thread.conversation_id, facts)]
    evidence = gather(db, ctx, state, own, [thread.conversation_id, thread.customer_id])
    return propose(db, ctx, action_type="conversations.send_reply", payload=payload, title=title, evidence=evidence,
                   conversation_id=thread.conversation_id)  # fmt: skip


# ----------------------------------------------------------------------------------------------- propose_customer
class ProposeCustomerArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("display_name",)
    display_name: PlainText = Field(max_length=200)
    kind: Literal["person", "organization"] = "person"
    given_name: PlainText | None = Field(default=None, max_length=100)
    family_name: PlainText | None = Field(default=None, max_length=100)
    organization_name: PlainText | None = Field(default=None, max_length=200)
    tags: list[PlainText] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def short_tags(cls, value: list[str]) -> list[str]:
        if any(len(t) > 50 for t in value):
            raise ValueError("tags are at most 50 characters")
        return value


def _propose_customer(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposeCustomerArgs)
    payload: dict[str, Any] = {"kind": args.kind, "display_name": args.display_name.strip(), "tags": args.tags}
    for field in ("given_name", "family_name", "organization_name"):
        if (value := getattr(args, field)) is not None:
            payload[field] = value
    # Contact points only from the server's visitor facts; consents are never set by the assistant.
    payload["contact_points"] = [
        {"type": kind, "value": value, "is_primary": True}
        for kind in ("email", "phone")
        if (value := str(state.server_facts.get(f"visitor_{kind}") or "").strip())
    ]
    contact_types = [p["type"] for p in payload["contact_points"]]
    title = f"Add customer {payload['display_name']}" + (f" ({', '.join(contact_types)})" if contact_types else "")
    own = [tool_result("propose_customer", None, {"display_name": payload["display_name"], "kind": args.kind,
                                                  "contact_types": contact_types})]  # fmt: skip
    evidence = gather(db, ctx, state, own, [])
    return propose(db, ctx, action_type="crm.create_customer", payload=payload, title=title, evidence=evidence)


# ----------------------------------------------------------------------------------------------- propose_reorder
class ProposeReorderArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("item_ids",)
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    note: PlainText | None = Field(default=None, max_length=1000)
    reason_text: PlainText | None = Field(default=None, max_length=300)

    @field_validator("item_ids")
    @classmethod
    def unique_sorted(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return sorted(set(value))


def _propose_reorder(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposeReorderArgs)
    below = {s.item_id: s for s in inventory.list_below_reorder(db, ctx)}
    missing = [i for i in args.item_ids if i not in below]
    if missing:
        raise Invalid("ai.not_below_reorder", detail="not below reorder point: " + ", ".join(map(str, missing)))
    rows = [below[i] for i in args.item_ids]
    lines = [{"item_id": str(s.item_id), "quantity": qty(s.suggested),
              "reason_text": s.explanation[:300] if args.reason_text is None else args.reason_text}
             for s in rows]  # fmt: skip
    payload: dict[str, Any] = {"lines": lines}
    if args.note is not None:
        payload["note"] = args.note
    parts = [f"{s.item_name} ×{qty(s.suggested)}" for s in rows]
    title = f"Reorder {parts[0]}" if len(parts) == 1 else f"Reorder {len(parts)} items: {', '.join(parts)}"
    own = [tool_result("propose_reorder", None, [{"item": s.item_name, "available": qty(s.available),
                                                  "reorder_point": qty(s.reorder_point),
                                                  "suggested": qty(s.suggested)} for s in rows])]  # fmt: skip
    evidence = gather(db, ctx, state, own, args.item_ids)
    return propose(db, ctx, action_type="inventory.create_reorder_draft", payload=payload, title=title,
                   evidence=evidence)  # fmt: skip


TOOLS = (
    Tool(
        name="hold_slot",
        description="Hold a free slot (from find_slots) for 30 minutes for an EXISTING customer. Returns hold_id for "
        "propose_booking. New customers get no hold.",
        kind="hold", permission=HOLD, args_model=HoldSlotArgs, run=_hold_slot,
    ),
    Tool(
        name="propose_booking",
        description="Propose one booking for approval. Existing customer: pass hold_id (from hold_slot). New "
        "customer: pass service_id, resource_id and starts_at of a find_slots slot (the system adds their details). "
        "Optional notes and a reply to a conversation you read.",
        kind="propose", permission=PROPOSE, args_model=ProposeBookingArgs, run=_propose_booking,
    ),
    Tool(
        name="propose_reply",
        description="Propose a plain-text reply (no HTML) to a conversation you read with get_conversation in this "
        "run. A person approves before it is sent.",
        kind="propose", permission=PROPOSE, args_model=ProposeReplyArgs, run=_propose_reply,
    ),
    Tool(
        name="propose_customer",
        description="Propose adding a new customer record (name, kind, tags). Contact details are added by the "
        "system from the visitor's own form; never type them.",
        kind="propose", permission=PROPOSE, args_model=ProposeCustomerArgs, run=_propose_customer,
    ),
    Tool(
        name="propose_reorder",
        description="Propose a reorder draft for items below their reorder point (ids from list_low_stock). "
        "Quantities are the computed suggestions; you cannot change them. Optional plain-text note and "
        "reason_text (up to 300 characters, used for each line).",
        kind="propose", permission=PROPOSE, args_model=ProposeReorderArgs, run=_propose_reorder,
    ),
)  # fmt: skip
