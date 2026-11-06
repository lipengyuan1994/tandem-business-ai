# Backend

See docs/roadmap/04-repository-and-conventions.md.

P10-C1 part 2 adds independently checked intake replies, shadow reply autonomy and stoppable delayed web publication.
Contract and validation evidence: [part 2 card](../docs/roadmap/ai/cards/P10-C1-part2.md).
Migration: `20261105_1100_grounded_publication` (after knowledge/handoff). Run the local PostgreSQL pytest scope in the
card, Ruff/mypy, OpenAPI export and `python3 -I data/validate_fixtures.py`; `make check` includes fixture validation.
Frontend countdown/Stop rendering and generated TypeScript belong to part 3; this slice supplies backend fields.
