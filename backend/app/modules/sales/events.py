"""Identifier-only sales events: no customer snapshots or free text in event payloads."""

import uuid
from typing import ClassVar

from app.platform.events import DomainEventPayload


class DocumentChanged(DomainEventPayload):
    event_type: ClassVar[str] = "sales.document_changed"
    document_id: uuid.UUID
    document_type: str
    action: str
    status: str
    version: int
