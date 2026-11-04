# M06 · scheduling

| Field | Value |
|---|---|
| Release / phase | MVP · P3 |
| Tier / kind | **A** for availability, booking and executors; B for resource CRUD and UI · PRE |
| Depends on | platform, org, crm, catalog, inventory, conversations, approvals, notifications |
| Owns tables | `resources`, `resource_services`, `availability_rules`, `availability_exceptions`, `appointments` |

## Purpose
Who or what can be booked, when they are available, and appointments, including short-lived **holds** used during proposal review. It guarantees no double-booking at the database level. Availability is computed deterministically; the AI only calls it.

## Tables
`resources` — `location_id` (FK locations), `kind` (`staff` \| `room` \| `equipment`), `name`, `user_id` (nullable; staff resource linked to a user), `capacity` (always 1 in MVP), `color`, `status` (`active` \| `archived`).

`resource_services` — `(resource_id, service_id)`: which resources can perform which services. **If a service has no rows here, every active resource of the service's `resource_kind` qualifies.**

`availability_rules` — `resource_id`, `weekday 0–6`, `start_time time`, `end_time time`, `valid_from date null`, `valid_to date null`. Interpreted in the resource's location time zone.

`availability_exceptions` — `resource_id` (null = whole location), `location_id`, `starts_at`, `ends_at` (UTC), `kind` (`closed` \| `extra_open`), `reason`.

`appointments`

| Column | Type | Null | Notes |
|---|---|---|---|
| customer_id | uuid | no | Composite FK customers |
| service_id | uuid | no | Composite FK catalog_items (kind=service) |
| resource_id | uuid | no | Composite FK resources |
| location_id | uuid | no | |
| starts_at | timestamptz | no | |
| ends_at | timestamptz | no | `starts_at + duration` |
| blocked_until | timestamptz | no | `ends_at + buffer`; used by the no-overlap constraint |
| timezone | text | no | Location zone at booking (for display and explanations) |
| status | text | no | `held` \| `confirmed` \| `cancelled` \| `completed` \| `no_show` |
| hold_expires_at | timestamptz | yes | Required when `held` |
| proposal_id | uuid | yes | Proposal that created the hold or booking |
| source | text | no | `staff` \| `assistant` \| `public_request` |
| notes | text | yes | **PII possible** |
| cancelled_reason | text | yes | |

**No-overlap constraint (requires `btree_gist`):**
```sql
alter table appointments add constraint appointments_no_overlap
  exclude using gist (business_id with =, resource_id with =, tstzrange(starts_at, blocked_until, '[)') with &&)
  where (status in ('held', 'confirmed'));
```

## Availability algorithm (`find_slots`) — deterministic

Input: `service_id`, `earliest`, `latest` (UTC; maximum span 14 days), optional `resource_id`, optional `location_id` (default location), `limit` (default 10).

1. `req = catalog.get_service_requirements(service_id)` → duration, buffer, resource_kind.
2. Candidate resources = active resources of `resource_kind` at the location that qualify (see `resource_services`), filtered by `resource_id` if given.
3. For each local calendar day overlapping the window (in the location time zone):
   - business open intervals = `org.get_opening_hours` for that weekday;
   - resource intervals = matching `availability_rules` (valid on that date);
   - working = open ∩ resource; minus `closed` exceptions; plus `extra_open` exceptions;
   - convert to UTC with `zoneinfo`. Skip non-existent local times; use `fold=0` for ambiguous times.
4. Busy = this resource's appointments with status `confirmed`, or `held` with `hold_expires_at > now()`, as `[starts_at, blocked_until)`.
5. Free = working − busy. Candidate starts are aligned to `slot_granularity_minutes` in **local** time. A start is valid when `[start, start + duration + buffer)` fits inside a free interval, `start ≥ now + booking_lead_time_minutes` and `start ≥ earliest`, and `start < latest` (half-open window). (`latest` bounds the start time, so "Tuesday morning" means starting before noon.)
6. Return slots sorted by start, then resource name, up to `limit`: `{starts_at, ends_at, resource_id, resource_name, local_label}` (`local_label` e.g. "Tue 10 Nov 10:00 MST").

## Business rules
1. The database constraint is the final guard. Services convert an exclusion violation into `409 scheduling.slot_unavailable`.
2. Before inserting or confirming, the booking service cancels **expired** holds overlapping the target slot, in the same transaction.
3. A hold (`place_hold`) needs `scheduling.appointment.write` (granted to the assistant context) and is valid for `hold_minutes`. Placing one never notifies anyone and never reserves stock.
4. Booking (`book`) — the same function for direct staff booking and the executor:
   1. validate that the service and customer are active and that the slot is inside working hours, unless `override_hours=true` (staff only, audited);
   2. confirm the given hold if it is still valid, otherwise insert a confirmed row;
   3. for each service part, `inventory.reserve_stock(ref_type='appointment', ref_id=appointment.id)`. Insufficient stock fails the whole booking (`inventory.insufficient_stock`) unless `skip_parts=true` (staff only, audited);
   4. `crm.add_timeline_entry`, audit, publish `scheduling.appointment_confirmed`.
5. Cancel: status `cancelled`, `inventory.release_reservations(ref)`, timeline, audit, event. **No stock movement.**
6. Reschedule: version check, new times through the same overlap guard, and reservations kept. If the service changes, release and re-reserve.
7. Complete: status `completed` and `inventory.consume_reservations(ref)`, which creates `consumption` movements. No-show: status `no_show` and release reservations.
8. Expired holds are cancelled by the job `scheduling.release_expired_holds` every minute. When `approvals.proposal_closed` arrives with an outcome other than `succeeded`, holds with that `proposal_id` are released immediately.
9. `bind_hold` requires `scheduling.appointment.write` and locks the appointment in the current business. It accepts
   only `held` appointments whose `proposal_id` is null or already the requested proposal (idempotent). Otherwise it
   raises `scheduling.hold_not_bindable`. The target proposal must exist in the same business, verified with the
   approvals public lookup even on idempotent calls. Proposal creation and binding belong in the same transaction.

## Public interface (`public.py`)
```python
def find_slots(db, ctx, q: SlotQuery) -> list[SlotDto]
def place_hold(db, ctx, *, service_id, resource_id, starts_at, customer_id, proposal_id=None) -> HoldDto
def bind_hold(db, ctx, hold_id, proposal_id) -> None
def get_appointment(db, ctx, appointment_id) -> AppointmentDto
def list_appointments(db, ctx, *, start, end, resource_id=None, customer_id=None) -> list[AppointmentDto]
def book(db, ctx, req: BookingRequest) -> AppointmentDto          # used by executor
def cancel(db, ctx, appointment_id, version, reason) -> AppointmentDto
def reschedule(db, ctx, appointment_id, version, new_starts_at, resource_id=None) -> AppointmentDto
def agenda_for_day(db, ctx, local_date) -> list[AppointmentDto]      # dashboard
```

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET / POST | `/scheduling/resources` | `scheduling.resource.read` / `.write` | |
| PATCH | `/scheduling/resources/{id}` | write | |
| GET / PUT | `/scheduling/resources/{id}/availability` | read / write | Replace weekly rules |
| GET / PUT | `/scheduling/resources/{id}/services` | read / write | |
| GET / POST | `/scheduling/exceptions` | read / write | |
| GET | `/scheduling/slots` | `scheduling.appointment.read` | `?service_id&earliest&latest&resource_id` |
| POST | `/scheduling/holds` | `scheduling.appointment.write` | Hold a slot while a booking proposal is reviewed (body: service, resource, customer, starts_at) |
| POST | `/public/{slug}/appointment-requests` | public | Moved here from M04 (see M04 as-built note); rate limited |
| GET | `/scheduling/appointments` | read | `?start&end&resource_id&customer_id&status` |
| POST | `/scheduling/appointments` | `scheduling.appointment.write` | Direct booking (staff) |
| GET | `/scheduling/appointments/{id}` | read | |
| POST | `/scheduling/appointments/{id}/cancel` \| `/reschedule` \| `/complete` \| `/no-show` | write | Body includes `version` |

## Permissions
`scheduling.resource.read`, `scheduling.resource.write`, `scheduling.appointment.read`, `scheduling.appointment.write`, `scheduling.appointment.override` (hours or parts override; owner and admin), `scheduling.proposal.approve`.

## Events
Published: `scheduling.appointment_confirmed`, `scheduling.appointment_cancelled`, `scheduling.appointment_rescheduled`, `scheduling.appointment_completed`. Subscribed: `approvals.proposal_closed` (release holds).

## Executors
| Action type | Payload | `describe()` shows |
|---|---|---|
| `scheduling.book_appointment` | `customer: {id, version} \| {new: CustomerCreate}`, `service_id`, `resource_id`, `starts_at`, `hold_id?`, `reply?: {conversation_id, body}` | Customer (new or existing), local date and time with zone, resource, service, parts to reserve with current availability, reply text |
| `scheduling.reschedule_appointment` | `appointment_id`, `appointment_version`, `new_starts_at`, `resource_id?`, `reply?` | Old → new time |
| `scheduling.cancel_appointment` | `appointment_id`, `appointment_version`, `reason`, `reply?` | Cancellation + reservations released |

The executor creates the customer through `crm.create_customer` if `new`, then calls `book`, then `conversations.send_reply` if `reply` is present, all in one transaction.

## Jobs
`scheduling.release_expired_holds` (cron, every minute).

## Error codes
`scheduling.slot_unavailable`, `scheduling.outside_working_hours`, `scheduling.lead_time_violation`, `scheduling.resource_not_qualified`, `scheduling.hold_expired`, `scheduling.appointment_not_found`, `scheduling.appointment_version_conflict`, `scheduling.invalid_window`.

## UI
`/schedule`: `ResourceDayGrid` (resources as columns, 15-minute rows in local time, current-time line; holds hatched with expiry countdown; status colours) plus a week view. The appointment drawer has actions. The new-appointment form has a slot finder that calls `/slots`. Settings → Resources with a weekly availability editor.

## Tests (must exist)
- `test_find_slots_respects_hours_rules_exceptions_buffer_and_lead_time`
- `test_find_slots_dst_transition_america_denver` (8 Mar and 1 Nov 2026 transitions)
- `test_concurrent_bookings_same_slot_one_succeeds_one_conflict` (two connections)
- `test_hold_blocks_slot_until_expiry_then_slot_free`
- `test_book_confirms_valid_hold_and_reserves_parts_atomically`
- `test_booking_fails_entirely_when_part_insufficient`
- `test_cancel_releases_reservation_without_stock_movement`
- `test_complete_consumes_reservation_with_movement`
- `test_executor_new_customer_booking_and_reply_single_transaction`
- `test_holds_released_when_proposal_rejected`

## Out of scope
Recurring appointments, multi-resource appointments (technician + bay), capacity > 1 (classes), customer self-service rebooking, external calendar sync (v1+ via connector).

## As built (P3, 2026-11-03)
- Slot windows are half-open: a start is offered when `start < latest`, so `latest` bounds the start time (see the algorithm above).
- `POST /scheduling/holds` exists so staff (and later the assistant) can hold a slot before proposing a booking.
- UI: `ResourceDayGrid` places appointments by wall-clock minutes in the business zone (`features/scheduling/grid.ts`; tests run with the browser in UTC). Holds are hatched (`hatched` utility in `theme.css`) with a minute countdown. The grid widens beyond 07:00–19:00 to fit appointments. The slot finder books directly and turns `scheduling.slot_unavailable` into a plain message. A week view and drag-to-reschedule are not built yet (v1).

Public appointment requests require enabled public intake and an active service marked bookable online.
They accept optional `consent_to_contact` (default false) and a validated e-mail address, and pass the flag
to conversations for the same server-owned request-contact evidence as public chat (M04).
Generated conversation subjects are limited to 200 characters. The response sets a visitor cookie and supplies
`reply_url`, which the request widget exposes so requesters can retrieve staff replies.
Stock parts are reserved in item UUID order, matching sales and purchasing. Slot boundary rounding preserves
the DST fold so a repeated-hour start never precedes the absolute lower bound.

Appointment mutations acquire the customer before appointment/stock locks, matching booking and conversation writes.
