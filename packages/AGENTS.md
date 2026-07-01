# packages

Python workspace packages live here.

## Rules

- `skim-cli` owns command-line UX and dispatch only.
- `skim-core` owns crawlers, models, persistence, enrichment, research, and shared utilities.
- Do not create cross-package imports from core back into CLI.
- Validate package changes with `uv run pytest tests -q` plus the relevant root lint command.
