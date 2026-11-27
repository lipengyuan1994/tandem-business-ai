"""Payments relay (ADR-022 decision 3): a small service run by Boston AI Forward that holds the Stripe platform key.

It is a separate app (`uvicorn app.relay.main:create_app --factory`) with its own settings (`RELAY_*`) and its own
database. It imports nothing from `app.platform` or `app.modules`, and they import nothing from it.
"""
