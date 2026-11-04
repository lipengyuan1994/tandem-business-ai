"""Identifier-only procurement events."""

import uuid
from typing import ClassVar

from app.platform.events import DomainEventPayload


class QuoteReceived(DomainEventPayload):
    event_type: ClassVar[str] = "purchasing.quote_received"
    quote_request_id: uuid.UUID
    supplier_id: uuid.UUID
    declined: bool
    all_answered: bool
