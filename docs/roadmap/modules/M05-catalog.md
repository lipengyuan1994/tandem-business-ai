# M05 · catalog

| Field | Value |
|---|---|
| Release / phase | MVP · P3 |
| Tier / kind | B · PRE |
| Depends on | platform, org, approvals |
| Owns tables | `catalog_items`, `service_parts` |

## Purpose
What the business sells: **services** (bookable, with a duration and the resource type they need) and **products** (stock items, possibly sold or used as parts). It holds prices, which the AI quotes only from here. It also records which parts a service consumes.

## Tables
`catalog_items`

| Column | Type | Null | Notes |
|---|---|---|---|
| kind | text | no | `service` \| `product` |
| sku | text | yes | Unique per business when present |
| name | text | no | |
| description | text | yes | |
| unit | text | no | `each` for products; `each` or `hour` for services |
| price_minor | bigint | yes | Null = "price on request" |
| currency | char(3) | no | Defaults to business currency |
| tax_code | text | yes | Free text in MVP |
| duration_minutes | int | yes | **Required for services**, 5–1440 |
| buffer_minutes | int | no | Default 0; blocks time after a service |
| resource_kind | text | yes | For services: `staff` \| `room` \| `equipment` |
| is_stock_tracked | bool | no | Products only |
| is_bookable_online | bool | no | Service visible on public request form |
| status | text | no | `active` \| `archived` |

`service_parts` — `service_id` (composite FK, kind=service), `item_id` (composite FK, kind=product, stock tracked), `quantity numeric(14,3) > 0`. Unique `(service_id, item_id)`.

## Business rules
1. Services must have `duration_minutes` and `resource_kind`. Products must not have a duration.
2. A part must be a stock-tracked, active product.
3. Archiving an item that is a required part of an active service → 409 unless the service is archived first or the part is removed.
4. Price changes do not affect existing appointments (v1 sales documents copy prices at creation).

## Public interface (`public.py`)
```python
def get_item(db, ctx, item_id) -> CatalogItemDto
def search_items(db, ctx, query: str | None, kind: str | None, limit=20) -> list[CatalogItemDto]
def get_service_requirements(db, ctx, service_id) -> ServiceRequirements   # duration, buffer, resource_kind, parts [(item_id, qty)]
def list_public_services(db, ctx) -> list[PublicServiceDto]               # name, description, duration, price
```

## API
| Method | Path | Permission |
|---|---|---|
| GET / POST | `/catalog/items` | `catalog.item.read` / `catalog.item.write` |
| GET / PATCH | `/catalog/items/{id}` | read / write |
| POST | `/catalog/items/{id}/archive` | write |
| GET / PUT | `/catalog/items/{id}/parts` | read / write (replace list) |
| GET | `/public/{slug}/services` | public |

## Permissions
`catalog.item.read`, `catalog.item.write`, `catalog.proposal.approve` (reserved).

## Events
Published: `catalog.item_changed`.

## Error codes
`catalog.item_not_found`, `catalog.sku_taken`, `catalog.service_requires_duration`, `catalog.invalid_part`, `catalog.item_in_use`.

## UI
`/catalog`: tabs Services / Products; item form; parts editor on services (search products, quantity).

## Tests (must exist)
- `test_service_requires_duration_and_resource_kind`
- `test_part_must_be_stock_tracked_product`
- `test_cannot_archive_required_part_of_active_service`
- `test_public_services_only_lists_bookable_online_active`

## Out of scope
Variants, bundles, price lists per customer group, tax calculation engines.
