---
name: skim
description: "Use when user asks to operate a local Skim workspace: inspect crawl DB health, run targeted crawls, search collected posts, prepare source inventory bundles, triage crawler/session issues, or create source-backed research summaries. Do NOT use for generic web search, hosted crawling, secret extraction, blog publishing, or external notifications."
argument-hint: "[status|research|coverage|refresh|triage|bundle] [topic/date/platform]"
license: MIT
compatibility: "Requires Python 3.12+, uv, and a Skim checkout. Uses local SQLite and user-owned session files."
metadata:
  version: "0.2.0"
  repository: "https://github.com/seungwonme/skim"
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
user-invocable: true
---

# Skim

Operate Skim as a local data source. Prefer the CLI over hand-written SQL.

## Ground Rules

- Use the user's language for the final answer.
- Treat `data/skim.db`, `data/sessions/*.json`, and `uv run skim --help` as active truth.
- Do not print passwords, cookies, Keychain values, or raw session file contents.
- Do not publish blog posts or send external notifications from this skill.
- Do not use web search unless the user explicitly asks for external research.
- Keep outputs source-backed: include platform, title or author, and URL for cited items.
- For wide synthesis, create a bundle under `/tmp/skim/<slug>/` before writing the final answer.

## Repo Selection

Run commands from a Skim checkout.

1. If the current directory has `packages/skim-cli/pyproject.toml`, use it.
2. Else if `SKIM_LOCAL_PATH` points to a checkout with `packages/skim-cli/pyproject.toml`, `cd` there.
3. Else stop and ask the user to run from a Skim checkout or set `SKIM_LOCAL_PATH`.

Use `uv run skim ...` inside a checkout. For released, out-of-tree execution, use `uv tool run --from 'git+https://github.com/seungwonme/skim@vX.Y.Z#subdirectory=packages/skim-cli' skim ...` only after that tag contains the current CLI.

## Modes

- Status or health: run `uv run skim doctor`.
- Platform triage: run `uv run skim doctor --platform <name>`.
- Refresh planning: run `uv run skim refresh-plan --days <n>`.
- Coverage: run `uv run skim coverage --days <n>`.
- Bundle handoff: run `uv run skim bundle [topic] --days <n>`.
- Fresh data: run the smallest useful `uv run skim crawl ...` command.
- Topic research: run `uv run skim research "TOPIC" --days 7 --sources all --refresh auto --emit json`.

## Output Contract

- Report weak coverage before drawing conclusions.
- Report bundle paths when files are created.
- Report the smallest next command when data is missing.
