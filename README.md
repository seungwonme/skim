# skim

<p align="center"><b>English</b> | <a href="README.ko.md">한국어</a></p>

<p align="center">
  <img src="images/skim-readme-banner.png" alt="Skim local-first information curation pipeline banner" width="100%">
</p>

![status: local-first crawler](https://img.shields.io/badge/status-local--first%20crawler-blue)
![last commit](https://img.shields.io/github/last-commit/seungwonme/skim)
![license: MIT](https://img.shields.io/badge/license-MIT-green)
![python: 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

> Local-first information curation pipeline for feeds, social timelines, papers, videos, and AI lab updates.

Skim collects posts from multiple public feeds and session-based social sources, stores them in local SQLite, and exposes them through a Python CLI and a Swift macOS desktop app.

> [!WARNING]
> Some API crawlers use authenticated browser sessions and unofficial web endpoints. Use your own accounts, respect each platform's terms and rate limits, and expect upstream pages/APIs to change.

## Contents

| Path | Purpose |
|---|---|
| `packages/skim-core/` | Crawlers, models, enrichment, SQLite persistence, research/search |
| `packages/skim-cli/` | Typer CLI exposed as `uv run skim ...` |
| `apps/desktop/` | SwiftUI macOS desktop app |
| `scripts/` | Import, cron, and maintenance scripts |
| `images/` | README and project images |
| `docs/` | Design notes, crawler notes, source backlog, generated implementation plans |
| `data/` | Local runtime artifacts: SQLite DB, session files, crawl JSON output |

## Supported Sources

| Type | Platform | Source |
|---|---|---|
| Feed | Hacker News | hnrss.org newest + Show + Ask |
| Feed | Lobsters | RSS + per-story JSON (comments) |
| Feed | GeekNews | news.hada.io Atom |
| Feed | YouTube | RSS + `yt-dlp` |
| Feed | Product Hunt | RSS |
| Feed | arXiv | Atom API (cs.AI, cs.CL, cs.LG, cs.CV) |
| Feed | Hugging Face | Daily Papers JSON API |
| Feed | Every.to | RSS feeds |
| Feed | Blogs | Registered RSS feeds in `tracked_sources`; `PERSONAL_BLOGS` provides fallback seeds |
| Feed | AI Labs | LangChain, DeepMind, Google Research, Hugging Face, Mistral |
| API | Threads | Instagram Private API |
| API | X | GraphQL via `twitter-api-client` |
| API | LinkedIn | Voyager GraphQL |
| API | Reddit | JSON listing API |

## Install

```bash
pnpm install   # husky/commitlint git hooks
uv sync
uv run playwright install
brew install just
```

Python requires 3.12+. Tasks run through `just`; Python tooling uses `uv`.

## Usage

List platforms:

```bash
uv run skim platforms
```

Crawl recent sources:

```bash
uv run skim crawl all --days 1
uv run skim crawl all --days 1 --catch-up   # daily batch: also fills missed runs (up to 7 days)
uv run skim crawl hackernews --count 10
uv run skim crawl reddit --subreddit python --sort hot --count 10
```

Search the local post store:

```bash
uv run skim research "AI video" --days 7 --emit summary
uv run skim research "vector database" --sources hackernews,arxiv --emit json

# `--emit json` carries every body in full. Trim it before handing it to an agent.
uv run skim research "agents" --fields platform,title,url,timestamp
uv run skim research "agents" --max-chars 2000    # sets `truncated` on shortened posts
```

Inspect and package local data:

```bash
uv run skim doctor
uv run skim doctor --platform reddit
uv run skim doctor --strict            # exit 1 on any warning (for cron)
uv run skim backup --keep 3            # online backup + quick_check
uv run skim refresh-plan --days 1
uv run skim coverage --days 7 --emit json
uv run skim coverage --by-day --days 7   # new rows per platform per local day
uv run skim bundle "AI video" --days 7
uv run skim bundle --days 1 --group-by platform    # no topic: recent posts with bodies
```

Get posts out of SQLite:

```bash
uv run skim export ./exported --days 7                 # one Markdown file per post
uv run skim export ./exported --days 7 --format json
uv run skim source export --out sources.opml           # share or back up the source list
uv run skim source import sources.opml --platform blogs
```

An execution status of `success` means the crawl finished. Inspect the article-failure and partial-body counts in `skim doctor` as well: comments or a product tagline can be stored while the original article is unavailable. `source_empty` identifies a source that returned neither a body nor comments.

Repair reviewed rows through the maintenance commands. Start with `--dry-run`; recovery preserves existing comments and skips rows changed by another writer during extraction.

```bash
uv run python scripts/backfill_missing_content.py --ids 123 456 --include-failed --dry-run
uv run python scripts/backfill_missing_content.py --ids 123 456 --include-failed
uv run python scripts/backfill_geeknews_topics.py --ids 789 --wait-for-budget
```

The GeekNews command waits for the shared request budget. It consumes the same budget as scheduled crawls and backfills, so a recovery batch reduces the requests available to the next crawl until the window expires.

## Agent Skill

This repository includes an agent skill at `.agents/skills/skim/SKILL.md`; `.claude/skills/skim` is a relative link to it so Claude Code and other agents read the same copy. It helps an agent inspect Skim health, refresh sources, run local research, check coverage, triage crawler issues, and prepare source bundles under the system temp directory (`$TMPDIR/skim/...`) from a Skim checkout.

```bash
claude plugin validate .
python3 ~/.agents/skills/shared/skill-manager/scripts/quick_validate.py .agents/skills/skim
```

Login for session-based sources:

```bash
uv run skim login threads
uv run skim login reddit
printf '%s\n' "$PASSWORD" | uv run skim login threads --identifier user@example.com --password-stdin --save-credential
```

## Desktop App

```bash
swift run --package-path apps/desktop SkimDesktop
swift build --package-path apps/desktop
just install-desktop                 # install /Applications/Skim.app
scripts/build-app.sh ~/Applications  # install to a custom folder
```

The desktop app reads the same local workspace as the CLI. It can manage tracked sources and credentials, then browse `data/skim.db`.

## Data And Auth

- SQLite database: `data/skim.db`
- Session cookies: `data/sessions/*.json`
- Crawl output: `data/<platform>/*.json`
- Optional workspace override: `SKIM_WORKSPACE_ROOT`

Existing sessions are reused from `data/sessions/*.json`.

On macOS, credentials can be stored in Keychain. SQLite keeps only the Keychain reference (`platform_credentials.secret_service` / `secret_account`), and both the CLI and desktop app read the password from Keychain when login starts. To avoid shell history, prefer `--password-stdin` over `--password`.

## Development

```bash
just lint
just test     # Python pytest + Swift unit tests
just e2e      # desktop e2e smoke (fixture DB + real app boot)
just build    # desktop app
just dev      # run desktop app
just install-desktop
just format
```

Python-only checks:

```bash
uv run pytest tests -q
uv run ruff check --fix packages tests scripts
uv run ruff format packages tests scripts
uv run flake8
uv run pylint packages/skim-core/src/skim_core packages/skim-cli/src/skim_cli scripts
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

## Acknowledgements

[Horizon](https://github.com/Thysrael/Horizon)'s public-source configuration informed the addition of vLLM Blog, Sean Goedecke, SemiAnalysis, NVIDIA CUDA Blog, and the vLLM, SGLang, and Triton release feeds. These sources use Skim's existing RSS/Atom pipeline without an AI API key. See the [source inventory](docs/SOURCES.md) for registered feeds.

For new blog items, Skim skips an additional database row when the normalized article URL matches and the complete incoming body, apart from whitespace, is already present in a stored post. Empty or different bodies remain eligible for storage, and existing posts continue through the normal update path.

## License

MIT. See `LICENSE`.
