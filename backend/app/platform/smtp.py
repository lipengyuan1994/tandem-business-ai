"""Strict SMTP configuration shared by settings and delivery. Never include a URL in errors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.utils import parseaddr
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    implicit_tls: bool
    starttls: bool
    sender: str
    username: str | None = field(repr=False)
    password: str | None = field(repr=False)


def mailbox(value: str) -> str:
    """One mailbox only, without control characters or header injection."""
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("SMTP requires a valid single From/recipient mailbox")
    _, address = parseaddr(value)
    parsed = Address(addr_spec=address)
    if not parsed.username or not parsed.domain or "," in value or ";" in value:
        raise ValueError("SMTP requires a valid single From/recipient mailbox")
    address.encode("ascii")  # SMTPUTF8 envelope addresses are not enabled by this transport.
    return address


def parse_smtp_url(value: str, fallback_from: str = "") -> SmtpConfig | None:
    if not value:
        return None
    try:
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or re.search(r"%(?![0-9a-fA-F]{2})", value):
            raise ValueError
        url = urlsplit(value)
        if url.scheme not in {"smtp", "smtps"} or not url.hostname or url.path not in {"", "/"} or url.fragment:
            raise ValueError
        host = url.hostname
        if ":" not in host and (
            len(host) > 253
            or any(
                not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in host.rstrip(".").split(".")
            )
        ):
            raise ValueError
        query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, errors="strict")
        if set(query) - {"starttls", "from"} or any(len(v) != 1 for v in query.values()):
            raise ValueError
        tls = query.get("starttls", ["true"])[0]
        if tls not in {"true", "false"}:
            raise ValueError
        port = url.port if url.port is not None else (465 if url.scheme == "smtps" else 587)
        if not 1 <= port <= 65535:
            raise ValueError
        implicit = url.scheme == "smtps" or port == 465
        if implicit and "starttls" in query:
            raise ValueError
        if not implicit and tls == "false" and url.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError
        sender = query.get("from", [fallback_from])[0]
        mailbox(sender)
        username = unquote(url.username, errors="strict") if url.username is not None else None
        password = unquote(url.password, errors="strict") if url.password is not None else None
        if (username is None) != (password is None) or (username is not None and not username):
            raise ValueError
        if any(ord(c) < 32 or ord(c) == 127 for c in (username or "") + (password or "")):
            raise ValueError
        return SmtpConfig(url.hostname, port, implicit, not implicit and tls == "true", sender, username, password)
    except (ValueError, TypeError, IndexError):
        raise ValueError(
            "Invalid SMTP_URL: use smtp://host:587 with STARTTLS or smtps://host:465; "
            "set a valid from query parameter or SMTP_FROM. Plain SMTP requires explicit starttls=false on localhost."
        ) from None
