"""Code-owned AI team. Metadata never grants permissions or expands autonomy eligibility."""

from dataclasses import dataclass

from app.platform.errors import NotFound

from .gateway.routes import ModelClass


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    job: str
    use_cases: tuple[str, ...]
    triggers: tuple[str, ...]
    action_types: tuple[str, ...]
    model_classes: tuple[ModelClass, ...]
    tools: tuple[str, ...]
    handoffs: tuple[str, ...]
    default_enabled: bool = True


_FRONT_TOOLS = (
    "get_conversation",
    "find_customers",
    "get_customer",
    "customer_history",
    "list_services",
    "get_service_parts",
    "find_slots",
    "list_appointments",
    "check_stock",
    "search_knowledge",
    "hold_slot",
    "propose_booking",
    "propose_reply",
    "propose_customer",
)
_PURCHASING_TOOLS = (
    "check_stock",
    "list_low_stock",
    "list_suppliers_for_item",
    "get_quote_request",
    "compare_quotes",
    "list_late_purchase_orders",
    "propose_quote_request",
    "propose_supplier_quote",
    "propose_po_from_quote",
    "propose_supplier_followup",
    "propose_send_po",
)

AGENTS: dict[str, Agent] = {
    "front_desk": Agent(
        "front_desk",
        "Front Desk",
        "Answer customers, collect details and arrange bookings.",
        ("intake", "summary"),
        ("conversations.message_received", "ai.intake", "api.summary"),
        (
            "conversations.send_reply",
            "scheduling.book_appointment",
            "scheduling.reschedule_appointment",
            "scheduling.cancel_appointment",
            "crm.create_customer",
            "crm.update_customer",
        ),
        ("agent", "fast_extract", "safety", "grounding"),
        _FRONT_TOOLS,
        ("front_desk", "customer_care"),
    ),
    "customer_care": Agent(
        "customer_care",
        "Customer Care",
        "Check in after service, prepare recall reminders and triage complaints for people.",
        ("customer_care",),
        (
            "scheduling.appointment_completed",
            "conversations.message_received",
            "ai.customer_care_checkin",
            "ai.customer_care_recall",
            "ai.customer_care_triage",
            "ai.customer_care_recall_subject",
            "ai.customer_care_classify",
        ),
        ("customer_care.send_message",),
        ("writer", "agent", "safety"),
        ("get_conversation", "customer_history", "search_knowledge"),
        ("customer_care",),
        False,
    ),
    "marketing": Agent(
        "marketing",
        "Marketing",
        "Prepare campaign drafts for staff review.",
        ("campaign_draft",),
        ("api.campaign_draft",),
        ("marketing.approve_campaign",),
        ("writer", "agent"),
        ("search_knowledge", "today_summary"),
        ("marketing",),
    ),
    "inventory": Agent(
        "inventory",
        "Inventory",
        "Watch low stock and prepare reorder drafts.",
        ("reorder_note",),
        ("inventory.stock_below_reorder_level", "ai.reorder_note"),
        ("inventory.create_reorder_draft", "inventory.adjust_stock"),
        ("writer", "agent"),
        ("check_stock", "list_low_stock", "get_service_parts", "list_appointments", "propose_reorder"),
        ("inventory", "purchasing"),
    ),
    "purchasing": Agent(
        "purchasing",
        "Purchasing",
        "Request and compare quotes, prepare POs and supplier follow-ups.",
        ("procurement",),
        (
            "purchasing.quote_received",
            "ai.procurement_request_quotes",
            "ai.procurement_compare",
            "ai.procurement_scan",
            "ai.procurement_followup",
            "api.procurement",
        ),
        (
            "purchasing.create_po_from_draft",
            "purchasing.approve_po",
            "purchasing.send_po",
            "purchasing.send_quote_requests",
            "purchasing.record_supplier_quote",
            "purchasing.create_po_from_quote",
            "purchasing.send_supplier_followup",
        ),
        ("agent", "fast_extract", "writer"),
        _PURCHASING_TOOLS,
        ("purchasing",),
    ),
    "finance": Agent(
        "finance",
        "Finance",
        "Review overdue invoices and prepare payment reminders.",
        ("payment_followup",),
        ("ai.payment_followup", "ai.payment_followup_scan"),
        ("sales.send_payment_reminder", "sales.create_quote", "sales.confirm_order"),
        ("writer", "agent"),
        ("list_overdue_invoices", "get_customer", "propose_payment_reminder"),
        ("finance",),
    ),
    "operations_manager": Agent(
        "operations_manager",
        "Operations Manager",
        "Brief staff and route their requests.",
        ("briefing", "copilot"),
        ("api.briefing", "api.copilot"),
        (),
        ("agent", "writer"),
        (),
        ("front_desk", "customer_care", "marketing", "inventory", "purchasing", "finance", "operations_manager"),
    ),
}
ACTION_OWNERS = {action: a.key for a in AGENTS.values() for action in a.action_types}
USE_CASE_OWNERS = {use_case: a.key for a in AGENTS.values() for use_case in a.use_cases}
JOB_OWNERS = {trigger: a.key for a in AGENTS.values() for trigger in a.triggers if trigger.startswith("ai.")}


def get_agent(key: str) -> Agent:
    try:
        return AGENTS[key]
    except KeyError:
        raise NotFound("ai.agent_not_found") from None
