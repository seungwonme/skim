# skim_core/crawlers/auth

Shared authentication helpers for CDP-based login flows.

## Rules

- Keep credential input via environment variables or existing desktop credential bridge; do not hard-code secrets.
- Session files belong under the configured workspace `data/sessions/` path.
- Preserve manual-login fallback when auto-fill fails.
- Test changes with `tests/test_cdp_autofill.py` or a focused new test.
