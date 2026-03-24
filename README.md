# skim

멀티 플랫폼 정보 큐레이션 파이프라인.

다양한 소스(HackerNews, GeekNews, Reddit, Threads, X, LinkedIn, YouTube, ProductHunt, ArXiv, HuggingFace 등)에서 콘텐츠를 수집하고, enrichment를 거쳐 SQLite에 저장합니다.

## 설치

```bash
# 의존성 설치
uv sync

# Playwright 브라우저 설치
uv run playwright install
```

## 환경 설정

`.env` 파일 생성:

```bash
THREADS_USERNAME=your_instagram_username
THREADS_PASSWORD=your_instagram_password
LINKEDIN_USERNAME=your_linkedin_email
LINKEDIN_PASSWORD=your_linkedin_password
X_USERNAME=your_x_username
X_PASSWORD=your_x_password
REDDIT_USERNAME=your_reddit_username
REDDIT_PASSWORD=your_reddit_password
GOOGLE_WEBAPP_URL=your_google_webapp_url
```

## 사용법

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

## 지원 플랫폼

| 유형 | 플랫폼 |
|------|--------|
| Feed (RSS/HTTP) | HackerNews, GeekNews, YouTube, ProductHunt, ArXiv, HuggingFace, EveryTo |
| API | Threads, X, LinkedIn |
| Browser | Reddit |

## 데이터 저장

- **SQLite**: `data/skim.db` — posts, summaries, feedback, runs
- **JSON**: `data/{platform}/{timestamp}.json` — 크롤링 결과
- **Sessions**: `data/sessions/{platform}_session.json` — 로그인 세션

## 라이선스

MIT
