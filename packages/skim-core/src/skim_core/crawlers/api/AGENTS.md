# skim_core/crawlers/api

API or session-backed crawlers.

## Rules

- Supported API crawlers: `threads`, `linkedin`, `x`, `reddit`.
- Use saved sessions under `data/sessions/` when a platform requires login state.
- Preserve stable `external_id` and ISO timestamp behavior; update `tests/test_social_api_metadata.py` or platform-specific tests when changing metadata.
- Reddit is HTTP/API based in this repo. Do not route it through removed browser crawler code.
