# Payments relay

Standalone service that holds the Stripe platform key for every Tandem server
([ADR-022](../../../docs/roadmap/03-decisions.md), runbook [payments-relay](../../../docs/runbooks/payments-relay.md)).
It imports nothing from Tandem (`tests/architecture/test_relay_isolation.py`); it shares only the backend image.

- `config.py` `RELAY_*` settings · `db.py`, `models.py` its own SQLite store · `crypto.py` seals client secrets
- `signing.py` the Tandem ↔ relay HMAC scheme (Tandem keeps a copy in `modules/payments/signing.py`)
- `stripe.py` the few Stripe calls it makes, form-encoded, with idempotency keys · `delivery.py` forwarding events
- `main.py` the app: client registration, the business's account and onboarding links, Stripe's Connect webhook
- `testing.py` a fake Stripe over `respx`, used by the relay's tests and the payments module's
