
`list_active_service_requirements(db, ctx) -> list[ServiceRequirements]` requires `catalog.item.read` and returns all
active services with durations, prices and required parts for intake context. Archived and foreign services are excluded.

P10-B2: optional service-only `recall_interval_months` (1–120), included in versioned writes/read/export. Public `recall_services` lists configured active services.

P10-E1: required_resources is an optional list of unique role/kind pairs (max three), primary first and matching resource_kind. Products reject requirements. Versioned create/update and requirements/public readers carry the list; recall_interval_months remains B2 configuration.
