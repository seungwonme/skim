# AGENTS.md

Shared AI working guide for this repository. `CLAUDE.md` imports this file.

## Start Here

- Human setup and commands: `README.md`
- Source backlog and future crawl targets: `docs/TODO.md`
- Directory-specific AI rules: nearest `AGENTS.md`
- Treat `data/`, `refs/`, and `worktrees/` as local/runtime material unless a task names them.

## Commands

```bash
# 의존성 설치
pnpm install   # husky/commitlint 훅용
uv sync
uv run playwright install
brew install just

# 루트 품질 게이트 (justfile이 태스크 단일 진입점)
just lint
just test    # Python pytest + Swift 유닛 테스트
just e2e     # desktop e2e 스모크 (fixture DB + 실제 앱 부팅)
just build   # desktop 앱 빌드
just dev     # desktop 앱 실행

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

# YouTube 히스토리 (채널 과거 영상 목록 백필 + 개별 전사)
uv run skim youtube-history --channel LangChain --years 1
uv run skim youtube-transcribe <video-url-or-id>

# 소스 진단과 등록
uv run skim source probe https://example.com/blog   # 읽기 전용 판정 (등록 안 함)
uv run skim source probe <url> --no-sample --emit json
uv run skim source add https://example.com/blog     # 진단 후 tracked_sources 등록
uv run skim source list --platform blogs
uv run skim source sync                             # feed_config -> tracked_sources (멱등)
uv run skim source refresh --all                    # tier 재관측, 죽은 피드 탐지
uv run skim source list --emit markdown > docs/SOURCES.md   # 인벤토리 갱신

# 기타
uv run skim platforms           # 지원 플랫폼 목록
uv run skim login threads       # CDP 로그인
uv run skim login reddit        # Reddit 로그인 세션 저장

# Desktop
swift run --package-path apps/desktop SkimDesktop
swift build --package-path apps/desktop
```

## Architecture

### Monorepo Layout

```text
.
├── apps/
│   └── desktop/                   # SwiftUI macOS app
├── packages/
│   ├── skim-cli/src/skim_cli/           # Typer CLI
│   └── skim-core/src/skim_core/         # crawler, DB, enrichment, feed config
├── scripts/                             # import/cron/helper scripts
├── images/                              # README/project images
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

### 데이터 계약: DB는 소비 준비가 끝난 상태다

- `posts.content_markdown`은 **추출이 완료된 정본 본문**이다. 이 DB를 읽는 소비자(AI, digest, 데스크톱 앱, research)는 재추출 절차 없이 그대로 사용한다고 가정한다.
- 따라서 추출 완결성은 크롤러의 책임이다. 저장 시점에 링크 원문 본문, 플랫폼 자체 본문(Ask/Show HN 텍스트, GeekNews 한국어 요약), 토론(댓글)까지 채워야 한다. "링크만 저장"은 계약 위반이다.
- 예외는 `--no-content` 명시 실행과 `youtube-history` 백필 행(임베드용 목록, 자막은 사용자가 요청할 때 `youtube-transcribe`로 채움)뿐이다.
- 크롤러가 본문에 합성하는 섹션 라벨은 항상 영어로 쓴다 (예: `## Hacker News Comments`, `## Original Article`). 가용한 메타데이터(작성자, 작성시각, 점수)는 텍스트에 함께 표기한다.

#### 댓글 수집

댓글은 `skim_core.comments`의 `Comment`로 정규화한 뒤 `render_comment_section()`으로 섹션을 만들고
`append_comment_section()`으로 본문 뒤에 잇는다. 각 크롤러가 자기 포맷을 따로 만들지 않는다.

| 플랫폼 | 섹션 라벨 | 추가 요청 |
|--------|-----------|-----------|
| hackernews | `## Hacker News Comments` | Algolia item API 1건 |
| geeknews | `## GeekNews Comments` | 없음 (지표 수집이 받는 토픽 HTML 재사용) |
| x | `## X Replies` | 스레드는 없음(TweetDetail 재사용). 단독 트윗은 답글 3개 이상인 것만, 회차당 20건까지 |
| reddit | `## Reddit Comments` | 게시글당 1건 (초당 1요청 간격) |
| linkedin | `## LinkedIn Comments` | 게시글당 1건 (Voyager `feed/comments`) |
| youtube | `## YouTube Comments` | 영상당 yt-dlp 1회 |
| producthunt | `## Product Hunt Comments` | 제품당 1건 (PH 제품 페이지) |
| threads | `## Threads Replies` | 답글 3개 이상인 게시물만, 회차당 20건까지 |

- threads 답글은 타임라인 GraphQL이 주지 않는다. 대신 게시물 문서의 SSR 페이로드가
  답글까지 담고 있고 로그인도 필요 없어서, persisted query 좌표(`doc_id`)를 새로 들지 않는다.
  단 `threads.net`으로 요청하면 리다이렉트 뒤 페이로드가 빠진 셸이 오므로 `threads.com`으로 받는다.
  같은 URL이라도 페이로드가 빠진 문서가 간헐적으로 와서 한 번 재시도한다.
- threads는 작성자 self-reply 연작을 답글과 같은 `edges`에 담는다. 그 연작은 이미 본문에
  있으므로 스레드 시작자가 원글 작성자면 통째로 건너뛴다. 대화 중 작성자가 남긴 답변은 남는다.
- `comments`(= `direct_reply_count`)가 0보다 커도 답글 섹션이 안 붙을 수 있다. 삭제되거나
  비공개 계정이 단 답글까지 세는 값이라, 실제 노출되는 답글이 없는 게시물이 있다
  (브라우저로 열어도 안 보인다). 이 불일치만으로 추출 실패로 판단하지 않는다.
- 상한은 플랫폼별 `MAX_COMMENTS`(기본 15)와 댓글당 1200자다. 본문 신호를 댓글이 덮지 않게 한다.
- 댓글 수집 실패는 게시글 저장을 막지 않는다. 본문만 저장하고 경고만 남긴다.

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
- `packages/skim-core/src/skim_core/comments.py`: 플랫폼 중립 `Comment`와 본문 댓글 섹션 합성
- `packages/skim-core/src/skim_core/feed_utils.py`: RSS/Atom 파싱, KST 변환. `FEED_HEADERS`의 Chrome 버전은 news.hada.io 차단선에 걸리므로 함부로 낮추지 않는다
- `packages/skim-core/src/skim_core/feed_config.py`: RSS URL, YouTube 채널 ID, API endpoint 설정
- `apps/desktop/`: SwiftUI desktop reader for local `data/skim.db`

### 소스 목록의 정본

`youtube`와 `blogs`는 **DB의 `tracked_sources` 테이블이 정본**이고, `feed_config.py`는 레지스트리가 비었거나 DB를 못 읽을 때만 쓰이는 폴백 겸 seed다. 나머지 플랫폼은 아직 `feed_config.py`가 정본이다.

- 새 소스는 `skim source add <url>`로 등록한다. probe가 피드를 찾고 관측한 `fetch_tier`를 함께 기록한다.
- `fetch_tier`는 사람이 선언하는 값이 아니라 probe가 관측한 값이다: `rss`(피드에 본문 포함) > `rss+enrich`(HTTP 추출) > `rss+render`(playwright 필요) > `scrape`(피드 없음).
- `feed_config.py`를 직접 고쳤으면 `skim source sync`로 레지스트리에 반영한다.
- 계정 팔로우가 소스 목록을 소유하는 플랫폼(reddit, threads, x, linkedin)은 레지스트리에 넣지 않는다.
- 소스를 추가·갱신했으면 `docs/SOURCES.md`를 재생성해 함께 커밋한다. 목록이 DB에 있어 저장소 diff에 안 남으므로, 이 문서가 "언제 무엇을 추가했는지"의 유일한 기록이다.
- 추출 회귀는 `skim doctor`가 소스별로 잡는다. 판정은 절대 임계가 아니라 그 소스의 지난 120일 대비다 (`source_health.py`).

### 새 크롤러 추가 방법

1. `packages/skim-core/src/skim_core/crawlers/{type}/` 아래에 크롤러 클래스 생성 (`async crawl(**options) -> List[Post]`)
2. `packages/skim-core/src/skim_core/crawlers/__init__.py`의 `REGISTRY`에 등록
3. Feed 크롤러면 `packages/skim-core/src/skim_core/feed_config.py`에 소스 추가

## Docs Hygiene

- `README.md`는 사람용 설치, 실행, 구조 요약만 둔다.
- `docs/TODO.md`는 소스 후보와 작업 큐만 둔다. 구현 계획은 `docs/plans/` 아래로 분리한다.
- 오래된 설계/리뷰 문서는 삭제보다 첫 문단에 historical 또는 draft 상태를 명시한다.
- Claude 전용 로딩 표면은 `CLAUDE.md`에만 두고, 공용 AI 규칙은 이 파일에 둔다.

## Git Convention

- 브랜치: `type/[branch/]description[-#issue]` (GitFlow)
- 커밋: `<type>(<scope>): <subject>` (Conventional Commits)
- type: feat, fix, docs, style, refactor, test, chore

## Runtime Auth

- `SKIM_WORKSPACE_ROOT` can override the workspace root when needed.
- Login sessions live under `data/sessions/{platform}_session.json`.
- macOS credentials live in Keychain; SQLite stores only `platform_credentials` references.
- Use `uv run skim login <platform> --identifier <id>` to read a saved Keychain credential, or add `--password-stdin --save-credential` to store one from CLI.

## Tooling

- 태스크 러너: `just` (justfile)
- Node: husky/commitlint 훅용으로만 `pnpm` 유지 (JS/TS 소스 없음)
- Python: `uv` workspace
- Swift desktop: `apps/desktop`
- Git hooks: `husky`
- Commit message validation: `commitlint`
