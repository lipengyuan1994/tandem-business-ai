# P3 · Business modules (Nov 3) — PRE

**Goal:** a usable ERP without AI. The flagship workflow works with a **staff-created** proposal. In P5 the AI creates the same proposals.
**Entry:** P2-06 done (approvals engine), P2-01/02 done.

Parallel lanes: **Lane A** (Tier A) P3-03 → P3-04 → P3-05 → P3-15. **Lane B** (Tier B) P3-01 → P3-02 → P3-06 → P3-07 → P3-08 → P3-14. **Lane C** (Tier B frontend) P3-09 … P3-13 as their backends land → P3-16.

### P3-01 · catalog backend
Kind: PRE · Tier: B · Module: M05 · Size: M · Depends on: P2-06
Read first: [M05](../modules/M05-catalog.md), [04](../04-repository-and-conventions.md)
Done when:
- [ ] All tests listed in M05 + standard tests pass; `make gen-api` run.

### P3-02 · crm backend
Kind: PRE · Tier: B · Module: M03 · Size: L · Depends on: P2-06
Read first: [M03](../modules/M03-crm.md)
Steps: tables, `current_consents` view, trigram search, match endpoint, timeline API, public functions. Executors are in P3-08.
Done when:
- [ ] M03 tests (except executor tests) + standard tests pass.

### P3-03 · inventory backend
Kind: PRE · Tier: A · Module: M07 · Size: L · Depends on: P3-01
Read first: [M07](../modules/M07-inventory.md), [07 Executors](../07-workflow-engine.md#executor-interface-platformregistrypy)
Steps: ledger, levels with row locks, reservations (reserve, release, consume), adjust and count, reorder rules, suggestions, drafts, the `inventory.adjust_stock` and `inventory.create_reorder_draft` executors, the reorder scan cron, the below-reorder event.
Done when:
- [ ] All M07 tests pass, including `test_concurrent_reservations_cannot_oversell`.

### P3-04 · scheduling: resources, availability and slot finder
Kind: PRE · Tier: A · Module: M06 · Size: L · Depends on: P2-01, P3-01
Read first: [M06](../modules/M06-scheduling.md) (Availability algorithm), [M02 settings](../modules/M02-org.md#tables)
Steps: resources, resource_services, availability rules and exceptions, the `appointments` table with the exclusion constraint (bookings come in P3-05), `find_slots` and `/slots`.
Done when:
- [ ] `test_find_slots_respects_hours_rules_exceptions_buffer_and_lead_time` and `test_find_slots_dst_transition_america_denver` pass, plus a property-style test: no returned slot overlaps an existing confirmed appointment (randomised data, fixed seed).

### P3-05 · scheduling: holds, bookings and executors
Kind: PRE · Tier: A · Module: M06 · Size: L · Depends on: P3-04, P3-03, P3-02, P3-06
Read first: [M06](../modules/M06-scheduling.md), [07 Holds](../07-workflow-engine.md#holds-scheduling-and-proposals-working-together)
Steps: `place_hold`, `book`, `cancel`, `reschedule`, `complete`, `no_show`; release expired holds (cron); subscriber for `approvals.proposal_closed`; executors `scheduling.book_appointment` (with the inline new customer and reply), `reschedule_appointment`, `cancel_appointment`; timeline entries; events.
Done when:
- [ ] All remaining M06 tests pass, including `test_concurrent_bookings_same_slot_one_succeeds_one_conflict` and `test_executor_new_customer_booking_and_reply_single_transaction`.

### P3-06 · conversations backend (including public chat)
Kind: PRE · Tier: B (visitor token and rate limits reviewed by Tier A) · Module: M04 · Size: L · Depends on: P3-02, P2-07
Read first: [M04](../modules/M04-conversations.md), [06 visitors](../06-security-and-privacy.md#authentication-flow-keycloak-backend-for-frontend)
Done when:
- [ ] All M04 tests pass; `conversations.send_reply` executor registered and tested.

### P3-07 · dashboard backend
Kind: PRE · Tier: B · Module: M11 · Size: S · Depends on: P3-05, P3-06, P3-03
Done when:
- [ ] M11 tests pass; `/dashboard/today` < 150 ms on the seed (logged in test).

### P3-08 · crm executors
Kind: PRE · Tier: B · Module: M03 · Size: S · Depends on: P3-02
Done when:
- [ ] `test_executor_update_fails_on_stale_version` and a create-customer executor test pass.

### P3-09 · Frontend: customers
Kind: PRE · Tier: B · Module: frontend/crm · Size: M · Depends on: P3-02, P2-08
Read first: [M03 UI](../modules/M03-crm.md#ui), [09](../09-frontend.md)
Done when:
- [ ] Component tests for list, profile tabs, consent history panel, create/edit dialog with a 409 conflict dialog.

### P3-10 · Frontend: catalog and inventory
Kind: PRE · Tier: B · Module: frontend/catalog, inventory · Size: M · Depends on: P3-01, P3-03
Done when:
- [ ] On hand / reserved / available columns, ledger with running balance, adjust dialog with reason, reorder suggestions panel; component tests pass.

### P3-11 · Frontend: schedule
Kind: PRE · Tier: B (calendar component reviewed by Tier A) · Module: frontend/scheduling · Size: L · Depends on: P3-05
Read first: [M06 UI](../modules/M06-scheduling.md#ui), [09 Times](../09-frontend.md#data-access-pattern)
Done when:
- [ ] `ResourceDayGrid` renders in the business time zone (test with America/Denver while the browser is in UTC), holds are hatched with a countdown, the slot finder books, and the conflict response shows a clear message; component tests pass.

### P3-12 · Frontend: inbox and public pages
Kind: PRE · Tier: B · Module: frontend/conversations, public-widget · Size: L · Depends on: P3-06
Done when:
- [ ] Inbox three-pane layout; internal note toggle; link customer; public chat and request pages with a privacy notice; the public bundle is ≤ 80 KB gzip.

### P3-13 · Frontend: Today and action-specific approval forms
Kind: PRE · Tier: B · Module: frontend/dashboard, approvals · Size: M · Depends on: P3-07, P3-05, P2-09
Done when:
- [ ] Today cards with permission-aware sections; custom edit forms for `scheduling.book_appointment` (slot picker), `conversations.send_reply` (text) and `inventory.adjust_stock`; component tests pass.

### P3-14 · Seed command
Kind: PRE · Tier: B · Module: cli · Size: M · Depends on: P3-05, P3-06, P3-08
Read first: [05 Seed](../05-data-model.md#seed-and-demo-data), `data/README.md`
Create: `python -m app.cli seed [--reset]` loading `data/fixtures.json` through module services (as system context), mapping fixture keys to `fixture_id()`. Keycloak users for seeded staff are created in the dev realm only.
Done when:
- [ ] `make seed` on a fresh dev DB produces the demo state; running it twice is idempotent (`--reset` wipes the two demo businesses first).

### P3-15 · Integration and concurrency suite
Kind: PRE · Tier: A · Module: tests · Size: M · Depends on: P3-05, P3-06
Create: `backend/tests/integration/test_flagship_without_ai.py` and `test_concurrency_suite.py`
Done when:
- [ ] Flagship flow: visitor appointment request → staff creates a `book_appointment` proposal with hold + reply → approve → appointment confirmed, part reserved (available −1, on hand unchanged), reply delivered (web chat), timeline has 3 entries, audit complete.
- [ ] Concurrency: double approval click, two bookings for one slot, two reservations for the last unit. Each yields exactly one success.

### P3-16 · End-to-end tests (Playwright)
Kind: PRE · Tier: B · Module: frontend/e2e · Size: M · Depends on: P3-09 … P3-14
Done when:
- [ ] `make e2e` passes: login with TOTP, flagship flow through the UI, conflict dialog, business switch isolation, axe scan with zero serious violations on Today, Inbox, Approvals, Customers, Schedule and Inventory.

## Exit gate P3
- [ ] Flagship workflow without AI works in the UI with seeded data.
- [ ] `make check` and `make e2e` green; Tier-A spot review of scheduling, inventory and executors.
- [ ] P3 entries in `PRE_EVENT_WORK.md` (dated).
