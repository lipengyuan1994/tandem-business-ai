from __future__ import annotations

import uuid
from typing import Literal

from app.platform.events import DomainEventPayload


class ProposalCreated(DomainEventPayload):
    event_type = "approvals.proposal_created"
    proposal_id: uuid.UUID
    action_type: str
    origin_kind: str
    title: str


class ProposalDecided(DomainEventPayload):
    event_type = "approvals.proposal_decided"
    proposal_id: uuid.UUID
    decision: Literal["approve", "reject"]


class ProposalClosed(DomainEventPayload):
    event_type = "approvals.proposal_closed"
    proposal_id: uuid.UUID
    action_type: str
    outcome: Literal["succeeded", "failed", "rejected", "expired", "cancelled"]
