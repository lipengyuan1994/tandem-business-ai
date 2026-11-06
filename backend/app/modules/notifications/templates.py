"""Message templates rendered in a sandbox (variables only; attribute access to objects is blocked)."""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

from app.platform.config import is_regulated
from app.platform.errors import Invalid

_ENV = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False)

TEMPLATES: dict[str, tuple[str | None, str]] = {
    "self_service_link": (
        "Your bookings at {{ business_name }}",
        "View or change your bookings: {{ link }}\nThis single-use link expires in 20 minutes. "
        "If you did not request it, ignore this email.",
    ),
    "self_service_confirmation": (
        "Booking update at {{ business_name }}",
        "Your booking change ({{ action }}) at {{ business_name }} is confirmed. Booking time: {{ local_time }}.",
    ),
    "customer_care": ("{{ subject }}", "{{ body }}"),
    "purchase_order": (
        "Purchase order {{ number }} from {{ business_name }}",
        "{{ business_name }}\nPurchase order: {{ number }}\nDate: {{ date }}\n\n{{ lines }}\n\n"
        "PO total: {{ total }}\nCurrency: {{ currency }}\nDelivery location: {{ delivery_location }}",
    ),
    "quote_request": (
        "Quote request from {{ business_name }}",
        "{{ body }}\n\nItems:\n{{ lines }}\nNeeded by: {{ needed_by }}\nReply by: {{ reply_by }}\n"
        "Submit or decline your quote: {{ quote_url }}",
    ),
    "supplier_followup": ("Supplier followup from {{ business_name }}", "{{ body }}"),
    "conversation_reply": (None, "{{ body }}"),
    "payment_reminder": (
        "Payment reminder: invoice {{number}}",
        "{{ body }}\n\nAmount due: {{ amount }} {{ currency }}\nDue date: {{ due_date }}",
    ),
    "appointment_confirmation": (
        "Your appointment at {{ business_name }}",
        "Hi {{ customer_name }}, your {{ service_name }} is booked for {{ local_time }}. "
        "Reply to this message if you need to change it.",
    ),
    "marketing_campaign": (
        "{{ subject }}",
        "{{ body }}\n\n--\nSent by {{ business_name }}. To stop marketing e-mails, unsubscribe: {{ unsubscribe_url }}",
    ),
    "test_message": ("Test", "Hello {{ name }}"),
}

TEMPLATES["appointment_reminder"] = (
    "Appointment reminder from {{ business_name }}",
    "Hi {{ customer_name }}, your {{ service_name }} is scheduled for {{ local_time }}. "
    "Reply to this message if you need to change it.",
)


def _template(template_key: str) -> tuple[str | None, str]:
    if template_key not in TEMPLATES:
        raise Invalid("notifications.unknown_template", detail=template_key)
    if is_regulated() and template_key in {"appointment_confirmation", "appointment_reminder"}:
        return (
            "Appointment at {{ business_name }}",
            "{{ business_name }}: appointment on {{ local_time }}. Reply to this message to contact the business.",
        )
    return TEMPLATES[template_key]


def _plain(value: Any) -> Any:
    """Only plain data reaches templates (no objects whose attributes could be explored)."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def render(template_key: str, variables: dict[str, Any]) -> tuple[str | None, str]:
    subject, body = _template(template_key)
    data = _plain(variables)
    try:
        return (_ENV.from_string(subject).render(**data) if subject else None, _ENV.from_string(body).render(**data))
    except Exception as exc:
        raise Invalid("notifications.template_error", detail=str(exc)[:200]) from exc


_HTML_ENV = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=True)


def render_html(template_key: str, variables: dict[str, Any]) -> str:
    """The same source and plain data as text, escaped before adding a static HTML wrapper."""
    render(template_key, variables)
    body = _HTML_ENV.from_string(_template(template_key)[1]).render(**_plain(variables))
    return (
        '<!doctype html><html><body><div style="font-family:Arial,sans-serif;line-height:1.5;'
        'color:#222;white-space:pre-wrap">' + body + "</div></body></html>"
    )
