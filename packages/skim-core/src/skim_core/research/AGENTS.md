# skim_core/research

Local research/search layer over stored posts.

## Rules

- Keep refresh, search, serialization, and store concerns separate.
- `research` CLI output should remain structured and deterministic for downstream tools.
- Missing login/session state should be a structured skip or warning, not an unhandled crash.
- Cover behavior changes with the existing `tests/test_research_*.py` files.
