# skim

멀티 플랫폼 정보 큐레이션 파이프라인. 11개 소스에서 콘텐츠를 수집하고, enrichment를 거쳐 SQLite에 저장합니다.

## 지원 플랫폼

| 유형 | 플랫폼 | 소스 |
|------|--------|------|
| Feed | HackerNews | hnrss.org (30+ points) |
| Feed | GeekNews | news.hada.io Atom |
| Feed | YouTube | RSS + yt-dlp (자막 추출) |
| Feed | ProductHunt | RSS |
| Feed | arXiv | Atom API (cs.AI) |
| Feed | HuggingFace | Daily Papers JSON API |
| Feed | Every.to | 6개 칼럼 RSS |
| API | Threads | Instagram Private API |
| API | X | GraphQL (twitter-api-client) |
| API | LinkedIn | API |
| Browser | Reddit | Playwright (비활성) |

## 설치

```bash
uv sync
uv run playwright install
cp .env.example .env  # 환경변수 설정
```

## 사용법

```bash
# 일일 배치 (최근 1일, enrichment 포함)
python main.py crawl all --days 1

# 단일 플랫폼
python main.py crawl hackernews --count 10
python main.py crawl threads --user-id 314216 --count 5

# 여러 플랫폼 + 옵션
python main.py crawl hackernews geeknews --days 1 --no-content
python main.py crawl all --days 1

# SNS 로그인 (Chrome 브라우저에서 수동 로그인 후 쿠키 자동 추출)
python main.py login threads
python main.py login x

# 지원 플랫폼 목록
python main.py platforms
```

## Desktop GUI

`desktop/`에는 `Tauri + React + TypeScript` 기반의 macOS 우선 데스크톱 GUI가 들어 있다. 첫 릴리스 범위는 `설정 + 탐색`이다.

```bash
cd desktop
pnpm install
pnpm tauri dev
```

현재 GUI에서 가능한 작업:

- YouTube / Threads / X / LinkedIn 추적 대상 등록
- macOS Keychain 기반 자격 증명 저장
- 세션 파일 상태 확인과 브라우저 로그인 트리거
- `data/skim.db` 검색, 필터링, 상세 조회
- `CSV`, `JSON`, `Markdown` 내보내기
- `feed_config.py` 기반 YouTube 목록 import

### CLI 옵션

| 옵션 | 설명 |
|------|------|
| `--count`, `-c` | 수집 개수 (SNS 기본값: 50) |
| `--days` | 과거 N일 수집 (Feed 기본값: 1) |
| `--output`, `-o` | JSON 출력 파일 경로 |
| `--debug`, `-d` | 디버그 모드 |
| `--sheets`, `-s` | Google Sheets 내보내기 |
| `--no-content` | enrichment 건너뛰기 |
| `--user-id`, `-u` | 특정 사용자 프로필 |

## 아키텍처

```
main.py (Typer CLI)
  ├── src/crawlers/         # 크롤러 (feed/, api/, browser/)
  │   ├── base.py           # Crawler Protocol
  │   ├── __init__.py       # REGISTRY (플랫폼 → 크롤러 매핑)
  │   └── auth/cdp.py       # Chrome DevTools Protocol 로그인
  ├── src/models.py         # Post Pydantic 모델
  ├── src/db.py             # SQLite (WAL, 중복 제거)
  ├── src/enrichment.py     # defuddle (본문 추출) + yt-dlp (자막)
  ├── src/feed_utils.py     # RSS/Atom 파싱
  └── feed_config.py        # URL/채널 설정
```

### 데이터 흐름
1. CLI에서 플랫폼 지정 → `REGISTRY`에서 크롤러 조회
2. `crawler.crawl(**options)` → `List[Post]` 반환
3. (선택) enrichment: defuddle로 본문 마크다운 추출, yt-dlp로 자막 추출
4. SQLite 저장 (`UNIQUE(platform, external_id)` 중복 제거)
5. JSON 파일 저장 → `data/{platform}/{timestamp}.json`

### 데이터 저장
- **SQLite**: `data/skim.db` — posts, summaries, feedback, runs
- **JSON**: `data/{platform}/{timestamp}.json`
- **Sessions**: `data/sessions/{platform}_session.json`

GUI 추가 후 설정 관련 테이블도 함께 사용한다.

- `tracked_sources`
- `platform_credentials`
- `app_settings`

## 자동 수집

```bash
# cron 등록 예시 (매일 09:00)
0 9 * * * /path/to/skim/scripts/run_daily_feed.sh
```

## 환경 변수

`.env` 파일에 설정:

```bash
# SNS 인증 (CDP 로그인에 사용)
THREADS_USERNAME=...
THREADS_PASSWORD=...
X_USERNAME=...
X_PASSWORD=...
LINKEDIN_USERNAME=...
LINKEDIN_PASSWORD=...
REDDIT_USERNAME=...
REDDIT_PASSWORD=...

# Google Sheets 내보내기
GOOGLE_WEBAPP_URL=...
```

## 라이선스

MIT
