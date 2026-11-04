# M07 · inventory

| Field | Value |
|---|---|
| Release / phase | MVP · P3 |
| Tier / kind | **A** for ledger and reservations; B for UI and reorder rules · PRE |
| Depends on | platform, org, catalog, approvals |
| Owns tables | `stock_locations`, `stock_levels`, `stock_movements`, `stock_reservations`, `reorder_rules`, `reorder_drafts`, `reorder_draft_lines` |

## Purpose
A stock **ledger**, never a number anyone overwrites. Movements change physical stock (on hand). Reservations promise stock to appointments and orders, which reduces *available* but not *on hand*. Reorder rules produce deterministic suggestions. Drafts list what to buy (MVP); purchasing (v1) turns them into purchase orders.

## Tables
`stock_locations` — `name`, `location_id` (FK locations, nullable), `is_default bool` (one per business).

`stock_levels` — current totals per item and stock location. Maintained **only** by inventory services, in the same transaction as the ledger row.

| Column | Type | Notes |
|---|---|---|
| item_id | uuid | Composite FK catalog_items (stock-tracked product) |
| stock_location_id | uuid | |
| on_hand | numeric(14,3) | `check (on_hand >= 0)` |
| reserved | numeric(14,3) | `check (reserved >= 0)` |
| unique (business_id, item_id, stock_location_id) | | |

`stock_movements` — **append-only**: `item_id`, `stock_location_id`, `quantity` (signed, ≠ 0), `reason` (`receipt` \| `consumption` \| `sale` \| `adjustment_gain` \| `adjustment_loss` \| `count_correction` \| `transfer_in` \| `transfer_out` \| `return`), `ref_type`, `ref_id`, `note`, `unit_cost_minor` (nullable), `occurred_at`, `recorded_by`, `proposal_id`.

`stock_reservations` — `item_id`, `stock_location_id`, `quantity > 0`, `status` (`active` \| `released` \| `consumed`), `ref_type` (`appointment` \| `sales_order`), `ref_id`, `released_at`, `consumed_movement_id`. Index `(business_id, ref_type, ref_id)`.

`reorder_rules` — `item_id`, `stock_location_id`, `reorder_point` (alert when available < this), `target_level` (order up to), `lead_time_days`, `supplier_hint` (text, MVP). Unique per item and location.

`reorder_drafts` — `status` (`draft` \| `sent_to_purchasing` \| `cancelled`), `note`, `proposal_id`. `reorder_draft_lines` — `draft_id`, `item_id`, `quantity`, `reason_text`.

## Business rules
1. Every change to `stock_levels` happens after `SELECT … FOR UPDATE` of that level row (created on first use), in the same transaction as the movement or reservation. Concurrent operations on one item serialise there.
2. **Movement:** `on_hand += quantity`. If the result would be < 0 → `409 inventory.insufficient_on_hand`.
3. **Reserve:** `reserved += qty`. If `on_hand − reserved` would be < 0 and the business setting `allow_backorders` is false → `409 inventory.insufficient_stock` (detail: item, available, requested).
4. **Release:** `reserved −= qty`; status `released`. **No movement.** Releasing is idempotent per reservation.
5. **Consume:** creates a `consumption` movement (−qty) and `reserved −= qty`; status `consumed`.
6. **Adjust** (direct path) needs `inventory.stock.adjust` and a reason. Other users go through the `inventory.adjust_stock` proposal.
7. **Count correction:** `set_counted(item, location, counted)` writes one movement of `counted − on_hand`.
8. Whole-unit check: for `unit='each'`, quantities must be integers.
9. **Reorder suggestion (deterministic):** for each rule where `available < reorder_point`: `suggested = ceil(target_level − available)` (minus open purchase orders in v1). After any change that moves `available` from ≥ the reorder point to below it, publish `inventory.stock_below_reorder_level` once (dedupe key: item, location, date).
10. Movements and reservations are never edited or deleted (the app role has INSERT/SELECT on movements). Mistakes are fixed with compensating movements.

## Public interface (`public.py`)
```python
def get_availability(db, ctx, item_ids: list[UUID], stock_location_id=None) -> list[AvailabilityDto]  # on_hand, reserved, available, unit
def reserve_stock(db, ctx, *, item_id, quantity, ref_type, ref_id, stock_location_id=None) -> ReservationDto
def release_reservations(db, ctx, *, ref_type, ref_id) -> int
def consume_reservations(db, ctx, *, ref_type, ref_id) -> int
def record_movement(db, ctx, m: MovementIn) -> MovementDto        # purchasing receipts (v1), sales (v1)
def list_below_reorder(db, ctx) -> list[ReorderSuggestionDto]       # item, available, reorder_point, target, suggested, explanation
```

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/inventory/levels` | `inventory.stock.read` | `?q=&below_reorder=true` |
| GET | `/inventory/items/{item_id}` | read | Levels per location, recent movements, active reservations |
| GET | `/inventory/movements` | read | Ledger, filterable |
| POST | `/inventory/movements` | `inventory.stock.adjust` | Receipt or adjustment with reason |
| POST | `/inventory/counts` | `inventory.stock.adjust` | Count correction |
| GET / PUT | `/inventory/reorder-rules/{item_id}` | `inventory.reorder.read` / `.write` | |
| GET | `/inventory/reorder-suggestions` | `inventory.stock.read` | |
| GET / POST | `/inventory/reorder-drafts` | `inventory.reorder.read` / `.write` | |
| GET / POST | `/inventory/stock-locations` | read / `inventory.reorder.write` | |

## Permissions
`inventory.stock.read`, `inventory.stock.adjust`, `inventory.reorder.read`, `inventory.reorder.write`, `inventory.proposal.approve`.

## Events
Published: `inventory.stock_below_reorder_level`, `inventory.stock_adjusted`.

## Executors
`inventory.adjust_stock` (payload: item, location, quantity, reason, note; `describe()` shows on hand before → after). `inventory.create_reorder_draft` (payload: lines; creates a `reorder_drafts` row).

## Jobs
`inventory.reorder_scan` (hourly): re-evaluates rules and publishes missed crossings (idempotent through the dedupe key).

## Error codes
`inventory.insufficient_stock`, `inventory.insufficient_on_hand`, `inventory.not_stock_tracked`, `inventory.fractional_quantity`, `inventory.reservation_not_found`.

## UI
`/inventory`: table with **On hand · Reserved · Available** columns (available highlighted when below the reorder point). Item page has a ledger table (running balance), reservations with links to appointments, an adjust dialog (reason required) and a reorder rule form. A reorder suggestions panel offers "Create draft".

## Tests (must exist)
- `test_reserve_reduces_available_not_on_hand`
- `test_reserve_rejects_when_available_insufficient_unless_backorders`
- `test_concurrent_reservations_cannot_oversell` (threads)
- `test_release_is_idempotent_and_creates_no_movement`
- `test_consume_creates_consumption_movement`
- `test_count_correction_writes_difference_movement`
- `test_movements_are_append_only_for_app_role`
- `test_below_reorder_event_published_once_per_crossing`
- `test_reorder_suggestion_formula`

## Out of scope
Batches/lots, serial numbers, expiry dates, valuation methods (FIFO/average) (v1+: unit_cost recorded but not valued), multi-location transfers UI (movements support it).

Multi-item stock locks use item UUID then stock-location UUID order, including reservation release/consumption.
Consumption measures available stock before changing either reserved or on-hand quantities; consuming promised
stock leaves availability unchanged and cannot itself create a new below-reorder crossing.
