"""Fencing of untrusted text (contracts §8): customer, supplier and file text is data, never instructions."""

from __future__ import annotations

import re

from .protocol import ContextBlock

_MARKER = re.compile(r"<(/?)untrusted", re.IGNORECASE)


def escape(text: str) -> str:
    """Neutralise `<untrusted` and `</untrusted` (any case) so fenced text cannot close or open a fence."""
    return _MARKER.sub(lambda m: "‹" + m.group(0)[1:], text)


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def fence(block: ContextBlock) -> str:
    """Untrusted: `<untrusted source="{label}" id="{source_id}">` + escaped text + `</untrusted>` on their own lines;
    trusted: `## {label}` then the text."""
    if not block.untrusted:
        return f"## {block.label}\n{block.text}"
    source_id = "" if block.source_id is None else str(block.source_id)
    return f'<untrusted source="{_attr(block.label)}" id="{source_id}">\n{escape(block.text)}\n</untrusted>'


__all__ = ["escape", "fence"]
