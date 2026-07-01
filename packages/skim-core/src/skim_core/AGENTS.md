# skim_core

Core library for crawler contracts, persistence, enrichment, feed configuration, research, and shared utilities.

## Rules

- `Post` schema changes must be checked against `db.py`, desktop queries, and tests.
- Use `paths.workspace_root()`, `DATA_DIR`, and `SESSIONS_DIR` instead of hard-coded workspace paths.
- Crawler registration is centralized in `crawlers.REGISTRY`.
- Enrichment failures should not fail the crawl unless the caller explicitly requires hard failure.
- Add regression tests for behavior changes in DB writes, timestamp handling, research, or crawler metadata.
