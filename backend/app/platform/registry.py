"""Composition registry: everything modules contribute at startup (routers, permissions, executors,
jobs, event subscribers, cron, resolvers, data-lifecycle handlers). Frozen after startup.

Approvals looks executors up here, so it never imports business modules (02-architecture.md rule 5).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, overload
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.platform.context import Context
    from app.platform.export import ExportTables


@dataclass(frozen=True)
class PreviewLine:
    label: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class ExecutionMeta:
    proposal_id: UUID
    revision_id: UUID
    idempotency_key: str


@dataclass(frozen=True)
class ChatMoney:
    total_minor: int
    currency: str
    subject: str


DeliveryState = Literal[
    "none", "scheduled", "sending", "delivered", "failed", "unknown", "stopped", "withheld", "published"
]


@dataclass(frozen=True)
class ExecutorSpec:
    action_type: str
    payload_model: type[BaseModel]
    approve_permission: str
    describe: Callable[[Session, Context, Any], list[PreviewLine]]
    validate: Callable[[Session, Context, Any], None]
    run: Callable[[Session, Context, Any, ExecutionMeta], dict[str, Any]]
    second_person: bool | Callable[[Session, Context, Any], bool] = False
    chat_money: Callable[[Session, Context, Any], ChatMoney] | None = None
    autonomy_reasons: Callable[[Session, Context, Any], list[str]] | None = None


JobHandler = Callable[[Session, "Context", dict[str, Any]], None]
NonTransactionalJobHandler = Callable[["Context", dict[str, Any]], None]
EventHandler = Callable[[Session, "Context", dict[str, Any], UUID], None]  # (db, ctx, payload, event_id)


@dataclass(frozen=True)
class JobSpec:
    handler: JobHandler | NonTransactionalJobHandler
    transactional: bool = True


@dataclass(frozen=True)
class ErasureTarget:
    """Who is being erased, with every identifier other modules may hold in free text or as a recipient."""

    customer_id: UUID
    display_name: str
    emails: tuple[str, ...]
    phones: tuple[str, ...]
    names: tuple[str, ...] = ()  # given, family and organisation name
    phones_normalized: tuple[str, ...] = ()
    # Ids of records that belong to the person (appointments, conversations, messages, outbox rows, proposals).
    # Records that carry one of them lose all their free text, not only the strings that match a needle.
    linked_ids: frozenset[UUID] = frozenset()

    @property
    def needles(self) -> list[str]:
        """Strings to remove from free text (names, addresses, numbers), longest first. Under 3 characters: skipped."""
        values = {
            self.display_name, *self.names, *self.emails, *(e.lower() for e in self.emails),
            *self.phones, *self.phones_normalized,
        }  # fmt: skip
        return sorted((v for v in values if len(v.strip()) >= 3), key=len, reverse=True)

    @property
    def all_ids(self) -> list[UUID]:
        return sorted({self.customer_id, *self.linked_ids})


@dataclass(frozen=True)
class SessionInfo:
    session_id: UUID
    user_id: UUID
    business_id: UUID | None
    permissions: frozenset[str]
    csrf_token_hash: str


class SessionResolver(Protocol):
    def __call__(self, token: str) -> SessionInfo | None: ...


class PublicBusinessResolver(Protocol):
    def __call__(self, slug: str) -> UUID | None: ...


class ExportHandler(Protocol):
    def __call__(self, db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables: ...


@dataclass(frozen=True)
class CronSpec:
    name: str
    job_kind: str
    every_seconds: int
    per_business: bool = False


@dataclass
class Registry:
    routers: list[tuple[APIRouter, str, list[str]]] = field(default_factory=list)
    permissions: set[str] = field(default_factory=set)
    executors: dict[str, ExecutorSpec] = field(default_factory=dict)
    jobs: dict[str, JobSpec] = field(default_factory=dict)
    subscribers: dict[str, dict[str, EventHandler]] = field(default_factory=dict)  # event type -> name -> handler
    cron: list[CronSpec] = field(default_factory=list)
    session_resolver: SessionResolver | None = None
    public_business_resolver: PublicBusinessResolver | None = None
    # Data lifecycle (M18). Dicts keep insertion order = module registration order (dependencies first).
    export_handlers: dict[str, ExportHandler] = field(default_factory=dict)  # (db, ctx) -> {table: rows}
    erasure_handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)  # (db, ctx, target) -> {what: count}
    erasure_checks: dict[str, Callable[..., Any]] = field(
        default_factory=dict
    )  # (db, ctx, target) -> [blocking reasons]
    erasure_links: dict[str, Callable[..., Any]] = field(default_factory=dict)  # (db, ctx, target) -> {ids}
    restriction_handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    retention_handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    proposal_delivery_reader: Callable[[Session, Context, UUID], DeliveryState] | None = None
    proposal_delivery_readers: list[Callable[[Session, Context, UUID], DeliveryState]] = field(default_factory=list)
    public_routes: set[str] = field(default_factory=set)
    _frozen: bool = False

    def _check(self) -> None:
        if self._frozen:
            raise RuntimeError("registry is frozen after startup")

    def add_router(self, router: APIRouter, prefix: str, tags: list[str]) -> None:
        self._check()
        self.routers.append((router, prefix, tags))

    def add_permissions(self, permissions: set[str] | frozenset[str]) -> None:
        self._check()
        self.permissions |= set(permissions)

    def add_executor(self, spec: ExecutorSpec) -> None:
        self._check()
        if spec.action_type in self.executors:
            raise ValueError(f"duplicate executor {spec.action_type}")
        self.executors[spec.action_type] = spec

    @overload
    def add_job(self, kind: str, handler: JobHandler, *, transactional: Literal[True] = True) -> None: ...

    @overload
    def add_job(self, kind: str, handler: NonTransactionalJobHandler, *, transactional: Literal[False]) -> None: ...

    def add_job(
        self, kind: str, handler: JobHandler | NonTransactionalJobHandler, *, transactional: bool = True
    ) -> None:
        self._check()
        if kind in self.jobs:
            raise ValueError(f"duplicate job handler {kind}")
        self.jobs[kind] = JobSpec(handler, transactional)

    def subscribe(self, event_type: str, subscriber: str, handler: EventHandler) -> None:
        self._check()
        self.subscribers.setdefault(event_type, {})[subscriber] = handler

    def add_export_handler(self, module: str, handler: ExportHandler) -> None:
        self._check()
        self.export_handlers[module] = handler

    def add_erasure_handler(
        self, module: str, handler: Callable[[Session, Context, ErasureTarget], dict[str, int]]
    ) -> None:
        self._check()
        self.erasure_handlers[module] = handler

    def add_restriction_handler(
        self, module: str, handler: Callable[[Session, Context, ErasureTarget], dict[str, int]]
    ) -> None:
        self._check()
        self.restriction_handlers[module] = handler

    def add_erasure_links(self, module: str, links: Callable[[Session, Context, ErasureTarget], set[UUID]]) -> None:
        """Ids of the module's records that belong to the person (for example their appointments)."""
        self._check()
        self.erasure_links[module] = links

    def add_erasure_check(self, module: str, check: Callable[[Session, Context, ErasureTarget], list[str]]) -> None:
        """A module can refuse an erasure while it would break something (for example an open proposal)."""
        self._check()
        self.erasure_checks[module] = check

    def add_cron(self, name: str, job_kind: str, every_seconds: int, *, per_business: bool = False) -> None:
        self._check()
        self.cron.append(CronSpec(name, job_kind, every_seconds, per_business))

    def set_session_resolver(self, resolver: SessionResolver) -> None:
        self._check()
        self.session_resolver = resolver

    def set_public_business_resolver(self, resolver: PublicBusinessResolver) -> None:
        self._check()
        self.public_business_resolver = resolver

    def add_public_route(self, path: str) -> None:
        """Declare a route that intentionally has no permission dependency (checked by an architecture test)."""
        self._check()
        self.public_routes.add(path)

    def freeze(self) -> None:
        from app.platform.retention import register

        if not self._frozen:
            register(self)
            self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen


_registry: Registry | None = None


def set_registry(registry: Registry) -> None:
    global _registry
    _registry = registry


def get_registry() -> Registry:
    if _registry is None:
        raise RuntimeError("registry not initialised; call app.main.build_registry() first")
    return _registry
