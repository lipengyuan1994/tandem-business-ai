"""Relay storage. Times are Unix seconds (integers) so SQLite and PostgreSQL behave the same.

- `relay_clients`: one per Tandem business that registered (a Tandem install registers each business separately).
- `relay_accounts`: the Stripe connected account each client created; the relay acts on an account only for the
  client that owns it.
- `relay_events`: Connect webhook events waiting for or done with delivery. Only the envelope (id, type, account,
  the object's id and kind) is kept, never the object itself: Tandem re-reads what it needs.
- `relay_nonces`: request nonces seen in the last ten minutes (replay guard).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RelayClient(Base):
    __tablename__ = "relay_clients"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    business_ref: Mapped[str] = mapped_column(String(64))
    base_url: Mapped[str] = mapped_column(String(500))
    events_url: Mapped[str] = mapped_column(String(500))
    secret_sealed: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[int] = mapped_column(Integer)
    disabled_at: Mapped[int | None] = mapped_column(Integer)


class RelayAccount(Base):
    __tablename__ = "relay_accounts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(40), ForeignKey("relay_clients.id"))
    created_at: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("client_id"),)


class RelayEvent(Base):
    __tablename__ = "relay_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(40), ForeignKey("relay_clients.id"))
    account_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[int | None] = mapped_column(Integer)
    delivered_at: Mapped[int | None] = mapped_column(Integer)
    given_up_at: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = (Index("ix_relay_events_due", "delivered_at", "given_up_at", "next_attempt_at"),)


class RelayNonce(Base):
    __tablename__ = "relay_nonces"
    client_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
