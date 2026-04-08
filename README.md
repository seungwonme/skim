# skim

멀티 플랫폼 정보 큐레이션 파이프라인 모노레포다. Python crawler/CLI, React/Tauri desktop app, Rust backend를 한 저장소에서 관리한다.

## Workspace Layout

```text
.
├── apps/
│   └── desktop/                # React + Vite + Tauri desktop app
├── packages/
│   ├── skim-cli/               # Typer CLI package
│   └── skim-core/              # crawler, DB, enrichment, feed config
├── tooling/
│   └── scripts/                # 운영/보조 스크립트
├── data/                       # local runtime artifacts
└── docs/
```

## Install

```bash
pnpm install
uv sync
uv run playwright install
cp .env.example .env
```

## Common Commands

### Root

```bash
pnpm lint
pnpm test
pnpm build
pnpm desktop:dev
```

### CLI

```bash
uv run skim platforms
uv run skim crawl all --days 1
uv run skim crawl hackernews --count 10
uv run skim crawl reddit --subreddit python --sort hot --count 10
uv run skim login threads
```

### Desktop

```bash
pnpm desktop:dev
pnpm desktop:build
```

## Supported Platforms

| 유형 | 플랫폼 | 소스 |
|------|--------|------|
| Feed | HackerNews | hnrss.org |
| Feed | GeekNews | news.hada.io Atom |
| Feed | YouTube | RSS + yt-dlp |
| Feed | ProductHunt | RSS |
| Feed | arXiv | Atom API |
| Feed | HuggingFace | Daily Papers JSON API |
| Feed | Every.to | Multi-feed RSS |
| API | Threads | Instagram Private API |
| API | X | GraphQL (`twitter-api-client`) |
| API | LinkedIn | Voyager GraphQL |
| API | Reddit | JSON listing API |

## Architecture

```text
uv run skim ...
  -> packages/skim-cli/src/skim_cli/cli.py
  -> packages/skim-core/src/skim_core/crawlers/*
  -> packages/skim-core/src/skim_core/db.py
  -> data/skim.db + data/<platform>/*.json

pnpm desktop:dev
  -> apps/desktop
  -> apps/desktop/src-tauri/src/lib.rs
  -> data/skim.db + data/sessions/*.json
```

### Data

- SQLite: `data/skim.db`
- Sessions: `data/sessions/*.json`
- Crawl output: `data/<platform>/*.json`

### Desktop Features

- tracked source management
- credential management with macOS Keychain integration
- session status inspection and login trigger
- post search/filter/detail view
- CSV / JSON / Markdown export
- YouTube source import from `skim_core.feed_config`

## Environment Variables

```bash
SKIM_WORKSPACE_ROOT=
GOOGLE_WEBAPP_URL=

THREADS_USERNAME=
THREADS_PASSWORD=
LINKEDIN_USERNAME=
LINKEDIN_PASSWORD=
X_USERNAME=
X_PASSWORD=
REDDIT_USERNAME=
REDDIT_PASSWORD=
```

## Automation

```bash
0 9 * * * /path/to/skim/tooling/scripts/run_daily_feed.sh
```

## Quality Gates

- JS/TS workspace tasks: `pnpm` + `turbo`
- Python workspace tasks: `uv`
- Git hooks: `husky`
- Commit message validation: `commitlint`

## License

MIT
