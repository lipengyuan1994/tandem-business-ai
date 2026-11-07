# scheduling (M06)

Spec: [M06](../../../../docs/roadmap/modules/M06-scheduling.md).

`place_hold` blocks a slot reversibly without notifying anyone or reserving stock.
`bind_hold(db, ctx, hold_id, proposal_id) -> None` requires `scheduling.appointment.write` and locks a hold scoped to
the current business. Only `held` appointments with no proposal, or already bound to the same proposal, are bindable.
The target proposal must also exist in the current business, checked through approvals' public `proposal_exists`
lookup before binding, including idempotent calls. Missing or foreign holds/proposals, other appointment statuses,
and binding to a different proposal raise
`Conflict("scheduling.hold_not_bindable")`. Repeated binding to the same proposal is a no-op. Create the proposal
and bind its hold in the same caller-owned transaction.

The `approvals.proposal_closed` subscriber releases bound holds for every outcome except `succeeded`.
The existing expiry jobs continue to release expired holds.

The public binding boundary also checks `hold_expires_at > now` under the hold's row lock. Expired holds cannot be
bound, including before the expiry job runs and on repeated binding to the same proposal.

P10-B2: completed events include completed_at. Public `completed_service_page`, `latest_completed_appointment`, `has_future_service_appointment` provide bounded care discovery. `get_appointment(...,lock=False)` supports non-locking fact reads.

P10-E1: optional catalog required_resources allocate a qualified primary plus room/equipment at one location. `slots.py` owns joint availability; `holds.py` holds/allocates; `resources.py` CRUD; `reads.py` reads/expiry. `appointment_resources` mirrors primaries and additional resources under one exclusion constraint. Triggers synchronize every parent lifecycle change; the migration backfills no rows. Slots and appointments expose named resources; exports include the ledger.

P10-C2b: `self_service{,_api,_models,_schemas}.py` owns hash-only 20-minute magic tokens, business/customer scoped
60-minute idle sessions and fee/change receipts (forced RLS). `/public/{slug}/self-service/{request,exchange,bookings}`
plus owned slots/cancel/reschedule routes: exact Origin, public rate limits, active-customer locks, version and exact
fee-policy acknowledgment. Reschedule consumes a canonical hold then updates the existing booking/reservations.
Customer actions are visitor `act_notify`, with timeline/audit, staff notifications and transactional outbox mail.
Export excludes capability hashes; erasure revokes/clears fee text; a migration trigger revokes access on any customer
deactivation/restriction/erasure and cancels queued self-service mail on complete e-mail opt-out. Migration follows B2.
