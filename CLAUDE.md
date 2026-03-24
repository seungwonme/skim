# CLAUDE.md — skim

멀티 플랫폼 정보 큐레이션 파이프라인.

## Commands

### Development Setup
```bash
# Install dependencies (uv manages virtualenv automatically)
uv sync

# Install Playwright browser
uv run playwright install
```

### Linting and Formatting
```bash
# Run Black formatter
black . --config pyproject.toml

# Run isort for import sorting
isort . --settings-path pyproject.toml

# Run flake8 linter
flake8

# Run pylint
pylint src/

# Pre-commit hooks (if pre-commit is installed)
pre-commit install
pre-commit run --all-files
```

### Running the Crawler
```bash
# 단일 플랫폼 크롤링
python main.py crawl hackernews --count 10
python main.py crawl threads --user-id 314216 --count 5

# 일일 배치 (최근 1일, 콘텐츠 enrichment 포함)
python main.py crawl all --days 1

# 여러 플랫폼 지정
python main.py crawl hackernews geeknews --days 1 --no-content

# 디버그/미리보기
python main.py crawl reddit --debug
python main.py crawl all --days 1 --dry-run

# 지원 플랫폼 목록
python main.py platforms

# 로그인
python main.py login threads
```

## Architecture Overview

### Core Structure
- **main.py**: 통합 CLI (Typer). `crawl`, `login`, `platforms` 커맨드 제공
- **src/crawlers/**: 유형별 크롤러 구현
  - **base.py**: `Crawler` Protocol — 모든 크롤러의 공통 인터페이스
  - **browser/**: 브라우저 기반 (Playwright) — reddit
  - **api/**: API 기반 — threads, x, linkedin
  - **feed/**: RSS/HTTP 피드 기반 — hackernews, geeknews, youtube, producthunt, arxiv, huggingface, everyto
  - **auth/**: 인증 유틸리티 (CDP 로그인)
  - **`__init__.py`**: `REGISTRY` dict — 플랫폼명 → 크롤러 클래스 매핑
- **src/models.py**: Post Pydantic 모델 (title, summary, content_markdown 등 확장 필드 포함)
- **src/db.py**: SQLite 저장 (posts, summaries, feedback, runs)
- **src/enrichment.py**: 콘텐츠 enrichment (defuddle, YouTube transcript)
- **src/feed_utils.py**: RSS/Atom 파싱 유틸리티
- **src/exporters/**: Google Sheets 내보내기
- **feed_config.py**: RSS URL, YouTube 채널, API URL 설정

### Key Design Patterns
1. **Protocol Pattern**: 모든 크롤러가 `Crawler` Protocol 구현 (`async crawl(**options) -> List[Post]`)
2. **Registry Pattern**: `REGISTRY` dict로 플랫폼명 기반 크롤러 조회
3. **Dual Mode**: feed 크롤러는 `since` 유무에 따라 RSS/API 모드 자동 전환
4. **BrowserCrawler ABC**: 브라우저 기반 크롤러용 Playwright 라이프사이클 관리

### Crawler Flow
1. `python main.py crawl [platforms] [options]`
2. REGISTRY에서 크롤러 클래스 조회
3. `crawler.crawl(**options)` 실행 → `List[Post]` 반환
4. SQLite DB에 저장 (중복 자동 제거)
5. JSON 파일 저장 + (선택) Google Sheets 내보내기

### Data Storage
- **SQLite**: `data/skim.db` — posts, summaries, feedback, runs 테이블
- **JSON**: `data/{platform}/{timestamp}.json` — 크롤링 결과 파일
- **Sessions**: `data/sessions/{platform}_session.json` — 로그인 세션

## Environment Variables
`.env` 파일에 설정:
- `THREADS_USERNAME`, `THREADS_PASSWORD`
- `LINKEDIN_USERNAME`, `LINKEDIN_PASSWORD`
- `X_USERNAME`, `X_PASSWORD`
- `REDDIT_USERNAME`, `REDDIT_PASSWORD`
- `GOOGLE_WEBAPP_URL` (Google Sheets export)

<rules>
The following rules should be considered foundational. Make sure you're familiar with them before working on this project:
@.cursor/rules/memory-bank.mdc
@.cursor/rules/vibe-coding.mdc

Git convention defining branch naming, commit message format, and issue labeling based on GitFlow and Conventional Commits.:
@.cursor/rules/git-convention.mdc

threads 크롤러를 수정할 때 참고하세요:
@.cursor/rules/threads-crawler.mdc
</rules>
