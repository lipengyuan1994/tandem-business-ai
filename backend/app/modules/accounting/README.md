# accounting (M20)

One-way push of Tandem's money records to QuickBooks Online (Xero can follow behind the same seam).
Spec: [M20](../../../../docs/roadmap/modules/M20-accounting.md).

Tables: `accounting_connections` (one per business: Intuit company id, sealed OAuth tokens, account
mapping, sync lease) and `accounting_sync_records` (one row per Tandem record per QuickBooks company:
remote id, SyncToken, content hash, status `synced|failed|skipped`, attempts, back-off, last error).
Forced business RLS. Exports omit sealed tokens and the OAuth state hash. Erasure redacts customer
identifiers in record labels and errors; copies already in QuickBooks are the business's books and are
corrected there.

Permissions: `accounting.sync.read|run` (owner/admin/demo_reviewer; auditor read) and
`accounting.connection.manage` (owner only: connect, map accounts, disconnect).

Reads only through `sales.public` (`ledger_*` invoice, credit note and payment feeds),
`purchasing.public` (`ledger_*` bill, bill payment and supplier feeds) and `payments.public`
(`ledger_payouts`, Stripe payouts), plus `org.public` for the business currency and time zone. It never writes to other modules.

Flow:
- Connect: `POST /accounting/quickbooks/connect` stores a one-time state hash (10 min) and returns
  Intuit's authorize URL. Intuit redirects the browser to `/accounting/quickbooks/callback`, which checks
  and clears the state, exchanges the code, reads CompanyInfo/Preferences (name, country, home currency),
  stores tokens sealed (JWE dir/A256GCM, key derived from `APP_SECRET_KEY`) and redirects to
  `/settings/accounting`. Connecting is refused under `DATA_PROFILE=regulated`.
- Tokens: refreshed under the connection row lock 5 minutes before expiry; the rotated refresh token is
  always stored. A refused refresh or unreadable token sets `needs_reconnect`.
- Sync (`accounting.sync` job, non-transactional; cron every 15 min per business when `auto_sync`, and
  `POST .../sync`; at most one waiting run per business): leases the connection, then pushes invoices, customer
  payments, credit notes (as credit memos, each applied to its invoice with a zero-amount payment),
  supplier bill payments and bills dated on or after `sync_from`. Customers, suppliers and the two service
  items ("Tandem sales", "Tandem sales tax") are linked by exact name or created on demand.
- Change detection: a record is due when its Tandem `version` differs from the stored one; nothing is sent
  when the body hash is unchanged. Updates read the SyncToken first and retry once on a stale token.
  Voided payments are voided in QuickBooks; void bills are deleted (QuickBooks bills cannot be voided).
- Idempotency: every write carries `requestid` = uuid5(company, record, operation, content, attempt), so a
  write whose answer was lost is not repeated, while a rejected one gets a fresh id on retry.
- Failures: per record, back-off 5 min / 15 min / 1 h / 6 h / 24 h; staff can retry from the log. Sign-in
  lost, throttling (429), QuickBooks outages and a missing egress allowlist stop the run. A run makes at
  most 150 calls or 4 minutes of work, then queues a follow-up.
- Records in a currency other than the QuickBooks home currency are skipped with a reason.
- Stripe (M21 phase 4): with a Stripe clearing account mapped, Stripe payments are deposited to it, and each paid
  payout becomes a Transfer from clearing to the bank (`payout_transfer`) and a Purchase for Stripe's fees from
  clearing to the Stripe fees account (`payout_fees`). A payout that fails later has both deleted. Fees of manual
  payouts, or of payouts Stripe could not list in full, are left to the bookkeeper. No clearing account: nothing
  about Stripe is sent.

Limits: one QuickBooks company per business; push only (edits in QuickBooks are not read back); invoice
lines all post to the "Tandem sales" item and its mapped income account; bills post as one expense line;
customer and supplier details are not updated after the first push; non-US companies may need a tax code
on lines, which is not set yet.

Config: `QUICKBOOKS_CLIENT_ID`, `QUICKBOOKS_CLIENT_SECRET`, `QUICKBOOKS_ENVIRONMENT` (`sandbox` default),
`QUICKBOOKS_REDIRECT_URI` (default `{APP_BASE_URL}/api/v1/accounting/quickbooks/callback`), and the Intuit
hosts in `EGRESS_ALLOWLIST` (see `quickbooks.INTUIT_HOSTS`).
