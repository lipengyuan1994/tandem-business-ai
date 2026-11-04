"""Events published by org (upward communication, 07-workflow-engine.md#domain-events)."""

from __future__ import annotations

import uuid

from app.platform.events import DomainEventPayload


class SettingsChanged(DomainEventPayload):
    event_type = "org.settings_changed"
    changed_keys: list[str]


class OpeningHoursChanged(DomainEventPayload):
    event_type = "org.opening_hours_changed"
    location_id: uuid.UUID
