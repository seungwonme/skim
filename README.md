# skim

<p align="center"><b>English</b> | <a href="README.ko.md">한국어</a></p>

![status: local-first crawler](https://img.shields.io/badge/status-local--first%20crawler-blue)
![last commit](https://img.shields.io/github/last-commit/seungwonme/skim)
![license: MIT](https://img.shields.io/badge/license-MIT-green)
![python: 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

> Local-first information curation pipeline for feeds, social timelines, papers, videos, and AI lab updates.

Skim collects posts from multiple public feeds and session-based social sources, stores them in local SQLite, and exposes them through a Python CLI and a macOS desktop app.

> [!WARNING]
> Some API crawlers use authenticated browser sessions and unofficial web endpoints. Use your own accounts, respect each platform's terms and rate limits, and expect upstream pages/APIs to change.

## Contents

| Path | Purpose |
|---|---|
| `packages/skim-core/` | Crawlers, models, enrichment, SQLite persistence, research/search |
| `packages/skim-cli/` | Typer CLI exposed as `uv run skim ...` |
| `apps/desktop/` | React + Vite + Tauri desktop app |
| `tooling/scripts/` | Import, cron, and maintenance scripts |
| `docs/` | Design notes, crawler notes, source backlog, generated implementation plans |
| `data/` | Local runtime artifacts: SQLite DB, session files, crawl JSON output |

## Supported Sources

| Type | Platform | Source |
|---|---|---|
| Feed | Hacker News | hnrss.org |
| Feed | GeekNews | news.hada.io Atom |
| Feed | YouTube | RSS + `yt-dlp` |
| Feed | Product Hunt | RSS |
| Feed | arXiv | Atom API |
| Feed | Hugging Face | Daily Papers JSON API |
| Feed | Every.to | RSS feeds |
| Feed | Blogs | RSS feeds in `PERSONAL_BLOGS` |
| Feed | AI Labs | OpenAI RSS, Anthropic pages, LangChain blog |
| API | Threads | Instagram Private API |
| API | X | GraphQL via `twitter-api-client` |
| API | LinkedIn | Voyager GraphQL |
| API | Reddit | JSON listing API |

## Install

```bash
pnpm install
uv sync
uv run playwright install
cp .env.example .env
```

Python requires 3.12+. Node tooling uses `pnpm` and `turbo`; Python tooling uses `uv`.

## Usage

List platforms:

```bash
uv run skim platforms
```

Crawl recent sources:

```bash
uv run skim crawl all --days 1
uv run skim crawl hackernews --count 10
uv run skim crawl reddit --subreddit python --sort hot --count 10
```

Search the local post store:

```bash
uv run skim research "AI video" --days 7 --emit summary
uv run skim research "vector database" --sources hackernews,arxiv --emit json
```

Login for session-based sources:

```bash
uv run skim login threads
uv run skim login reddit
```

## Desktop App

```bash
pnpm desktop:dev
pnpm desktop:build
```

The desktop app reads the same local workspace as the CLI. It can inspect session status, trigger login, manage tracked sources, and browse `data/skim.db`.

macOS DMG builds are produced under `target/release/bundle/dmg/` by Tauri.

## Data And Auth

- SQLite database: `data/skim.db`
- Session cookies: `data/sessions/*.json`
- Crawl output: `data/<platform>/*.json`
- Optional workspace override: `SKIM_WORKSPACE_ROOT`

`.env` is only a login helper. Existing sessions are reused from `data/sessions/*.json`; the desktop credential flow stores credentials in macOS Keychain and injects them into the login process when needed.

## Environment Variables

```bash
SKIM_WORKSPACE_ROOT=

THREADS_USERNAME=
THREADS_PASSWORD=
LINKEDIN_USERNAME=
LINKEDIN_PASSWORD=
X_USERNAME=
X_PASSWORD=
REDDIT_USERNAME=
REDDIT_PASSWORD=
```

## Development

```bash
pnpm lint
pnpm test
pnpm build
pnpm typecheck
```

Python-only checks:

```bash
uv run pytest tests -q
uv run black packages tests tooling/scripts --config pyproject.toml
uv run isort packages tests tooling/scripts --settings-path pyproject.toml
uv run flake8
uv run pylint packages/skim-core/src/skim_core packages/skim-cli/src/skim_cli tooling/scripts
```

## Documentation

- `AGENTS.md` - AI working guide and repository conventions
- `docs/TODO.md` - source backlog and promotion checklist ([Korean](docs/TODO.ko.md))
- `docs/THREADS.md` - Threads crawler implementation notes
- `docs/DESIGN.md` - historical design inspiration note, not the current product spec

## Out Of Scope

Skim is not a hosted crawler service, a data resale product, or a way to bypass platform access controls. It is designed for local personal curation with user-owned sessions and public feeds.

## Contributing

Small fixes are welcome. Useful contribution areas:

- broken feed/API adapters
- timestamp or metadata normalization bugs
- focused tests for crawler, DB, or research behavior
- documentation corrections that match current CLI behavior

Before opening a PR, run the smallest relevant test first, then the root gates when the change touches shared behavior.

## License

MIT. See `LICENSE`.
