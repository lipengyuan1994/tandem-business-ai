from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

from app.platform import clock

Channel = Literal["web_chat", "phone_log", "walk_in", "email", "voice"]
Status = Literal["open", "waiting_on_staff", "waiting_on_customer", "closed"]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    direction: Literal["inbound", "outbound", "internal"]
    author_kind: Literal["visitor", "customer", "staff", "assistant", "system"]
    author_user_id: uuid.UUID | None
    body: str
    delivery_status: str | None
    proposal_id: uuid.UUID | None
    created_at: datetime
    publication_state: Literal["withheld", "published", "stopped", "cancelled"] = "published"
    publish_at: datetime | None = None
    cancellation_reason: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sending_label(self) -> str | None:
        if self.publication_state != "withheld" or self.publish_at is None:
            return None
        seconds = max(0, int((self.publish_at - clock.now()).total_seconds()))
        return f"Sending in {seconds // 60}:{seconds % 60:02}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def stop_url(self) -> str | None:
        return f"/api/v1/conversations/messages/{self.id}/stop" if self.publication_state == "withheld" else None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    customer_id: uuid.UUID | None
    channel: Channel
    subject: str | None
    status: Status
    assigned_to: uuid.UUID | None
    last_message_at: datetime
    visitor_name: str | None
    visitor_email: str | None
    contact_consent_at: datetime | None
    contact_consent_text: str | None
    emergency: bool = False
    handoff_requested_at: datetime | None
    ai_mode: Literal["auto", "human"]
    ai_generation: int
    ai_paused_at: datetime | None
    version: int


class ConversationListItem(ConversationOut):
    customer_name: str | None = None
    last_message_preview: str = ""


class ThreadOut(ConversationOut):
    customer_name: str | None = None
    messages: list[MessageOut]


class ConversationCreate(BaseModel):
    """Staff log a phone call or walk-in; the first message is the customer's request."""

    model_config = ConfigDict(extra="forbid")
    channel: Literal["phone_log", "walk_in"]
    body: str = Field(min_length=1, max_length=8000)
    customer_id: uuid.UUID | None = None
    subject: str | None = Field(default=None, max_length=200)
    visitor_name: str | None = Field(default=None, max_length=200)


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    status: Status | None = None
    assigned_to: uuid.UUID | None = None
    subject: str | None = Field(default=None, max_length=200)
    customer_id: uuid.UUID | None = None


class StaffMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["outbound", "internal"]
    body: str = Field(min_length=1, max_length=8000)


class PublicChatStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    message: str = Field(min_length=1, max_length=4000)
    consent_to_contact: bool = False


class PublicMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=4000)


class PublicMessageOut(BaseModel):
    id: uuid.UUID
    from_business: bool
    is_system: bool = False
    body: str
    created_at: datetime


class PublicThreadOut(BaseModel):
    next_cursor: str | None = None
    conversation_id: uuid.UUID
    business_name: str
    ai_greeting: str
    messages: list[PublicMessageOut]


class PreferredWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    time_from: time
    time_to: time


class AppointmentRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: uuid.UUID
    preferred: list[PreferredWindow] = Field(min_length=1, max_length=5)
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article_id: uuid.UUID
    version: int = Field(ge=1)


class GroundingClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=240)
    verdict: Literal["supported", "unsupported", "contradicted"]
    source: str = Field(max_length=100)


class GroundingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[GroundingClaim] = Field(max_length=8)
    verdict: Literal["supported", "unsupported", "contradicted", "unavailable"]


class SendReplyPayload(BaseModel):
    """conversations.send_reply proposal payload."""

    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    body: str = Field(min_length=1, max_length=4000)
    citations: list[Citation] = Field(default_factory=list, max_length=3)
    ai_generation: int | None = Field(default=None, ge=0)
    answers_message_id: uuid.UUID | None = None
    grounding: GroundingResult | None = None
    safety_clean: bool = False
    bundled_action: bool = False


class ThreadDto(BaseModel):
    """What other modules (and the AI) read: messages oldest → newest, linked customer id."""

    conversation_id: uuid.UUID
    channel: str
    status: str
    customer_id: uuid.UUID | None
    visitor_name: str | None
    messages: list[MessageOut]
    ai_mode: Literal["auto", "human"] = "auto"
    ai_generation: int = 0


class MessageContext(BaseModel):
    """Scoped message input and server-side visitor contacts for internal workflows."""

    conversation_id: uuid.UUID
    channel: Channel
    author_kind: Literal["visitor", "customer", "staff", "assistant", "system"]
    body: str
    customer_id: uuid.UUID | None
    visitor_name: str | None
    visitor_email: str | None
    visitor_phone: str | None
    created_at: datetime
    ai_mode: Literal["auto", "human"] = "auto"
    ai_generation: int = 0
    message_ai_generation: int = 0
    emergency: bool = False
