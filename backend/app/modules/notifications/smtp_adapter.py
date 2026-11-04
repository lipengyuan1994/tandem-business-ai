"""Verified TLS SMTP with conservative handling of ambiguous DATA outcomes."""

from __future__ import annotations

import hashlib
import logging
import re
import smtplib
import ssl
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from html import escape
from typing import Any
from urllib.parse import urlsplit

from app.platform import clock
from app.platform.config import get_settings
from app.platform.smtp import mailbox, parse_smtp_url

from .adapters import Delivered, PermanentError, RenderedMessage, Result, RetryableError, Unknown

log = logging.getLogger(__name__)
TIMEOUT = 10


class _SmtpPolicy(policy.EmailPolicy):
    def fold_binary(self, name: str, value: Any) -> bytes:
        if name.lower() == "list-unsubscribe":
            # RFC 2369 requires a literal URL, not RFC 2047 encoded words. Keep other headers' normal folding.
            line = f"{name}: {value}".encode("ascii")
            if len(line) > 998:  # RFC 5322 hard line limit; never split or encode the URI to fit.
                raise ValueError("unsubscribe URL exceeds the SMTP header line limit")
            return line + self.linesep.encode("ascii")
        return super().fold_binary(name, value)


class SmtpAdapter:
    def send(self, message: RenderedMessage, idempotency_key: str) -> Result:
        settings = get_settings()
        config = parse_smtp_url(settings.smtp_url, settings.smtp_from)
        if config is None:
            return PermanentError("email delivery is not configured")
        try:
            recipient = mailbox(message.recipient or "")
            mail = EmailMessage(policy=_SmtpPolicy(linesep="\r\n"))
            mail["From"], mail["To"] = config.sender, message.recipient
            mail["Subject"] = message.subject or "Message"
            mail["Date"] = format_datetime(clock.now())
            digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
            message_id = f"<{digest}@{mailbox(config.sender).rsplit('@', 1)[1]}>"
            mail["Message-ID"] = message_id
            if message.reply_to:
                mailbox(message.reply_to)
                mail["Reply-To"] = message.reply_to
            if message.unsubscribe_url:
                url = message.unsubscribe_url
                if urlsplit(url).scheme != "https" or not urlsplit(url).hostname or any(c in url for c in "\r\n<>"):
                    raise ValueError("invalid unsubscribe URL")
                mail["List-Unsubscribe"] = f"<{url}>"
                mail["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            mail.set_content(message.body)
            # Worker always supplies escaped HTML rendered from the same sandboxed template.
            mail.add_alternative(
                message.html
                if message.html is not None
                else "<html><body><pre>" + escape(message.body) + "</pre></body></html>",
                subtype="html",
            )
            wire = mail.as_bytes()
        except (ValueError, TypeError):
            return PermanentError("invalid email headers or recipient")
        log.info("smtp_delivery host=%s port=%s", config.host, config.port)
        client: smtplib.SMTP | None = None
        data_started = False
        try:
            context = ssl.create_default_context()
            client = (
                smtplib.SMTP_SSL(config.host, config.port, timeout=TIMEOUT, context=context)
                if config.implicit_tls
                else smtplib.SMTP(config.host, config.port, timeout=TIMEOUT)
            )
            client.ehlo_or_helo_if_needed()
            if config.starttls:
                client.starttls(context=context)
                client.ehlo()
            if config.username is not None:
                client.login(config.username, config.password or "")
            code, reply = client.mail(mailbox(config.sender))
            if code != 250:
                raise smtplib.SMTPResponseException(code, reply)
            code, reply = client.rcpt(recipient)
            if code not in {250, 251}:
                raise smtplib.SMTPResponseException(code, reply)
            code, reply = client.docmd("DATA")
            if code != 354:
                raise smtplib.SMTPResponseException(code, reply)
            data_started = True
            # SMTP transparency: double leading dots, then terminate DATA with CRLF-dot-CRLF.
            wire = re.sub(rb"(?m)^\.", b"..", wire)
            client.send(wire + (b"" if wire.endswith(b"\r\n") else b"\r\n") + b".\r\n")
            code, reply = client.getreply()
            if code != 250:
                raise smtplib.SMTPResponseException(code, reply)
            return Delivered(message_id)
        except smtplib.SMTPResponseException as exc:
            # Provider text can echo credentials, recipients or content: persist only the numeric code.
            error = f"SMTP response {exc.smtp_code}"
            if 400 <= exc.smtp_code < 500:
                return RetryableError(error)
            if 500 <= exc.smtp_code < 600:
                return PermanentError(error)
            # After DATA, malformed/truncated or unexpected replies cannot rule out acceptance.
            return Unknown(error + "; delivery uncertain") if data_started else PermanentError(error)
        except smtplib.SMTPNotSupportedError:
            return PermanentError("SMTP server does not support required TLS or authentication")
        except (OSError, smtplib.SMTPServerDisconnected):
            return (
                Unknown("SMTP connection lost after DATA; delivery uncertain")
                if data_started
                else RetryableError("SMTP connection failed before DATA")
            )
        except smtplib.SMTPException:
            return (
                Unknown("SMTP failure after DATA; delivery uncertain")
                if data_started
                else RetryableError("SMTP protocol error")
            )
        finally:
            if client is not None:
                client.close()  # acceptance is final; a failed QUIT must never trigger a duplicate
