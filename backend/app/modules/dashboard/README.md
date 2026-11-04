# dashboard · M11

Read-only Today aggregation through module public APIs; owns no tables.

- `public.today_summary(db, ctx) -> TodaySummary` uses the business timezone and DST-safe local day bounds.
- The `/api/v1/dashboard/today` endpoint returns the same fields and permission-filtered sections as the public read.
  Unreadable sections remain null. Agenda behavior is unchanged (the full business-local day).
- `/api/v1/dashboard/counters` provides navigation badges; no aggregation writes or model calls.
- The caller owns the transaction. Tests compare the public DTO to the seeded endpoint for owner and inventory roles.
