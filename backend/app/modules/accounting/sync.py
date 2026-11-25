"""Push Tandem money records to QuickBooks Online: one-way, idempotent, resumable.

A run (job `accounting.sync`) takes a lease on the connection, then walks what changed since the last run:
invoices, customer payments, credit notes (plus applying each to its invoice), supplier bill payments and bills.
Customers, suppliers and the two service items are created on demand when a document needs them. Every outcome
is stored in `accounting_sync_records`, keyed by QuickBooks company, so a record is sent once and only sent
again when its Tandem `version` and its content both changed.

Failures are per record with growing back-off; errors that would fail every call (sign-in lost, throttled,
QuickBooks down) stop the run and leave the rest for the next one. A run does at most MAX_OPS QuickBooks calls
and then queues a follow-up, staying well under Intuit's limit of 500 requests a minute per company.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.modules.purchasing.public import (
    LedgerBill,
    LedgerBillPayment,
    LedgerSupplierCredit,
    ledger_bill_payment_versions,
    ledger_bill_payments,
    ledger_bill_versions,
    ledger_bills,
    ledger_supplier_credit_versions,
    ledger_supplier_credits,
    ledger_suppliers,
)
from app.modules.sales.public import (
    LedgerInvoice,
    LedgerPayment,
    ledger_invoice_versions,
    ledger_invoices,
    ledger_payment_versions,
    ledger_payments,
)
from app.platform import clock, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict
from app.platform.models import Job
from app.platform.money import minor_text

from . import connection
from . import payloads as build
from . import permissions as P
from .models import AccountingSyncRecord
from .quickbooks import LedgerApi, LedgerError, RemoteRef, select_where

JOB_KIND = "accounting.sync"
JOB_PERMISSIONS = frozenset({P.SYNC_READ, P.SYNC_RUN, "sales.document.read", "purchasing.bill.read"})
MAX_OPS = 150
TIME_BUDGET = timedelta(minutes=4)
LEASE = timedelta(minutes=6)
BACKOFF = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=24),
)
BATCH = 200
NAMESPACE = uuid.UUID("6b1f0c1e-2f39-4b7e-9d1a-5e0f6c2b7a10")
T = TypeVar("T")


class Blocked(Exception):
    """A record could not be sent this time; its record (`key`) is already saved as failed."""

    def __init__(self, message: str, key: tuple[str, str]) -> None:
        super().__init__(message)
        self.message, self.key = message, key


class Skip(Exception):
    """A record will not be sent as it is (for example another currency); saved as skipped under `key`."""

    def __init__(self, message: str, key: tuple[str, str]) -> None:
        super().__init__(message)
        self.message, self.key = message, key


@dataclass
class Rec:
    local_version: int | None
    remote_id: str | None
    remote_sync_token: str | None
    remote_state: str | None
    content_hash: str | None
    status: str
    attempts: int
    next_attempt_at: datetime | None


class Engine:
    def __init__(self, ctx: Context, live: connection.LiveSession) -> None:
        self.ctx, self.live = ctx, live
        self.snap = live.snapshot
        self.ops = 0
        self.now = clock.now()
        self.deadline = self.now + TIME_BUDGET
        self.notes: list[str] = []
        with transaction(ctx) as db:
            rows = db.scalars(
                select(AccountingSyncRecord).where(
                    AccountingSyncRecord.business_id == ctx.business_id,
                    AccountingSyncRecord.provider == "quickbooks",
                    AccountingSyncRecord.realm_id == self.snap.realm_id,
                )
            )
            self.recs: dict[tuple[str, str], Rec] = {
                (r.entity_type, r.local_id): Rec(
                    r.local_version,
                    r.remote_id,
                    r.remote_sync_token,
                    r.remote_state,
                    r.content_hash,
                    r.status,
                    r.attempts,
                    r.next_attempt_at,
                )
                for r in rows
            }

    # ---- bookkeeping of outcomes -------------------------------------------------------------------------

    def _save(self, key: tuple[str, str], label: str, version: int | None, **fields: Any) -> None:
        entity, local_id = key
        now = clock.now()
        values = {"local_version": version, "label": label[:200], "last_attempt_at": now, **fields}
        with transaction(self.ctx) as db:
            stmt = pg_insert(AccountingSyncRecord).values(
                business_id=self.ctx.business_id,
                provider="quickbooks",
                realm_id=self.snap.realm_id,
                entity_type=entity,
                local_id=local_id,
                **values,
            )
            db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["business_id", "provider", "realm_id", "entity_type", "local_id"],
                    set_={**values, "version": AccountingSyncRecord.version + 1, "updated_at": now},
                )
            )
        rec = self.recs.get(key) or Rec(None, None, None, None, None, "failed", 0, None)
        for name in ("local_version", "remote_id", "remote_sync_token", "remote_state", "content_hash", "status"):
            if name in values:
                setattr(rec, name, values[name])
        rec.attempts = values.get("attempts", rec.attempts)
        rec.next_attempt_at = values.get("next_attempt_at", rec.next_attempt_at)
        self.recs[key] = rec

    def _synced(self, key: tuple[str, str], label: str, version: int | None, **fields: Any) -> None:
        self._save(
            key,
            label,
            version,
            status="synced",
            attempts=0,
            last_error=None,
            next_attempt_at=None,
            synced_at=clock.now(),
            **fields,
        )

    def _failed(self, key: tuple[str, str], label: str, version: int | None, message: str) -> Blocked:
        rec = self.recs.get(key)
        attempts = (rec.attempts if rec else 0) + 1
        self._save(
            key,
            label,
            # keep the version that last reached QuickBooks, so the change is picked up again after the back-off
            rec.local_version if rec is not None else version,
            status="failed",
            attempts=attempts,
            last_error=message[:500],
            next_attempt_at=clock.now() + BACKOFF[min(attempts, len(BACKOFF)) - 1],
        )
        return Blocked(message, key)

    def _skipped(self, key: tuple[str, str], label: str, version: int | None, message: str) -> Skip:
        self._save(key, label, version, status="skipped", last_error=message[:500], next_attempt_at=None)
        return Skip(message, key)

    def due(self, key: tuple[str, str], version: int) -> bool:
        rec = self.recs.get(key)
        if rec is None or rec.local_version != version:
            return rec is None or rec.status != "failed" or self._backoff_over(rec)
        return rec.status == "failed" and self._backoff_over(rec)

    def _backoff_over(self, rec: Rec) -> bool:
        return rec.next_attempt_at is None or rec.next_attempt_at <= self.now

    def budget_left(self) -> bool:
        return self.ops < MAX_OPS and clock.now() < self.deadline

    # ---- QuickBooks calls ----------------------------------------------------------------------------------

    def call(self, fn: Callable[[LedgerApi], T]) -> T:
        self.ops += 1
        try:
            return fn(self.live.api)
        except LedgerError as exc:
            if exc.kind == "auth" and self.live.reauthorize():
                self.ops += 1
                return fn(self.live.api)
            raise

    def request_id(self, key: tuple[str, str], op: str, salt: str) -> str:
        """Same request, same id, so a write whose answer was lost is not repeated. QuickBooks replays its answer
        for a known id, so a write it rejected (which counts as an attempt) gets a fresh id on the next try."""
        rec = self.recs.get(key)
        attempt = rec.attempts if rec is not None else 0
        return str(uuid.uuid5(NAMESPACE, f"{self.snap.realm_id}:{key[0]}:{key[1]}:{op}:{salt}:{attempt}"))

    def _token(self, entity: str, remote_id: str) -> str:
        return str(self.call(lambda api: api.read(entity, remote_id)).get("SyncToken") or "0")

    def push(
        self,
        key: tuple[str, str],
        qbo: str,
        label: str,
        version: int,
        body: dict[str, Any] | None,
        desired: str = "active",
    ) -> str | None:
        """Create, update, void or delete one QuickBooks object so it matches the Tandem record."""
        rec = self.recs.get(key)
        try:
            if desired != "active":
                if rec is None or rec.remote_id is None:
                    self._save(
                        key,
                        label,
                        version,
                        status="skipped",
                        last_error="Voided in Tandem before it reached QuickBooks",
                        next_attempt_at=None,
                        attempts=0,
                    )
                    return None
                remote_id = rec.remote_id
                if rec.remote_state != desired:
                    token = self._token(qbo, remote_id)
                    rid = self.request_id(key, desired, token)
                    if desired == "voided":
                        self.call(lambda api: api.void(qbo, remote_id, token, rid))
                    else:
                        self.call(lambda api: api.delete(qbo, remote_id, token, rid))
                self._synced(key, label, version, remote_state=desired)
                return remote_id
            assert body is not None
            digest = build.content_hash(body)
            if rec is not None and rec.remote_id is not None:
                remote_id = rec.remote_id
                if rec.remote_state != "active":
                    raise self._skipped(key, label, version, f"Already {rec.remote_state} in QuickBooks")
                if rec.content_hash == digest:
                    self._synced(key, label, version)
                    return remote_id
                ref = self._update(key, qbo, remote_id, body, digest)
            else:
                rid = self.request_id(key, "create", digest)
                ref = self.call(lambda api: api.create(qbo, body, rid))
            self._synced(
                key,
                label,
                version,
                remote_id=ref.id,
                remote_sync_token=ref.sync_token,
                remote_state="active",
                content_hash=digest,
            )
            return ref.id
        except LedgerError as exc:
            if exc.stops_run:
                raise
            raise self._failed(key, label, version, exc.message) from exc

    def _update(self, key: tuple[str, str], qbo: str, remote_id: str, body: dict[str, Any], digest: str) -> RemoteRef:
        for attempt in (1, 2):
            token = self._token(qbo, remote_id)
            rid = self.request_id(key, "update", f"{digest}:{token}")
            payload = {**body, "Id": remote_id, "SyncToken": token}

            def send(api: LedgerApi, payload: dict[str, Any] = payload, rid: str = rid) -> RemoteRef:
                return api.update(qbo, payload, rid)

            try:
                return self.call(send)
            except LedgerError as exc:
                if exc.kind != "stale" or attempt == 2:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    # ---- customers, suppliers and items (created on demand) -----------------------------------------------

    def party(self, entity: str, qbo: str, local_id: uuid.UUID, name: str, email: str | None) -> str:
        key = (entity, str(local_id))
        rec = self.recs.get(key)
        if rec is not None and rec.remote_id:
            return rec.remote_id
        word = "Customer" if entity == "customer" else "Supplier"
        label = f"{word} {build.clean_name(name)}"
        if rec is not None and rec.status == "failed" and not self._backoff_over(rec):
            raise Blocked(f"{label} could not be sent to QuickBooks", key)
        display = build.clean_name(name)
        table = "Customer" if qbo == "customer" else "Vendor"
        try:
            found = self.call(lambda api: api.query(select_where(table, "DisplayName", display)))
            taken = {r.remote_id for (e, _), r in self.recs.items() if e == entity and r.remote_id}
            match = next((row for row in found if str(row.get("Id")) not in taken), None)
            if match is not None:
                remote_id = str(match["Id"])
            else:
                if found:  # same name already linked to another Tandem record: keep them apart
                    display = build.clean_name(f"{display[:90]} {str(local_id)[-6:]}")
                remote_id = self._create_party(key, qbo, display, email, word)
        except LedgerError as exc:
            if exc.stops_run:
                raise
            raise self._failed(key, label, None, f"{label}: {exc.message}") from exc
        self._synced(key, label, None, remote_id=remote_id, remote_state="active")
        return remote_id

    def _create_party(self, key: tuple[str, str], qbo: str, display: str, email: str | None, word: str) -> str:
        body = build.party(display, email)
        try:
            rid = self.request_id(key, "create", build.content_hash(body))
            return self.call(lambda api: api.create(qbo, body, rid)).id
        except LedgerError as exc:
            if exc.kind != "duplicate_name":
                raise
        # QuickBooks names are unique across customers, suppliers and employees
        body = build.party(f"{display[: build.NAME_MAX - len(word) - 3]} ({word})", email)
        rid = self.request_id(key, "create", build.content_hash(body))
        return self.call(lambda api: api.create(qbo, body, rid)).id

    def item(self, key_name: str) -> str:
        key = ("item", key_name)
        name = build.SALES_ITEM_NAME if key_name == "sales" else build.TAX_ITEM_NAME
        account = self.snap.mapping.income_account_id if key_name == "sales" else self.snap.mapping.tax_account_id
        if not account:
            raise Blocked("Choose a sales tax account in QuickBooks settings", key)
        rec = self.recs.get(key)
        if rec is not None and rec.remote_id and rec.content_hash == account:
            return rec.remote_id
        label = f"Item {name}"
        try:
            remote_id = rec.remote_id if rec and rec.remote_id else None
            if remote_id is None:
                found = self.call(lambda api: api.query(select_where("Item", "Name", name)))
                if found:
                    remote_id = str(found[0]["Id"])
                else:
                    body = build.service_item(name, account)
                    rid = self.request_id(key, "create", account)
                    remote_id = self.call(lambda api: api.create("item", body, rid)).id
                    self._synced(key, label, None, remote_id=remote_id, remote_state="active", content_hash=account)
                    return remote_id
            current = self.call(lambda api: api.read("item", remote_id))
            if str((current.get("IncomeAccountRef") or {}).get("value") or "") != account:
                body = {
                    "Id": remote_id,
                    "SyncToken": str(current.get("SyncToken") or "0"),
                    "sparse": True,
                    "Name": name,
                    "IncomeAccountRef": build.ref(account),
                }
                rid = self.request_id(key, "update", f"{account}:{body['SyncToken']}")
                self.call(lambda api: api.update("item", body, rid))
        except LedgerError as exc:
            if exc.stops_run:
                raise
            raise self._failed(key, label, None, f"{label}: {exc.message}") from exc
        self._synced(key, label, None, remote_id=remote_id, remote_state="active", content_hash=account)
        return remote_id

    # ---- documents ---------------------------------------------------------------------------------------

    def _currency(self, key: tuple[str, str], label: str, version: int, currency: str) -> None:
        home = self.snap.home_currency
        if home and currency != home:
            raise self._skipped(key, label, version, f"In {currency}; the QuickBooks company uses {home}")

    @staticmethod
    def doc_label(doc: LedgerInvoice) -> str:
        return f"{'Invoice' if doc.kind == 'invoice' else 'Credit note'} {doc.number} for {doc.bill_to_name}"

    def sales_doc(self, doc: LedgerInvoice) -> str:
        key = ("invoice" if doc.kind == "invoice" else "credit_note", str(doc.id))
        label = self.doc_label(doc)
        self._currency(key, label, doc.version, doc.currency)
        try:
            refs = build.SalesRefs(
                customer_id=self.party("customer", "customer", doc.customer_id, doc.bill_to_name, doc.bill_to_email),
                sales_item_id=self.item("sales"),
                tax_item_id=self.item("tax") if doc.tax_minor else None,
                receivable_account_id=self.snap.mapping.receivable_account_id,
                us_company=(self.snap.country or "US").upper() == "US",
            )
        except Blocked as exc:
            raise self._failed(key, label, doc.version, exc.message) from exc
        body = build.sales_document(doc, refs)
        remote_id = self.push(key, "invoice" if doc.kind == "invoice" else "creditmemo", label, doc.version, body)
        assert remote_id is not None
        return remote_id

    def ensure_invoice(self, invoice_id: uuid.UUID) -> str:
        rec = self.recs.get(("invoice", str(invoice_id)))
        if rec is not None and rec.remote_id and rec.remote_state == "active":
            return rec.remote_id
        if rec is not None and rec.status == "failed" and not self._backoff_over(rec):
            raise Blocked("it failed to sync and will be retried", ("invoice", str(invoice_id)))
        with transaction(self.ctx) as db:
            doc = ledger_invoices(db, self.ctx, {invoice_id}).get(invoice_id)
        if doc is None:
            raise Blocked("The invoice is not issued", ("invoice", str(invoice_id)))
        return self.sales_doc(doc)

    def payment(self, p: LedgerPayment, number: str) -> None:
        key = ("invoice_payment", str(p.id))
        label = f"Payment for invoice {number}"
        if p.voided:
            self.push(key, "payment", label, p.version, None, desired="voided")
            return
        self._currency(key, label, p.version, p.currency)
        try:
            invoice_id = self.ensure_invoice(p.invoice_id)
        except Skip as exc:
            raise self._skipped(key, label, p.version, f"Its invoice was not sent: {exc.message}") from exc
        except Blocked as exc:
            raise self._failed(key, label, p.version, f"Waiting for the invoice: {exc.message}") from exc
        customer = self.recs.get(("customer", str(p.customer_id)))
        if customer is None or not customer.remote_id:
            raise self._failed(key, label, p.version, "Waiting for the customer to reach QuickBooks")
        body = build.customer_payment(p, customer.remote_id, invoice_id, self.snap.mapping.deposit_account_id)
        self.push(key, "payment", label, p.version, body)

    def credit_note(self, doc: LedgerInvoice) -> None:
        memo_id = self.sales_doc(doc)
        if doc.credit_of_invoice_id is None:
            return
        if doc.refund_minor:
            # QuickBooks has no refund linked to a credit memo that the API can create safely, so the refunded part
            # stays as an open credit on the credit memo there and the bookkeeper records the refund by hand.
            refund_key = ("customer_refund", str(doc.id))
            refund_label = f"Refund with credit note {doc.number}"
            if self.due(refund_key, doc.version):
                self._skipped(
                    refund_key,
                    refund_label,
                    doc.version,
                    f"Record this refund of {minor_text(doc.refund_minor, doc.currency)} {doc.currency} "
                    f"({doc.refund_method}, {doc.refund_paid_on}) against credit memo {doc.number} in QuickBooks",
                )
        applied = doc.total_minor - doc.refund_minor
        if applied <= 0:
            return
        key = ("credit_application", str(doc.id))
        label = f"Credit note {doc.number} applied to its invoice"
        rec = self.recs.get(key)
        if rec is not None and rec.remote_id:
            self._synced(key, label, doc.version)
            return
        try:
            invoice_id = self.ensure_invoice(doc.credit_of_invoice_id)
        except Skip as exc:
            raise self._skipped(key, label, doc.version, f"Its invoice was not sent: {exc.message}") from exc
        except Blocked as exc:
            raise self._failed(key, label, doc.version, f"Waiting for the invoice: {exc.message}") from exc
        customer = self.recs[("customer", str(doc.customer_id))].remote_id
        assert customer is not None
        body = build.credit_application(
            customer, invoice_id, memo_id, applied, doc.currency, doc.issue_date.isoformat()
        )
        self.push(key, "payment", label, doc.version, body)

    def supplier(self, supplier_id: uuid.UUID, fallback_name: str) -> str:
        if (rec := self.recs.get(("supplier", str(supplier_id)))) is not None and rec.remote_id:
            return rec.remote_id
        with transaction(self.ctx) as db:
            found = ledger_suppliers(db, self.ctx, {supplier_id}).get(supplier_id)
        name, email = (found.name, found.email) if found else (fallback_name, None)
        return self.party("supplier", "vendor", supplier_id, name, email)

    @staticmethod
    def bill_label(b: LedgerBill) -> str:
        ref = f" {b.supplier_reference}" if b.supplier_reference else ""
        return f"Bill{ref} from {b.supplier_name}"

    def bill(self, b: LedgerBill) -> str | None:
        key = ("bill", str(b.id))
        label = self.bill_label(b)
        if b.status == "void":  # QuickBooks bills cannot be voided, only deleted
            return self.push(key, "bill", label, b.version, None, desired="deleted")
        self._currency(key, label, b.version, b.currency)
        expense = self.snap.mapping.expense_account_id
        if not expense:
            raise self._failed(key, label, b.version, "Choose an expense account in QuickBooks settings")
        try:
            vendor = self.supplier(b.supplier_id, b.supplier_name)
        except Blocked as exc:
            raise self._failed(key, label, b.version, exc.message) from exc
        body = build.bill(b, vendor, expense, self.snap.mapping.payable_account_id)
        return self.push(key, "bill", label, b.version, body)

    def ensure_bill(self, bill_id: uuid.UUID) -> str:
        rec = self.recs.get(("bill", str(bill_id)))
        if rec is not None and rec.remote_id and rec.remote_state == "active":
            return rec.remote_id
        if rec is not None and rec.status == "failed" and not self._backoff_over(rec):
            raise Blocked("it failed to sync and will be retried", ("bill", str(bill_id)))
        with transaction(self.ctx) as db:
            row = ledger_bills(db, self.ctx, {bill_id}).get(bill_id)
        remote_id = self.bill(row) if row is not None and row.status != "void" else None
        if remote_id is None:
            raise Blocked("The bill is void", ("bill", str(bill_id)))
        return remote_id

    def bill_payment(self, p: LedgerBillPayment, bill_label: str) -> None:
        key = ("bill_payment", str(p.id))
        label = f"Payment for {bill_label}"
        if p.voided:
            self.push(key, "billpayment", label, p.version, None, desired="voided")
            return
        self._currency(key, label, p.version, p.currency)
        bank = self.snap.mapping.bank_account_id
        if not bank:
            raise self._failed(key, label, p.version, "Choose a bank account in QuickBooks settings")
        try:
            bill_id = self.ensure_bill(p.bill_id)
        except Skip as exc:
            raise self._skipped(key, label, p.version, f"Its bill was not sent: {exc.message}") from exc
        except Blocked as exc:
            raise self._failed(key, label, p.version, f"Waiting for the bill: {exc.message}") from exc
        vendor = self.recs.get(("supplier", str(p.supplier_id)))
        if vendor is None or not vendor.remote_id:
            raise self._failed(key, label, p.version, "Waiting for the supplier to reach QuickBooks")
        self.push(key, "billpayment", label, p.version, build.bill_payment(p, vendor.remote_id, bill_id, bank))

    def supplier_credit(self, c: LedgerSupplierCredit) -> None:
        """QuickBooks applies a vendor credit to a bill only through a bill payment, which the API cannot create
        safely beside the payments already sent, so the bookkeeper enters supplier credits by hand."""
        key = ("supplier_credit", str(c.id))
        bill = f"bill {c.bill_reference}" if c.bill_reference else "a bill"
        label = f"Credit from {c.supplier_name} on {bill}"
        amount_text = f"{minor_text(c.amount_minor, c.currency)} {c.currency}"
        if c.voided:
            message = f"This credit of {amount_text} was voided; remove it in QuickBooks if it was entered there"
        else:
            message = f"Enter this vendor credit of {amount_text} ({c.credit_date}) against {bill} in QuickBooks"
            if c.refund_minor:
                message += (
                    f", and the refund of {minor_text(c.refund_minor, c.currency)} {c.currency} received "
                    f"({c.refund_method}, {c.refund_received_on})"
                )
        self._skipped(key, label, c.version, message)

    # ---- the run -----------------------------------------------------------------------------------------

    def _each(self, items: list[T], work: Callable[[T], object]) -> bool:
        """Run `work` per item; False once the run's budget is spent."""
        for item in items:
            if not self.budget_left():
                return False
            try:
                work(item)
            except (Blocked, Skip):
                continue  # already recorded on the record itself
        return True

    def run(self) -> bool:
        """Push everything due. Returns True when work is left for a follow-up run."""
        m, since = self.snap.mapping, self.snap.sync_from
        missing = [
            label
            for label, value in (
                ("income", m.income_account_id),
                ("expense", m.expense_account_id),
                ("bank", m.bank_account_id),
            )
            if not value
        ]
        if missing:
            self.notes.append("Choose QuickBooks accounts for: " + ", ".join(missing))
        with transaction(self.ctx) as db:
            invoice_window = ledger_invoice_versions(db, self.ctx, since)
            bill_window = ledger_bill_versions(db, self.ctx, since)
            invoice_due = [i for i, v in invoice_window.items() if self._doc_due(i, v)]
            payment_due = [
                i
                for i, v in ledger_payment_versions(db, self.ctx, since).items()
                if self.due(("invoice_payment", str(i)), v)
            ]
            bill_due = [i for i, v in bill_window.items() if self.due(("bill", str(i)), v)]
            bill_payment_due = [
                i
                for i, v in ledger_bill_payment_versions(db, self.ctx, since).items()
                if self.due(("bill_payment", str(i)), v)
            ]
            credit_due = [
                i
                for i, v in ledger_supplier_credit_versions(db, self.ctx, since).items()
                if self.due(("supplier_credit", str(i)), v)
            ]
        # Older invoices and bills sent only because a newer payment needed them stay watched for voids and edits.
        invoice_due += [
            d.id
            for d in self._exported_outside(("invoice", "credit_note"), invoice_window, ledger_invoices)
            if self._doc_due(d.id, d.version)
        ]
        bill_due += [
            b.id
            for b in self._exported_outside(("bill",), bill_window, ledger_bills)
            if self.due(("bill", str(b.id)), b.version)
        ]
        if m.income_account_id:
            docs = self._load(invoice_due, ledger_invoices)
            invoices = sorted((d for d in docs if d.kind == "invoice"), key=lambda d: (d.issue_date, d.number))
            credits = sorted((d for d in docs if d.kind == "credit_note"), key=lambda d: (d.issue_date, d.number))
            payments = sorted(self._load(payment_due, ledger_payments), key=lambda p: (p.paid_on, str(p.id)))
            numbers = self._invoice_numbers({p.invoice_id for p in payments})
            if not (
                self._each(invoices, self.sales_doc)
                and self._each(payments, lambda p: self.payment(p, numbers.get(p.invoice_id, "")))
                and self._each(credits, self.credit_note)
            ):
                return True
        bill_rows = sorted(self._load(bill_due, ledger_bills), key=lambda b: (b.bill_date, str(b.id)))
        if m.bank_account_id:
            payments_out = sorted(
                self._load(bill_payment_due, ledger_bill_payments), key=lambda p: (p.paid_on, str(p.id))
            )
            labels = self._bill_labels({p.bill_id for p in payments_out})
            if not self._each(payments_out, lambda p: self.bill_payment(p, labels.get(p.bill_id, "a bill"))):
                return True
        if m.expense_account_id and not self._each(bill_rows, self.bill):
            return True
        credits_in = sorted(self._load(credit_due, ledger_supplier_credits), key=lambda c: (c.credit_date, str(c.id)))
        return not self._each(credits_in, self.supplier_credit)

    def _exported_outside(
        self, entities: tuple[str, ...], window: dict[uuid.UUID, int], loader: Callable[..., dict[uuid.UUID, T]]
    ) -> list[T]:
        ids = {
            uuid.UUID(local)
            for (entity, local), rec in self.recs.items()
            if entity in entities and rec.remote_id is not None and uuid.UUID(local) not in window
        }
        return self._load(sorted(ids), loader)

    def _doc_due(self, doc_id: uuid.UUID, version: int) -> bool:
        local = str(doc_id)
        known = [entity for entity in ("invoice", "credit_note") if (entity, local) in self.recs]
        if not known or any(self.due((entity, local), version) for entity in known):
            return True
        memo = self.recs.get(("credit_note", local))
        if memo is None or memo.status != "synced":
            return False
        applied = self.recs.get(("credit_application", local))
        return applied is None or (applied.status == "failed" and self._backoff_over(applied))

    def _load(self, ids: list[uuid.UUID], loader: Callable[..., dict[uuid.UUID, T]]) -> list[T]:
        out: list[T] = []
        for start in range(0, len(ids), BATCH):
            with transaction(self.ctx) as db:
                out.extend(loader(db, self.ctx, set(ids[start : start + BATCH])).values())
        return out

    def _invoice_numbers(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        docs = self._load(list(ids), ledger_invoices)
        return {d.id: d.number for d in docs}

    def _bill_labels(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        return {b.id: self.bill_label(b) for b in self._load(list(ids), ledger_bills)}


# ---- job entry points --------------------------------------------------------------------------------------


def _acquire(ctx: Context, manual: bool) -> bool:
    with transaction(ctx) as db:
        conn = connection.get(db, ctx, lock=True)
        if conn is None or conn.status != "connected" or not (manual or conn.auto_sync):
            return False
        now = clock.now()
        if conn.sync_lease_until is not None and conn.sync_lease_until > now:
            return False
        conn.sync_lease_until = now + LEASE
        conn.last_sync_started_at = now
        # no version bump: `version` guards the owner's settings form, which a background run must not invalidate
        return True


def _release(ctx: Context, error: str | None, more: bool) -> None:
    with transaction(ctx) as db:
        conn = connection.get(db, ctx, lock=True)
        if conn is None:
            return
        conn.sync_lease_until = None
        conn.last_sync_finished_at = clock.now()
        if conn.status == "connected":
            conn.last_sync_error = error[:500] if error else None
        if more:
            _queue(db, ctx, run_at=clock.now() + timedelta(seconds=30))


def run_job(ctx: Context, payload: dict[str, Any]) -> None:
    """Job handler (non-transactional: it talks to QuickBooks between short transactions)."""
    ctx = replace(ctx, permissions=JOB_PERMISSIONS)
    if not _acquire(ctx, bool(payload.get("manual"))):
        return
    error: str | None = None
    more = False
    try:
        with connection.session(ctx) as live:
            engine = Engine(ctx, live)
            more = engine.run()
            error = "; ".join(engine.notes) or None
    except LedgerError as exc:
        error = exc.message
        if exc.kind == "auth":
            connection.mark_needs_reconnect(ctx, exc.message)
    except Conflict:
        error = None  # disconnected meanwhile
    finally:
        _release(ctx, error, more)


def _queue(db: Any, ctx: Context, run_at: datetime | None = None) -> bool:
    """Queue one sync for the business unless one is already waiting. The run lease stops overlapping runs."""
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"accounting:sync:{ctx.business_id}"},
    )
    waiting = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.business_id == ctx.business_id, Job.kind == JOB_KIND, Job.status == "queued")
    )
    if waiting:
        return False
    jobs.enqueue(db, JOB_KIND, {"manual": True}, business_id=ctx.business_id, run_at=run_at, max_attempts=1)
    return True


def queue_now(ctx: Context) -> bool:
    """Ask for a sync soon. Returns False when one is already waiting to run."""
    ctx.require(P.SYNC_RUN)
    with transaction(ctx) as db:
        conn = connection.get(db, ctx)
        if conn is None or conn.status != "connected":
            raise Conflict("accounting.not_connected", detail="Connect QuickBooks first")
        return _queue(db, ctx)
