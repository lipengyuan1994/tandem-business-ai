"""Copy bounded quantities from an authenticated copilot message; no supplier text grants this path."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.identity import public as identity
from app.modules.inventory import public as inventory
from app.platform.context import Context
from app.platform.errors import Forbidden, Invalid, NotFound

from ..models import AiRun, AssistantMessage, AssistantThread
from .base import RunState
from .common import ToolArgs, qty, redact_contacts

MAX_REQUESTED_QUANTITY = Decimal(10000)


class RequestedQuantity(ToolArgs):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=10000, max_digits=8, decimal_places=3)


@dataclass(frozen=True)
class StaffSource:
    user_id: uuid.UUID
    message_id: uuid.UUID
    text: str


@dataclass(frozen=True)
class QuantityChoice:
    item_id: uuid.UUID
    name: str
    quantity: Decimal
    suggested: Decimal | None
    requested_by: uuid.UUID | None
    provenance: str
    explanation: str

    def result(self) -> dict[str, Any]:
        return {
            "item_id": str(self.item_id),
            "item": self.name,
            "quantity": qty(self.quantity),
            "suggested": qty(self.suggested) if self.suggested is not None else None,
            "requested_by": str(self.requested_by) if self.requested_by else None,
            "quantity_source": self.provenance,
        }


def staff_source(db: Session, ctx: Context, state: RunState) -> StaffSource | None:
    if ctx.on_behalf_of_user_id is None or not state.trigger_key.startswith("copilot:"):
        return None
    run = db.scalar(
        select(AiRun).where(
            AiRun.business_id == ctx.business_id,
            AiRun.id == state.run_id,
            AiRun.use_case == "copilot",
            AiRun.trigger_key == state.trigger_key,
            AiRun.on_behalf_of_user_id == ctx.on_behalf_of_user_id,
        )
    )
    if run is None:
        return None
    try:
        message_id = uuid.UUID(state.trigger_key.removeprefix("copilot:"))
    except ValueError:
        return None
    message = db.scalar(
        select(AssistantMessage)
        .join(
            AssistantThread,
            (AssistantThread.id == AssistantMessage.thread_id)
            & (AssistantThread.business_id == AssistantMessage.business_id),
        )
        .where(
            AssistantMessage.business_id == ctx.business_id,
            AssistantMessage.id == message_id,
            AssistantMessage.role == "user",
            AssistantThread.user_id == ctx.on_behalf_of_user_id,
        )
    )
    if message is None:
        return None
    return StaffSource(ctx.on_behalf_of_user_id, message.id, message.content)


_WORDS = dict(
    zip(
        (
            "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
            "sixteen seventeen eighteen nineteen twenty"
        ).split(),
        range(1, 21),
        strict=True,
    )
)
_VALUE = r"(?:[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)|" + "|".join(_WORDS) + ")"
_NUMBER = rf"(?<![\w.\-]){_VALUE}(?![\w\-]|\.\d)"


def _command_bound(text: str, position: int) -> bool:
    prefix = text[:position].rstrip()
    command = r"\b(?:reorder|order(?:ing)?|restock|replenish|buy|purchase|get|use|need)\b"
    return (
        not prefix
        or prefix.casefold() == "please"
        or re.search(command + r"\s+(?:exactly\s+)?$", text[:position], flags=re.IGNORECASE) is not None
        or (
            re.search(r"(?:\band|,)\s*$", text[:position], flags=re.IGNORECASE) is not None
            and re.search(command, prefix, flags=re.IGNORECASE) is not None
        )
    )


def _number(text: str, name: str, *, reference_bound: bool) -> Decimal | None:
    if re.search(
        r"\b(?:do\s+not|don't|never|not)\s+(?:reorder|order|restock|replenish|buy|purchase|get|use|need)\b",
        text,
        flags=re.IGNORECASE,
    ):
        raise Invalid("ai.ambiguous_staff_quantity", detail="Clarify the positive quantity to order")
    record = re.escape(name) + r"(?!\w)"
    before = rf"({_NUMBER})\s+(?:(?:of|units?(?:\s+of)?)\s+)?{record}"
    after = rf"{record}\s*(?:[×x:=]\s*|\b(?:quantity|qty)\s*[:=]?\s*)({_VALUE})(?![\w\-]|\.\d)"
    attached = [match for pattern in (before, after) for match in re.finditer(pattern, text, flags=re.IGNORECASE)]
    matches = [match.group(1) for match in attached if _command_bound(text, match.start())]
    if any(
        not _command_bound(text, match.start())
        and not re.search(r"\b(?:we have|stock is|on hand is)\s*$", text[: match.start()], flags=re.IGNORECASE)
        for match in attached
    ):
        raise Invalid("ai.quantity_item_mismatch", detail="Clarify the requested quantity for this item")
    unqualified = re.findall(
        rf"\b(?:reorder|order(?:ing)?|restock|replenish|buy|purchase|get|use|need|quantity|qty)\s+(?:(?:of|exactly)\s+)?({_NUMBER})",
        text,
        flags=re.IGNORECASE,
    )
    if not matches and reference_bound:
        matches = re.findall(
            rf"\b(?:order(?:ing)?|buy|purchase|get|use|need|quantity|qty)\s+(?:of\s+)?({_NUMBER})"
            r"(?=\s*(?:$|[.;!?]|\b(?:instead|from|for|on|in)\b))",
            text,
            flags=re.IGNORECASE,
        )
    if not matches and (
        unqualified or re.search(rf"(?:×|\bx\b|\bqty|\bquantity)\s*({_NUMBER})", text, flags=re.IGNORECASE)
    ):
        raise Invalid("ai.quantity_item_mismatch", detail="Requested quantity does not identify this item")
    values = {
        Decimal(_WORDS[value.casefold()]) if value.casefold() in _WORDS else Decimal(value.replace(",", ""))
        for value in matches
    }
    if len(values) > 1:
        raise Invalid("ai.ambiguous_staff_quantity", detail="Specify one quantity per item")
    if values:
        corrections = re.finditer(
            rf"\b(?:order|reorder|buy|purchase|get|use|need|quantity|qty)\s+({_NUMBER})", text, flags=re.IGNORECASE
        )
        for match in corrections:
            if re.match(r"\s*(?:$|[.!?;]|\b(?:instead|rather|not)\b)", text[match.end() :], flags=re.IGNORECASE):
                raw = match.group(1)
                corrected = (
                    Decimal(_WORDS[raw.casefold()]) if raw.casefold() in _WORDS else Decimal(raw.replace(",", ""))
                )
                if corrected not in values:
                    raise Invalid("ai.ambiguous_staff_quantity", detail="Clarify one final quantity per item")
    value = next(iter(values), None)
    if value is not None and (not 0 < value <= MAX_REQUESTED_QUANTITY or value % Decimal("0.001") != 0):
        raise Invalid("ai.invalid_staff_quantity", detail="Quantity must be positive, at most 10000, with 3 decimals")
    return value


def choices(
    db: Session,
    ctx: Context,
    state: RunState,
    item_ids: list[uuid.UUID],
    requested: list[RequestedQuantity],
    *,
    defaults: dict[uuid.UUID, Decimal] | None = None,
    reference_id: uuid.UUID | None = None,
) -> list[QuantityChoice]:
    if len({line.item_id for line in requested}) != len(requested):
        raise Invalid("ai.duplicate_quantity")
    explicit = {line.item_id: line.quantity for line in requested}
    if set(explicit) - set(item_ids):
        raise Invalid("ai.quantity_item_mismatch")
    source = staff_source(db, ctx, state)
    if explicit and source is None:
        raise Forbidden("ai.staff_quantity_not_allowed")
    products = catalog.get_items(db, ctx, set(item_ids))
    below = {line.item_id: line for line in inventory.list_below_reorder(db, ctx)}
    out = []
    for item_id in item_ids:
        item = products.get(item_id)
        if item is None:
            raise NotFound("catalog.item_not_found")
        if item.kind != "product" or not item.is_stock_tracked or item.status != "active":
            raise Invalid("ai.invalid_reorder_item")
        suggestion = below[item_id].suggested if item_id in below else None
        reference_bound = (
            len(item_ids) == 1
            and source is not None
            and (str(item_id) in source.text or (reference_id is not None and str(reference_id) in source.text))
        )
        copied = _number(source.text, item.name, reference_bound=reference_bound) if source else None
        if item_id in explicit and copied is None and defaults is None and explicit[item_id] != suggestion:
            detail = (
                "The user did not state a quantity. Call propose_reorder again without requested_quantities "
                f"(omit quantity) to use the suggested {qty(suggestion)} for {item.name}."
                if suggestion is not None
                else "The user did not state a quantity and there is no suggestion. Ask the user for a quantity."
            )
            raise Invalid("ai.quantity_not_requested", detail=detail)
        if item_id in explicit and (
            (copied is not None and explicit[item_id] != copied) or (copied is None and defaults is not None)
        ):
            raise Invalid("ai.quantity_not_requested", detail="Copy the quantity from the latest staff message")
        if copied is not None:
            assert source is not None
            name = identity.get_user_brief(db, ctx, source.user_id).display_name
            staff_name = (redact_contacts(name) or "Staff")[:100]
            provenance = (
                f"Quantity requested by {staff_name}: {qty(copied)} "
                f"(suggested {qty(suggestion) if suggestion is not None else 'none'})"
            )
            value, requested_by = copied, source.user_id
        else:
            fallback = defaults.get(item_id) if defaults is not None else suggestion
            if fallback is None:
                raise Invalid("ai.not_below_reorder", detail="No suggestion; staff must specify the quantity")
            value = fallback
            requested_by = None
            provenance = (
                f"Suggested {qty(suggestion)}" if suggestion == value else f"Source document quantity {qty(value)}"
            )
        out.append(
            QuantityChoice(
                item_id,
                item.name,
                value,
                suggestion,
                requested_by,
                provenance,
                below[item_id].explanation if item_id in below else "",
            )
        )
    return out
