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
                       SQLite 저장 + JSON 파일
```

### Crawler 유형과 패턴

모든 크롤러는 `packages/skim-core/src/skim_core/crawlers/base.py`의 `Crawler` Protocol을 구현하고, `packages/skim-core/src/skim_core/crawlers/__init__.py`의 `REGISTRY`에 등록된다.

| 유형 | 위치 | 옵션 기준 | 플랫폼 |
|------|------|-----------|--------|
| Feed | `packages/skim-core/src/skim_core/crawlers/feed/` | `since` | hackernews, geeknews, youtube, producthunt, arxiv, huggingface, everyto, blogs, ailabs |
| API | `packages/skim-core/src/skim_core/crawlers/api/` | `count` | threads, x, linkedin, reddit |

- Feed 크롤러: `since` 유무에 따라 RSS/API 모드 자동 전환
- API 크롤러: `data/sessions/{platform}_session.json` 세션 쿠키 재사용
- Reddit API 크롤러: subreddit listing은 verification challenge 해제 후 JSON endpoint 호출, 홈 피드는 로그인 세션 기반 `best.json` 호출

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

## Tooling

- JS/TS: `pnpm` workspace + `turbo` + `biome`
- Python: `uv` workspace
- Rust/Tauri: root `Cargo.toml` workspace + `apps/desktop/src-tauri`
- Git hooks: `husky`
- Commit message validation: `commitlint`

## Project Notes

- 2026-04-20: 개인 블로그/AI 빅테크 크롤러 추가.
  - `blogs` 플랫폼: `PERSONAL_BLOGS` dict + `BlogsCrawler`로 RSS 기반 멀티 피드 수집 (Addy Osmani, Phil Schmid, Tidy First 등). 소스 추가는 `feed_config.py`의 `PERSONAL_BLOGS`에 한 줄 append.
  - `ailabs` 플랫폼: `AI_LABS_SOURCES` 리스트 + `AILabsCrawler`로 RSS/HTML 혼합 수집. OpenAI(RSS), Anthropic News/Research/Engineering(HTML 스크래핑), LangChain Blog(HTML 스크래핑) 지원. `type` 키로 어댑터 분기.
  - 정확도 체인 (ailabs 전용): article meta HTTP → Playwright 렌더 → 사이트맵 `<lastmod>` 순으로 `_fetch_article_metadata`가 `published`를 채운다. `_resolve_entry_datetime`은 (1) article ISO published_time → (2) anchor 텍스트 날짜 → (3) sitemap lastmod 순서로 신뢰도를 매긴다. sitemap lastmod은 수정 시각일 수 있어 anchor 날짜보다 후순위. `_fetch_article_metadata`/`_fetch_sitemap_lastmod_map`에 `lru_cache` 적용.
  - **asyncio 루프와 sync_playwright 충돌**: CLI가 `asyncio.run(crawler.crawl(...))`로 async 컨텍스트에서 호출하는데, `_fetch_html_rendered()`가 `sync_playwright`를 쓰면 "Sync API inside asyncio loop" 예외 발생. `AILabsCrawler.crawl()`은 실제 작업을 `_crawl_sync`로 분리해 `asyncio.to_thread`로 워커 스레드에서 실행한다.
  - Anthropic 인덱스 파서는 동일 URL이 Featured(날짜 없음)와 PublicationList(날짜 있음)에 중복 렌더링되므로 "URL별로 첫 등장 anchor로 시작하되 dated anchor가 나오면 anchor_date 채움" 정책을 쓴다. `collected: Dict[str, Dict]` 구조.
  - enrichment 강화 (`enrichment.py`): `extract_article_content(url, title)` 래퍼가 HTTP fetch → trafilatura → 품질 게이트(`_is_content_usable`, word_count ≥ 150) → 얇으면 Playwright 렌더 + trafilatura → 마지막 fallback으로 defuddle. Playwright `wait_until="load" + 1.5s 대기` (OpenAI Cloudflare networkidle 타임아웃 회피).
  - **defuddle이 Anthropic Next.js 페이지에서 Node fetch 내부 hang을 일으켜 수분 블로킹** → trafilatura (Python 네이티브)를 기본 경로로 채택. ailabs/blogs 플랫폼은 trafilatura로 먼저 시도. 이는 subprocess 없이 처리되어 안정적.
  - `_item_to_post` (ailabs.py, blogs.py)는 `enrichment_method`/`enrichment_error`/`description`/`image`/`original_url` 같은 extras를 whitelist로 전달. 빠지면 Post.extra="allow"여도 저장 안 됨.
  - 품질 게이트를 통과 못하면 `content_markdown`을 비우고 `extra.enrichment_method="failed"` 마커를 심는다. `db.py`의 upsert(`save_posts`)는 YouTube의 `subtitle_lang=summary`와 동일하게 이 마커를 "재시도 가능"으로 해석해 다음 크롤링에서 더 좋은 본문이 오면 덮어쓴다. 회귀 고정: `tests/test_ailabs_enrichment_retry.py` (실패→성공 overwrite, 성공→실패 유지 2케이스).
  - RSS summary는 `Post.summary`에 별도 저장되므로 digest 파이프라인은 `content_markdown`이 비었을 때 `summary`로 폴백 가능. OpenAI는 Cloudflare bot 검증으로 `enrichment_method="failed"`가 많이 나오는 게 정상.
  - `external_id`는 URL에서 파싱 (`{netloc}{path}`). 이전에는 누락되어 DB `ON CONFLICT(platform, external_id)`가 title hash fallback으로 퇴행했음. URL 기준 크로스-소스 dedup도 `_dedupe_by_url`로 추가.
  - `requests.Session + urllib3 Retry (backoff 0.8, status 429/5xx)` 재사용. HTML/sitemap/article 페이지 fetch 모두 한 세션.
- 2026-04-09: `runs` 테이블에 `current_platform`, `runner_pid`, `runner_host`를 추가하고, 새 crawl 시작 시 죽은 PID의 stale `running` run을 `interrupted`로 자동 정리하도록 보강했다. `crawl`은 플랫폼 시작/완료를 `runs.summary`와 `current_platform`에 계속 기록한다.
- 2026-04-09: `save_posts()`는 동일 `(platform, external_id)` 충돌 시 비어 있던 `content_markdown`/`word_count`/기본 메타데이터를 보강하도록 변경했다. YouTube에서 `subtitle_lang="summary"` fallback으로 저장된 행은 나중에 실제 transcript가 오면 덮어쓸 수 있게 처리했다.
- 2026-04-09: YouTube 자막 추출은 `yt-dlp --list-subs` 결과의 실제 자막 코드(`ko-...`, `en-US-...`)를 골라 요청하도록 수정했고, 자막이 끝내 없으면 `summary`를 fallback 본문으로 저장해 digest 전수 분석에서 누락되지 않게 했다.
- 2026-04-08: 저장소를 표준 모노레포로 재구성했다. `desktop/`은 `apps/desktop/`, 루트 `src/`는 `packages/skim-core/src/skim_core/`, 루트 `main.py`는 `packages/skim-cli/src/skim_cli/cli.py`, `scripts/`는 `tooling/scripts/`로 이동했다.
- 2026-04-08: 루트 `pyproject.toml`은 `uv` workspace root로 전환했고, Python 코드는 `skim-core`와 `skim-cli` 패키지로 분리했다. 새 CLI 진입점은 `uv run skim ...`이다.
- 2026-04-08: 루트 JS tooling은 `pnpm workspace + turbo + biome + husky + commitlint` 기준으로 재구성했다.
- 2026-04-08: 루트 `lint`/`format` 스크립트는 monorepo 소스 범위(`packages`, `tests`, `tooling/scripts`)만 검사하도록 좁혔고, `.venv`/`node_modules`/`target` 같은 외부 산출물은 Python lint 대상에서 제외했다.
- 2026-04-08: desktop Tauri backend는 새 모노레포 경로 기준으로 workspace root를 다시 계산하고, 로그인/소스 import 명령도 새 CLI 및 `tooling/scripts/` 경로를 사용하도록 맞췄다.
- 2026-04-08: desktop Tauri backend의 `ensure_database()`는 이제 `posts/summaries/feedback/runs`까지 포함한 전체 SQLite schema를 초기화한다. 덕분에 새 worktree처럼 `data/skim.db`가 비어 있는 환경에서도 앱 overview와 Explorer가 `no such table: posts` 없이 기동된다. 회귀는 `cargo test -p desktop`의 fresh workspace 테스트 2건으로 고정했다.
- 2026-04-08: desktop frontend의 Tauri bridge 모듈 `apps/desktop/src/lib/api.ts`, `apps/desktop/src/lib/types.ts`는 필수 tracked 파일이다. merge 후 이 파일들이 빠지면 Vite가 `./lib/api` import를 해석하지 못해 앱이 즉시 깨진다.
- 2026-04-08: 루트 `.gitignore`의 일반 `lib/` 패턴이 `apps/desktop/src/lib/`까지 무시하지 않도록 예외(`!apps/desktop/src/lib/**`)를 유지한다.
- 2026-04-08: 첫 GitHub desktop 릴리즈 기준 버전은 `v0.2.0`으로 맞췄다. README에 DMG 릴리즈/설치 흐름을 추가했고, workspace Python 패키지 및 desktop app/Tauri 버전도 모두 `0.2.0`으로 통일했다.
- 2026-04-08: `threads`, `x`, `linkedin` API 크롤러는 `external_id`를 실제 플랫폼 식별자로 저장하고 `timestamp`를 ISO 8601으로 기록한다. `tests/test_social_api_metadata.py`로 회귀를 고정했다.
- 2026-04-08: 저장된 credential 기반 자동 로그인은 `SKIM_LOGIN_IDENTIFIER`, `SKIM_LOGIN_PASSWORD` 환경변수를 통해 Python 로그인 프로세스에 전달되고, `packages/skim-core/src/skim_core/crawlers/auth/cdp.py`가 이를 사용해 auto-fill을 시도한다.
- 2026-04-08: `dry-run` 모드는 제거된 상태다. `crawl`은 항상 DB 초기화, `runs` 기록, `posts` 저장, JSON 파일 저장을 수행하며, `tests/test_main_crawl_persistence.py`로 회귀를 고정했다.
- 2026-04-08: Reddit는 `packages/skim-core/src/skim_core/crawlers/api/reddit.py` 기반 순수 HTTP/API 크롤러로 동작한다. `crawl reddit --subreddit <slug> --sort hot|new`와 홈 피드 수집 모두 현재 구조에서 유지한다.
