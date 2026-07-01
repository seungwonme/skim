# skim_cli

Typer CLI entrypoint for `uv run skim`.

## Rules

- Keep business logic in `skim_core`; `cli.py` should parse options, dispatch, and print user-facing status.
- Platform names and counts must come from `skim_core.crawlers.REGISTRY` where possible, not hard-coded text.
- When changing CLI UX, add focused coverage in `tests/` and verify `uv run skim --help`, `uv run skim platforms`, and at least one smoke command.
- Preserve persistence behavior: `crawl` initializes DB, records runs, saves posts, and writes JSON output.
