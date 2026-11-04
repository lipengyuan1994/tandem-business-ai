from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
    handoff_requested_at: datetime | None
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
    body: str
    created_at: datetime


class PublicThreadOut(BaseModel):
    next_cursor: str | None = None
    conversation_id: uuid.UUID
    business_name: str
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


class SendReplyPayload(BaseModel):
    """conversations.send_reply proposal payload."""

    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    body: str = Field(min_length=1, max_length=4000)


class ThreadDto(BaseModel):
    """What other modules (and the AI) read: messages oldest → newest, linked customer id."""

    conversation_id: uuid.UUID
    channel: str
    status: str
    customer_id: uuid.UUID | None
    visitor_name: str | None
    messages: list[MessageOut]


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
