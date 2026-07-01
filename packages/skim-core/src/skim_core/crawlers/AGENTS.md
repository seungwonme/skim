# skim_core/crawlers

Crawler protocol, platform registry, and platform-specific crawler implementations.

## Current Registry

`threads`, `linkedin`, `x`, `reddit`, `hackernews`, `geeknews`, `youtube`, `producthunt`, `arxiv`, `huggingface`, `everyto`, `blogs`, `ailabs`

## Rules

- New crawlers implement `base.Crawler` and are registered in `__init__.py`.
- Feed crawlers live under `feed/`; API/login-backed crawlers live under `api/`.
- Removed browser-driven crawler paths must stay removed.
- Keep public platform behavior stable and add tests for external IDs, timestamps, and option handling.
