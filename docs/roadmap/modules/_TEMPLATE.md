# Mxx · <name>

| Field | Value |
|---|---|
| Release / phase | MVP · P3 |
| Tier / kind | B · PRE |
| Depends on | platform, … (must match `app/modules/dependencies.py`) |
| Owns tables | … |

## Purpose
Two or three sentences: what this module is responsible for, and what it is explicitly not responsible for.

## Tables
`table_name` — one line on what a row is.

| Column | Type | Null | Notes |
|---|---|---|---|
| … | … | … | PII? constraint? FK? |

Constraints and indexes: …

## Business rules
1. Numbered, testable statements ("Available quantity can never go below zero unless backorders are enabled").

## Public interface (`public.py`)
```python
def function_name(db: Session, ctx: Context, ...) -> Dto: ...
```

## API
| Method | Path | Permission | Notes |
|---|---|---|---|

## Permissions
`module.resource.action` — meaning.

## Events
Published: … · Subscribed: …

## Executors and jobs
…

## Error codes
`module.reason` — when.

## UI
Pages and components (see [09-frontend.md](../09-frontend.md)).

## Tests (must exist)
- `test_…`

## Out of scope
…
