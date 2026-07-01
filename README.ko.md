# skim

<p align="center"><a href="README.md">English</a> | <b>한국어</b></p>

![status: local-first crawler](https://img.shields.io/badge/status-local--first%20crawler-blue)
![last commit](https://img.shields.io/github/last-commit/seungwonme/skim)
![license: MIT](https://img.shields.io/badge/license-MIT-green)
![python: 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

> 피드, 소셜 타임라인, 논문, 영상, AI lab 업데이트를 로컬에서 수집하고 검색하는 정보 큐레이션 파이프라인.

Skim은 여러 public feed와 세션 기반 social source에서 post를 수집해 로컬 SQLite에 저장하고, Python CLI와 macOS desktop app으로 탐색할 수 있게 합니다.

> [!WARNING]
> 일부 API crawler는 로그인된 browser session과 비공식 web endpoint를 사용합니다. 본인 계정으로 사용하고, 각 플랫폼의 약관과 rate limit을 지켜야 하며, upstream page/API 변경으로 동작이 깨질 수 있습니다.

## 구성

| 경로 | 역할 |
|---|---|
| `packages/skim-core/` | crawler, model, enrichment, SQLite 저장, research/search |
| `packages/skim-cli/` | `uv run skim ...`로 실행하는 Typer CLI |
| `apps/desktop/` | React + Vite + Tauri desktop app |
| `tooling/scripts/` | import, cron, maintenance script |
| `docs/` | design note, crawler note, source backlog, generated implementation plan |
| `data/` | 로컬 실행 산출물: SQLite DB, session file, crawl JSON output |

## 지원 소스

| 유형 | 플랫폼 | 소스 |
|---|---|---|
| Feed | Hacker News | hnrss.org |
| Feed | GeekNews | news.hada.io Atom |
| Feed | YouTube | RSS + `yt-dlp` |
| Feed | Product Hunt | RSS |
| Feed | arXiv | Atom API |
| Feed | Hugging Face | Daily Papers JSON API |
| Feed | Every.to | RSS feeds |
| Feed | Blogs | `PERSONAL_BLOGS`의 RSS feeds |
| Feed | AI Labs | OpenAI RSS, Anthropic pages, LangChain blog |
| API | Threads | Instagram Private API |
| API | X | `twitter-api-client` 기반 GraphQL |
| API | LinkedIn | Voyager GraphQL |
| API | Reddit | JSON listing API |

## 설치

```bash
pnpm install
uv sync
uv run playwright install
cp .env.example .env
```

Python 3.12 이상이 필요합니다. Node tooling은 `pnpm`과 `turbo`, Python tooling은 `uv`를 사용합니다.

## 사용법

지원 플랫폼 확인:

```bash
uv run skim platforms
```

최근 소스 크롤링:

```bash
uv run skim crawl all --days 1
uv run skim crawl hackernews --count 10
uv run skim crawl reddit --subreddit python --sort hot --count 10
```

로컬 post 저장소 검색:

```bash
uv run skim research "AI video" --days 7 --emit summary
uv run skim research "vector database" --sources hackernews,arxiv --emit json
```

세션 기반 소스 로그인:

```bash
uv run skim login threads
uv run skim login reddit
```

## Desktop App

```bash
pnpm desktop:dev
pnpm desktop:build
```

Desktop app은 CLI와 같은 로컬 workspace를 읽습니다. session status 확인, login trigger, tracked source 관리, `data/skim.db` 탐색을 지원합니다.

macOS DMG build는 Tauri가 `target/release/bundle/dmg/` 아래에 생성합니다.

## 데이터와 인증

- SQLite database: `data/skim.db`
- Session cookies: `data/sessions/*.json`
- Crawl output: `data/<platform>/*.json`
- Optional workspace override: `SKIM_WORKSPACE_ROOT`

`.env`는 CLI login 보조용입니다. 이미 저장된 session은 `data/sessions/*.json`에서 재사용합니다.

CLI가 macOS Keychain을 직접 읽지는 않습니다. Desktop app이 password를 macOS Keychain에 저장하고, SQLite에는 Keychain reference만 남깁니다. Desktop에서 login을 실행하면 Tauri가 Keychain에서 password를 읽어 `SKIM_LOGIN_IDENTIFIER` / `SKIM_LOGIN_PASSWORD`를 `uv run skim login <platform>` process에 넘깁니다.

## 환경 변수

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

## 개발

```bash
pnpm lint
pnpm test
pnpm build
pnpm typecheck
```

Python 전용 check:

```bash
uv run pytest tests -q
uv run black packages tests tooling/scripts --config pyproject.toml
uv run isort packages tests tooling/scripts --settings-path pyproject.toml
uv run flake8
uv run pylint packages/skim-core/src/skim_core packages/skim-cli/src/skim_cli tooling/scripts
```

## 문서

- `AGENTS.md` - AI 작업 가이드와 repository convention
- `docs/TODO.ko.md` - source backlog와 promotion checklist ([English](docs/TODO.md))
- `docs/THREADS.md` - Threads crawler 구현 노트
- `docs/DESIGN.md` - historical design inspiration note, 현재 제품 spec 아님

## 범위 밖

Skim은 hosted crawler service, data resale product, platform access control 우회 도구가 아닙니다. 본인 소유 session과 public feed를 이용한 로컬 개인 큐레이션 용도로 설계되었습니다.

## 기여

작은 수정 PR을 환영합니다. 특히 아래 영역이 좋습니다.

- 깨진 feed/API adapter
- timestamp 또는 metadata normalization 버그
- crawler, DB, research behavior에 대한 focused test
- 현재 CLI 동작과 맞지 않는 문서 수정

PR을 열기 전에는 변경 범위에 맞는 가장 작은 test부터 실행하고, shared behavior를 건드렸다면 root gate까지 통과시켜 주세요.

## 라이선스

MIT. `LICENSE`를 참고하세요.
