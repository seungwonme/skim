# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 의존성 설치
pnpm install
uv sync
uv run playwright install

# 루트 품질 게이트
pnpm lint
pnpm test
pnpm build

# Python 개별 도구
uv run pytest tests -v
uv run black . --config pyproject.toml
uv run isort . --settings-path pyproject.toml
uv run flake8
uv run pylint packages/skim-core/src/skim_core packages/skim-cli/src/skim_cli

# 크롤링
uv run skim crawl hackernews --count 10
uv run skim crawl all --days 1
uv run skim crawl hackernews geeknews --days 1 --no-content
uv run skim crawl reddit --count 10
uv run skim crawl reddit --subreddit python --sort hot --count 10

# 기타
uv run skim platforms           # 지원 플랫폼 목록
uv run skim login threads       # CDP 로그인
uv run skim login reddit        # Reddit 로그인 세션 저장

# Desktop
pnpm desktop:dev
pnpm desktop:build
```

## Architecture

### Monorepo Layout

```text
.
├── apps/
│   └── desktop/                         # React + Vite + Tauri app
├── packages/
│   ├── skim-cli/src/skim_cli/           # Typer CLI
│   └── skim-core/src/skim_core/         # crawler, DB, enrichment, feed config
├── tooling/scripts/                     # import/cron/helper scripts
├── tests/                               # Python regression tests
└── data/                                # local runtime artifacts
```

### Pipeline Flow

```text
CLI (uv run skim ...) → skim_cli.cli → skim_core.crawlers.REGISTRY lookup
                                          ↓
                              crawler.crawl(**options) → List[Post]
                                          ↓
                            enrichment (defuddle / yt-dlp)
                                          ↓
                       SQLite 저장 + JSON 파일 + (Google Sheets)
```

### Crawler 유형과 패턴

모든 크롤러는 `packages/skim-core/src/skim_core/crawlers/base.py`의 `Crawler` Protocol을 구현하고, `packages/skim-core/src/skim_core/crawlers/__init__.py`의 `REGISTRY`에 등록된다.

| 유형 | 위치 | 옵션 기준 | 플랫폼 |
|------|------|-----------|--------|
| Feed | `packages/skim-core/src/skim_core/crawlers/feed/` | `since` | hackernews, geeknews, youtube, producthunt, arxiv, huggingface, everyto |
| API | `packages/skim-core/src/skim_core/crawlers/api/` | `count` | threads, x, linkedin, reddit |
| Browser | `packages/skim-core/src/skim_core/crawlers/browser/` | `count` | reddit legacy 구현 |

- Feed 크롤러: `since` 유무에 따라 RSS/API 모드 자동 전환
- API 크롤러: `data/sessions/{platform}_session.json` 세션 쿠키 재사용
- Reddit API 크롤러: subreddit listing은 verification challenge 해제 후 JSON endpoint 호출, 홈 피드는 로그인 세션 기반 `best.json` 호출
- Browser 크롤러: `BrowserCrawler`가 Playwright lifecycle과 debug dump를 관리

### 주요 모듈

- `packages/skim-cli/src/skim_cli/cli.py`: Typer CLI 엔트리포인트
- `packages/skim-core/src/skim_core/models.py`: `Post` Pydantic 모델
- `packages/skim-core/src/skim_core/db.py`: SQLite WAL 모드, `UNIQUE(platform, external_id)` 중복 제거
- `packages/skim-core/src/skim_core/enrichment.py`: `bunx defuddle`, `yt-dlp`, transcript 정리
- `packages/skim-core/src/skim_core/feed_utils.py`: RSS/Atom 파싱, KST 변환
- `packages/skim-core/src/skim_core/feed_config.py`: RSS URL, YouTube 채널 ID, API endpoint 설정
- `apps/desktop/src-tauri/src/lib.rs`: desktop backend, workspace/data/session path 해석, import/login bridge

### 새 크롤러 추가 방법

1. `packages/skim-core/src/skim_core/crawlers/{type}/` 아래에 크롤러 클래스 생성 (`async crawl(**options) -> List[Post]`)
2. `packages/skim-core/src/skim_core/crawlers/__init__.py`의 `REGISTRY`에 등록
3. Feed 크롤러면 `packages/skim-core/src/skim_core/feed_config.py`에 소스 추가

## Git Convention

- 브랜치: `type/[branch/]description[-#issue]` (GitFlow)
- 커밋: `<type>(<scope>): <subject>` (Conventional Commits)
- type: feat, fix, docs, style, refactor, test, chore

## Environment Variables

`.env` 파일에 설정:

- `SKIM_WORKSPACE_ROOT` (optional override)
- `THREADS_USERNAME`, `THREADS_PASSWORD`
- `LINKEDIN_USERNAME`, `LINKEDIN_PASSWORD`
- `X_USERNAME`, `X_PASSWORD`
- `REDDIT_USERNAME`, `REDDIT_PASSWORD`
- `GOOGLE_WEBAPP_URL`

## Tooling

- JS/TS: `pnpm` workspace + `turbo` + `biome`
- Python: `uv` workspace
- Rust/Tauri: root `Cargo.toml` workspace + `apps/desktop/src-tauri`
- Git hooks: `husky`
- Commit message validation: `commitlint`

## Project Notes

- 2026-04-08: 저장소를 표준 모노레포로 재구성했다. `desktop/`은 `apps/desktop/`, 루트 `src/`는 `packages/skim-core/src/skim_core/`, 루트 `main.py`는 `packages/skim-cli/src/skim_cli/cli.py`, `scripts/`는 `tooling/scripts/`로 이동했다.
- 2026-04-08: 루트 `pyproject.toml`은 `uv` workspace root로 전환했고, Python 코드는 `skim-core`와 `skim-cli` 패키지로 분리했다. 새 CLI 진입점은 `uv run skim ...`이다.
- 2026-04-08: 루트 JS tooling은 `pnpm workspace + turbo + biome + husky + commitlint` 기준으로 재구성했다.
- 2026-04-08: 루트 `lint`/`format` 스크립트는 monorepo 소스 범위(`packages`, `tests`, `tooling/scripts`)만 검사하도록 좁혔고, `.venv`/`node_modules`/`target` 같은 외부 산출물은 Python lint 대상에서 제외했다.
- 2026-04-08: desktop Tauri backend는 새 모노레포 경로 기준으로 workspace root를 다시 계산하고, 로그인/소스 import 명령도 새 CLI 및 `tooling/scripts/` 경로를 사용하도록 맞췄다.
- 2026-04-08: desktop Tauri backend의 `ensure_database()`는 이제 `posts/summaries/feedback/runs`까지 포함한 전체 SQLite schema를 초기화한다. 덕분에 새 worktree처럼 `data/skim.db`가 비어 있는 환경에서도 앱 overview와 Explorer가 `no such table: posts` 없이 기동된다. 회귀는 `cargo test -p desktop`의 fresh workspace 테스트 2건으로 고정했다.
- 2026-04-08: desktop frontend의 Tauri bridge 모듈 `apps/desktop/src/lib/api.ts`, `apps/desktop/src/lib/types.ts`는 필수 tracked 파일이다. merge 후 이 파일들이 빠지면 Vite가 `./lib/api` import를 해석하지 못해 앱이 즉시 깨진다.
- 2026-04-08: 루트 `.gitignore`의 일반 `lib/` 패턴이 `apps/desktop/src/lib/`까지 무시하지 않도록 예외(`!apps/desktop/src/lib/**`)를 유지한다.
- 2026-04-08: `threads`, `x`, `linkedin` API 크롤러는 `external_id`를 실제 플랫폼 식별자로 저장하고 `timestamp`를 ISO 8601으로 기록한다. `tests/test_social_api_metadata.py`로 회귀를 고정했다.
- 2026-04-08: 저장된 credential 기반 자동 로그인은 `SKIM_LOGIN_IDENTIFIER`, `SKIM_LOGIN_PASSWORD` 환경변수를 통해 Python 로그인 프로세스에 전달되고, `packages/skim-core/src/skim_core/crawlers/auth/cdp.py`가 이를 사용해 auto-fill을 시도한다.
- 2026-04-08: `dry-run` 모드는 제거된 상태다. `crawl`은 항상 DB 초기화, `runs` 기록, `posts` 저장, JSON 파일 저장을 수행하며, `tests/test_main_crawl_persistence.py`로 회귀를 고정했다.
- 2026-04-08: Reddit는 `packages/skim-core/src/skim_core/crawlers/api/reddit.py` 기반 순수 HTTP/API 크롤러로 동작한다. `crawl reddit --subreddit <slug> --sort hot|new`와 홈 피드 수집 모두 현재 구조에서 유지한다.
