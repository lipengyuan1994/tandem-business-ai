"""DTOs of the ai module: status (contracts §13) and the use-case output models (§11).

`Policy` (§6) and `UseCaseId` (§11) live here so that later cards (gateway, runner) import them instead of
redefining them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Policy = Literal["hosted_vultr", "hosted_any", "strict_private", "regulated"]
UseCaseId = Literal[
    "intake", "copilot", "summary", "reorder_note", "briefing", "procurement", "campaign_draft", "payment_followup",
    "safety",
]  # fmt: skip


class AiStatus(BaseModel):
    enabled: bool
    reason: str | None
    policy: Policy
    budget_percent: int


# ----------------------------------------------------------------------------------------- §11 output models
class ThreadSummary(BaseModel):
    summary: str = Field(max_length=800)
    open_questions: list[str] = Field(default=[], max_length=5)
    suggested_reply: str = Field(max_length=1500)


class ReorderNote(BaseModel):
    explanation: str = Field(max_length=300)  # goes into reason_text (≤ 300)


class Briefing(BaseModel):
    bullets: list[str] = Field(min_length=1, max_length=5)


class CampaignDraft(BaseModel):
    subject: str = Field(max_length=200)
    body_markdown: str = Field(max_length=6000)


class PaymentReminderDraft(BaseModel):
    text: str = Field(max_length=1500)


class SafetyVerdict(BaseModel):
    flagged: bool
    category: Literal["none", "abuse", "self_harm", "sexual", "violence", "other"]


class SupplierQuoteLineExtract(BaseModel):
    item_id: uuid.UUID  # one of the request's lines (given in the facts)
    unit_price: str | None  # as written, e.g. "11.40"
    quantity_available: str | None = None


class SupplierQuoteExtract(BaseModel):
    lines: list[SupplierQuoteLineExtract]
    currency: str | None
    lead_time_days: int | None
    min_quantity: str | None = None
    valid_until: date | None = None
    notes: str = Field(default="", max_length=500)


class ProcurementRecommendation(BaseModel):
    quote_id: uuid.UUID
    explanation: str = Field(max_length=800)


# ----------------------------------------------------------------------------------------- §12, §14 (AI-05)
RunStatus = Literal["running", "succeeded", "failed", "step_limit", "budget_exceeded", "policy_blocked"]


class UsageToday(BaseModel):
    input_tokens: int
    output_tokens: int
    cost_micros: int
    budget_tokens: int
    percent: int


class RunOut(BaseModel):
    """A recorded run without any text (no prompt, completion or result)."""

    id: uuid.UUID
    use_case: str
    status: RunStatus
    needs_human: bool
    model: str | None
    provider: str | None
    steps: int
    input_tokens: int
    output_tokens: int
    cost_micros: int
    latency_ms: int
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None


class UsageDayOut(BaseModel):
    day: date
    input_tokens: int
    output_tokens: int
    cost_micros: int
    runs: int


class UsageOut(BaseModel):
    today: UsageToday
    days: list[UsageDayOut]  # the last 30 UTC days, oldest first, today last (zeros for days without use)
