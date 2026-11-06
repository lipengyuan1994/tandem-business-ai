
`list_active_service_requirements(db, ctx) -> list[ServiceRequirements]` requires `catalog.item.read` and returns all
active services with durations, prices and required parts for intake context. Archived and foreign services are excluded.

P10-B2: optional service-only `recall_interval_months` (1–120), included in versioned writes/read/export. Public `recall_services` lists configured active services.
