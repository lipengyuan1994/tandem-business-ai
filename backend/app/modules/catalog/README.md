
`list_active_service_requirements(db, ctx) -> list[ServiceRequirements]` requires `catalog.item.read` and returns all
active services with durations, prices and required parts for intake context. Archived and foreign services are excluded.
