# skim

<p align="center"><a href="README.md">English</a> | <b>한국어</b></p>

<p align="center">
  <img src="images/skim-readme-banner.png" alt="Skim 로컬 우선 정보 큐레이션 파이프라인 배너" width="100%">
</p>

![status: local-first crawler](https://img.shields.io/badge/status-local--first%20crawler-blue)
![last commit](https://img.shields.io/github/last-commit/seungwonme/skim)
![license: MIT](https://img.shields.io/badge/license-MIT-green)
![python: 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

> 피드, 소셜 타임라인, 논문, 영상, AI lab 업데이트를 로컬에서 수집하고 검색하는 정보 큐레이션 파이프라인.

Skim은 여러 public feed와 세션 기반 social source에서 post를 수집해 로컬 SQLite에 저장하고, Python CLI와 Swift macOS desktop app으로 탐색할 수 있게 합니다.

> [!WARNING]
> 일부 API crawler는 로그인된 browser session과 비공식 web endpoint를 사용합니다. 본인 계정으로 사용하고, 각 플랫폼의 약관과 rate limit을 지켜야 하며, upstream page/API 변경으로 동작이 깨질 수 있습니다.

## 구성

| 경로 | 역할 |
|---|---|
| `packages/skim-core/` | crawler, model, enrichment, SQLite 저장, research/search |
| `packages/skim-cli/` | `uv run skim ...`로 실행하는 Typer CLI |
| `apps/swift-desktop/` | SwiftUI macOS desktop app |
| `scripts/` | import, cron, maintenance script |
| `images/` | README와 project image |
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
printf '%s\n' "$PASSWORD" | uv run skim login threads --identifier user@example.com --password-stdin --save-credential
```

## Desktop App

```bash
swift run --package-path apps/swift-desktop SkimDesktop
swift build --package-path apps/swift-desktop
```

Desktop app은 CLI와 같은 로컬 workspace를 읽습니다. tracked source와 credential을 관리하고, `data/skim.db`를 탐색합니다.

## 데이터와 인증

- SQLite database: `data/skim.db`
- Session cookies: `data/sessions/*.json`
- Crawl output: `data/<platform>/*.json`
- Optional workspace override: `SKIM_WORKSPACE_ROOT`

이미 저장된 session은 `data/sessions/*.json`에서 재사용합니다.

macOS에서는 credential을 Keychain에 저장할 수 있습니다. SQLite에는 Keychain reference(`platform_credentials.secret_service` / `secret_account`)만 남기고, CLI와 desktop app은 login 시작 시 Keychain에서 password를 읽습니다. shell history에 password가 남지 않게 `--password`보다 `--password-stdin` 사용을 권장합니다.

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
uv run black packages tests scripts --config pyproject.toml
uv run isort packages tests scripts --settings-path pyproject.toml
uv run flake8
uv run pylint packages/skim-core/src/skim_core packages/skim-cli/src/skim_cli scripts
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
