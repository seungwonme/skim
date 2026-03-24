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
python main.py crawl all --days 1 --dry-run

# 기타
python main.py platforms          # 지원 플랫폼 목록
python main.py login threads      # CDP 로그인
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
| API | `src/crawlers/api/` | `count` (개수) | threads, x, linkedin |
| Browser | `src/crawlers/browser/` | `count` | reddit (CAPTCHA로 비활성) |

- Feed 크롤러: `since` 유무에 따라 RSS/API 모드 자동 전환
- API 크롤러: CDP로 추출한 세션 쿠키 사용 (`data/sessions/{platform}_session.json`)
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
