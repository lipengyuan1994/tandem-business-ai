from __future__ import annotations

import uuid

from app.platform.events import DomainEventPayload


class DeliveryStatusChanged(DomainEventPayload):
    event_type = "notifications.delivery_status_changed"
    outbox_message_id: uuid.UUID
    related_type: str | None
    related_id: uuid.UUID | None
    status: str
