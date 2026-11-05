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
