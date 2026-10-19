# skim_core/crawlers/api

API or session-backed crawlers.

## Rules

- Supported API crawlers: `threads`, `linkedin`, `x`, `reddit`.
- Use saved sessions under `data/sessions/` when a platform requires login state.
- Exception: `threads` reads inside the dedicated Chrome profile through `crawlers/browser_profile.py` and does not load cookie files (#105). The other SNS crawlers are moving to the same path one PR at a time.
- Preserve stable `external_id` and ISO timestamp behavior; update `tests/test_social_api_metadata.py` or platform-specific tests when changing metadata.
- Reddit is HTTP/API based in this repo. Do not route it through removed browser crawler code.
