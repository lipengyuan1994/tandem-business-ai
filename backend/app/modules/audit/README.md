# audit (M09) — module card

Spec: [docs/roadmap/modules/M09-audit.md](../../../../docs/roadmap/modules/M09-audit.md). Writing audit events is `app.platform.audit.record`.

- Endpoints: `/audit/events` (filters, cursor pagination), `/audit/events/{id}`, `/audit/export.csv` (audited).

CSV exports prefix potentially executable spreadsheet text cells (including staff display names) with an apostrophe.
