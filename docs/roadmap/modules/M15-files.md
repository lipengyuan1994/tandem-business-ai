# M15 · files (v1)

| Field | Value |
|---|---|
| Release / phase | v1 · P8 |
| Tier / kind | B |
| Depends on | platform |
| Owns tables | `files` |

## Purpose
Attachments (photos of a damaged item, supplier invoices, documents) stored in **Vultr Object Storage** (S3-compatible), referenced from other modules by ID.

## Table
`files` — `object_key` (`{business_id}/{yyyy}/{mm}/{file_id}`), `filename`, `content_type`, `size_bytes`, `sha256`, `uploaded_by`, `ref_type`, `ref_id`, `status` (`pending` \| `ready` \| `deleted`), `deleted_at`.

## Design
- Upload: `POST /files` → server validates type (allowlist: jpg, png, webp, pdf; ≤ 10 MB) and returns a **pre-signed PUT URL** (5 minutes) → browser uploads directly → `POST /files/{id}/complete` → server checks size and hash with a HEAD request → `ready`.
- Download: `GET /files/{id}` → permission check through the referencing module's read permission → 302 to a pre-signed GET URL (5 minutes).
- Buckets are private. Server-side encryption per Vultr capability (verify), plus objects that are not public.
- Deletion: soft delete, then a purge job deletes objects after 30 days.
- Library: `boto3` with a custom endpoint (`OBJECT_STORAGE_ENDPOINT`); the endpoint host goes in the egress allowlist.

## Public interface
`attach(db, ctx, file_id, ref_type, ref_id)`, `list_for(db, ctx, ref_type, ref_id)`, `erase_for(db, ctx, ref_type, ref_id)`.

## Tests
- `test_presign_rejects_disallowed_type_and_size`
- `test_complete_verifies_size_and_hash`
- `test_download_requires_permission_on_referenced_record`
