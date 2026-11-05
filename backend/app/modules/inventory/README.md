# inventory (M07)

Stock availability, reservations, append-only stock movements and reorder drafts; business scoped.
Spec: [M07](../../../../docs/roadmap/modules/M07-inventory.md).

Public: `get_availability`, `reserve_stock`, `release_reservations`, `consume_reservations`,
`record_movement`, `list_below_reorder`, `has_open_reorder_draft`, `read_reorder_draft`,
`mark_reorder_draft_converted`; stock/reorder DTOs and StockAdjusted/StockBelowReorderLevel events.
Callers gate their feature permissions; public internal reads are not an authorization bypass.

Permissions: inventory.stock.read|adjust, inventory.reorder.read|write, inventory.proposal.approve.
Executors: inventory.adjust_stock and inventory.create_reorder_draft; both use existing services.
Registration contributes export/erasure, HTTP routes and executors; no separate worker queue.

A0 serializes open reorder intent per item and rechecks policy quantity/stock under locks at execution.
A1 staff copilot may propose bounded requested quantities; reason_text retains source provenance.
Immutable previews show requested-by-staff versus suggested quantities. Background U4 still uses
only computed suggestions. The executor's autonomy_reasons callback compares actual payload values
with current suggestions before any shadow/live cap reserve. Non-suggested proposals remain pending
(fell_back/quantity_not_suggested); human/chat approval can execute them. Policy execution remains
strict regardless of the provenance text. Existing staff/manual draft behavior is preserved.
