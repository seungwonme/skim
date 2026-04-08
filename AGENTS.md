# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 의존성 설치
uv sync
uv run playwright install

# 린트/포맷 (pre-commit이 커밋 시 자동 실행)
pre-commit run --all-files

# 개별 도구
black . --config pyproject.toml
isort . --settings-path pyproject.toml
flake8
pylint src/

# 크롤링
python main.py crawl hackernews --count 10
python main.py crawl all --days 1
python main.py crawl hackernews geeknews --days 1 --no-content
python main.py crawl all --days 1
python main.py crawl reddit --count 10
python main.py crawl reddit --subreddit python --sort hot --count 10

# 기타
python main.py platforms          # 지원 플랫폼 목록
python main.py login threads      # CDP 로그인
python main.py login reddit       # Reddit 로그인 세션 저장
```

## Architecture

### Pipeline Flow
```
CLI (main.py) → REGISTRY lookup → crawler.crawl(**options) → List[Post]
                                                                  ↓
                                              enrichment (defuddle / yt-dlp)
                                                                  ↓
                                              SQLite 저장 + JSON 파일 + (Google Sheets)
```

### Crawler 유형과 패턴
모든 크롤러는 `Crawler` Protocol (`src/crawlers/base.py`)을 구현하고, `REGISTRY` dict (`src/crawlers/__init__.py`)에 등록된다.

| 유형 | 위치 | 옵션 기준 | 플랫폼 |
|------|------|-----------|--------|
| Feed | `src/crawlers/feed/` | `since` (날짜) | hackernews, geeknews, youtube, producthunt, arxiv, huggingface, everyto |
| API | `src/crawlers/api/` | `count` (개수) | threads, x, linkedin, reddit |
| Browser | `src/crawlers/browser/` | `count` | reddit legacy 구현 (현재 미사용) |

- Feed 크롤러: `since` 유무에 따라 RSS/API 모드 자동 전환
- API 크롤러: CDP로 추출한 세션 쿠키 사용 (`data/sessions/{platform}_session.json`)
- Reddit API 크롤러: 서브레딧 listing은 verification challenge를 풀어 비로그인 JSON endpoint를 호출하고, 홈 피드는 저장된 `reddit_session.json` 세션으로 `best.json` listing을 호출한다.
- Browser 크롤러: `BrowserCrawler` ABC가 Playwright 라이프사이클 관리

### 주요 모듈
- **src/models.py**: `Post` Pydantic 모델. `extra = "allow"`로 플랫폼별 추가 필드 허용
- **src/db.py**: SQLite WAL 모드. `UNIQUE(platform, external_id)`로 중복 제거. 테이블: posts, summaries, feedback, runs
- **src/enrichment.py**: `defuddle` (bunx CLI)로 URL 본문 추출, `yt-dlp`로 YouTube 자막 추출
- **src/feed_utils.py**: `fetch_feed()` — feedparser 기반 RSS/Atom 파싱, KST 변환
- **feed_config.py**: RSS URL, YouTube 채널 ID, API 엔드포인트 설정

### 새 크롤러 추가 방법
1. `src/crawlers/{type}/` 아래에 크롤러 클래스 생성 (`async crawl(**options) -> List[Post]`)
2. `src/crawlers/__init__.py`의 `REGISTRY`에 등록
3. Feed 크롤러는 `feed_config.py`에 URL 추가

## Git Convention
- 브랜치: `type/[branch/]description[-#issue]` (GitFlow)
- 커밋: `<type>(<scope>): <subject>` (Conventional Commits)
- type: feat, fix, docs, style, refactor, test, chore

## Environment Variables
`.env` 파일에 설정:
- `THREADS_USERNAME`, `THREADS_PASSWORD`
- `LINKEDIN_USERNAME`, `LINKEDIN_PASSWORD`
- `X_USERNAME`, `X_PASSWORD`
- `REDDIT_USERNAME`, `REDDIT_PASSWORD`
- `GOOGLE_WEBAPP_URL` (Google Sheets export)

## Project Notes
- 2026-04-08: `threads`, `x`, `linkedin` API 크롤러가 이제 `external_id`를 실제 플랫폼 식별자로 저장하고 `timestamp`를 ISO 8601으로 기록한다. `tests/test_social_api_metadata.py`로 회귀를 고정했고, 기존 `data/skim.db`의 해당 플랫폼 레코드도 일괄 마이그레이션해 `external_id`/timestamp를 정규화하고 중복 8건을 제거했다.
- 2026-03-24: `TODO.md`에 링크드인 구독 후보 프로필 2개(`kjh941213`, `gb-jeong`)를 추가했다.
- 2026-04-08: `desktop/`에 `Tauri + React + TypeScript` 기반 macOS 우선 데스크톱 GUI를 추가했다. GUI 범위는 `Sources`, `Credentials`, `Explorer`이며, SQLite 설정 테이블 `tracked_sources`, `platform_credentials`, `app_settings`와 Python import 스크립트 `scripts/import_feed_config.py`를 도입했다.
- 2026-04-08: Explorer 기본 조회 제한을 제거해 전체 매칭 결과를 보여주도록 바꿨고, `react-icons/lu` 기반 아이콘을 탭/헤더/버튼에 적용해 UI를 단순화했다. 또한 `uv run python scripts/import_feed_config.py`를 실행해 기존 YouTube 소스 16개를 `tracked_sources`에 반입했다.
- 2026-04-08: Sources 탭의 전체 테이블 보기를 제거하고, `6개 미리보기 + 더보기` 카드 레이아웃으로 단순화했다.
- 2026-04-08: `docs/DESIGN.md`를 기준으로 데스크톱 UI를 warm parchment + serif hierarchy + terracotta accent + ring-shadow 중심의 Claude 계열 디자인 언어로 재정렬했다. 사이드바는 dark section, 메인 패널은 ivory/light cards, Explorer detail은 dark card로 구성했다.
- 2026-04-08: 데스크톱 좌측 사이드바를 viewport 기준 sticky rail로 바꾸고, `desktop/src/App.css`의 shell/panel/card/button/input 스타일을 `docs/DESIGN.md`의 warm parchment, serif hierarchy, generous radius, ring-shadow 규칙에 더 가깝게 재정렬했다. Explorer detail 패널도 동일 톤으로 sticky 처리해 긴 결과 목록에서도 메타데이터와 본문을 계속 볼 수 있게 맞췄다.
- 2026-04-08: Explorer 결과 리스트도 전체 펼침을 제거하고 `10개 미리보기 + 더보기` 흐름으로 맞췄다.
- 2026-04-08: Explorer는 빈 검색에서 `posts` 전체를 읽지 않도록 Tauri backend 기본 조회 제한을 25개(최대 200개)로 두고, 프런트는 25개씩 추가 조회하는 방식으로 바꿨다. Export는 현재 로드된 결과 또는 선택 항목 기준으로 안내한다.
- 2026-04-08: Explorer 필터는 draft/applied 상태를 분리해 입력 즉시 재조회하지 않고 `적용` 버튼으로만 반영되게 바꿨다. `더 불러오기` 버튼도 스크롤 영역 바깥 고정이 아니라 결과 리스트의 마지막 항목으로 옮겨 끝까지 스크롤했을 때 보이도록 조정했다.
- 2026-04-08: Explorer `search_posts` 응답에 `totalCount`를 추가해 제한 조회 중에도 전체 필터 매칭 개수를 알 수 있게 했다. 결과 헤더와 상태 문구는 이제 `총 N개 중 현재 M개 로드` 기준으로 표시한다.
- 2026-04-08: Explorer 결과 헤더에서 `25개씩 추가 조회` 안내 문구를 제거하고, 핵심 상태인 `총 N개 중 현재 M개 로드`만 남겨 밀도를 낮췄다.
- 2026-04-08: `desktop`의 `CredentialsPanel`, `ExplorerPanel`, `SourcesPanel` 입력 핸들러에서 `setState` 업데이터 내부로 `event.currentTarget`을 넘기던 패턴을 제거하고, 값을 먼저 캡처한 뒤 상태를 갱신하도록 수정해 React 이벤트 해제 이후 `currentTarget`이 `null`이 되는 크래시를 막았다.
- 2026-04-08: 저장된 credential 기반 자동 로그인 구현을 위해 `tests/test_cdp_autofill.py`를 추가해 CDP 로그인 모듈의 환경변수 credential 로딩과 자동 입력 호출 계약을 먼저 테스트로 고정했다.
- 2026-04-08: Credentials 로그인 버튼이 `credential.id` 기준으로 동작하도록 바꾸고, Tauri backend가 DB에서 해당 credential 메타데이터를 읽은 뒤 macOS Keychain에서 비밀번호를 조회해 `SKIM_LOGIN_IDENTIFIER`, `SKIM_LOGIN_PASSWORD` 환경변수로 Python 로그인 프로세스에 전달하도록 수정했다. `src/crawlers/auth/cdp.py`는 이 값을 사용해 Threads/X/LinkedIn 로그인 폼 자동 입력을 먼저 시도하고, 실패하면 기존 수동 로그인 흐름으로 fallback한다.
- 2026-04-08: `tests/test_cdp_autofill.py`의 assertion을 CDP expression escape 형태에 맞게 보정했고, 파일 인코딩 검증이 `utf-8`로 잡히도록 한국어 모듈 docstring을 추가했다.
- 2026-04-08: Threads 로그인 버튼 자동 클릭이 약한 문제를 줄이기 위해 CDP 자동 입력 스크립트를 async 평가로 바꾸고, 입력 직후 180ms 대기 후 버튼을 다시 찾도록 수정했다. 제출은 click만 쓰지 않고 pointer/mouse 이벤트, `form.requestSubmit()`, Enter key fallback까지 순서대로 시도한다.
- 2026-04-08: Credentials의 `lastVerifiedAt`은 Tauri backend에서 `Asia/Seoul` 기준 `YYYY-MM-DD HH:MM:SS KST` 문자열로 내려주도록 바꿨다. 또한 credential 삭제 시 기존 Keychain 삭제에 더해 `session_path` 파일도 함께 제거해 세션 로그아웃 효과가 나도록 수정했고, 해당 helper 동작은 `cargo test`용 unit test로 고정했다.
- 2026-04-08: `dry-run` 모드는 제거했다. `main.py crawl`은 이제 항상 DB 초기화, `runs` 기록, `posts` 저장, JSON 파일 저장을 수행하며, `tests/test_main_crawl_persistence.py`로 `dry_run` 파라미터가 CLI/API 시그니처에 남아 있지 않고 crawler 옵션으로도 전달되지 않음을 고정했다. 사용자는 실제 저장 결과를 기준으로 수집 무결성을 확인하는 방향을 기본으로 한다.
- 2026-04-08: Reddit를 `src/crawlers/api/reddit.py` 기반 순수 HTTP/API 크롤러로 재도입했다. `crawl reddit --subreddit <slug> --sort hot|new` 는 verification challenge를 해제한 뒤 subreddit listing JSON을 수집하고, `crawl reddit` 는 저장된 `data/sessions/reddit_session.json` 로그인 세션으로 홈 `best.json` listing을 수집한다. 수집 결과는 기존 `posts`/`runs`/JSON 저장 경로를 그대로 타며, desktop `Credentials`, `Sources`, `Explorer`에도 reddit 옵션을 추가했다.
