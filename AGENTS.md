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
uv run skim source export --out sources.opml                # 소스 목록 백업
uv run skim source import sources.opml --platform blogs     # 되읽기 (멱등)

# 데이터 꺼내기 (AI에 넘기기 전에 반드시 줄인다)
uv run skim research "topic" --fields platform,title,url    # 전문 없이 목록만
uv run skim research "topic" --max-chars 2000               # 본문 절단 + truncated 표시
uv run skim bundle --days 1 --group-by platform             # topic 없이 최근 글 본문까지
uv run skim export ./out --days 7 --unread                  # 마크다운 파일로
uv run skim mark 12 34 --state read                         # 소비 상태

# 운영
uv run skim backup --keep 3     # 온라인 백업 + quick_check
uv run skim doctor --strict     # warning 있으면 exit 1

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
| threads | `## Threads Replies` | 답글 1개 이상인 게시물 전부, 게시물당 1건 |
| lobsters | `## Lobsters Comments` | 게시물당 1건 (초당 1요청). 같은 응답의 `description_plain`이 본문 폴백 |

- **댓글 조회는 `comments`가 0보다 클 때만 한다.** 0건인 글을 조회하면 "유효 댓글 없음"과
  "HTTP 실패"가 둘 다 `None`이라 구분되지 않는다. reddit은 그 때문에 조용한 서브레딧에서
  0건 글 3개가 연속되면 서킷브레이커가 남은 게시글 전체의 댓글 수집을 끊었다.
- **파싱까지 `try` 안에 넣는다.** HTTP 호출만 감싸면 상류 응답 구조가 바뀔 때 파싱 예외가
  크롤 루프까지 올라가 그 회차의 게시글이 통째로 저장 0건이 된다. "댓글 실패가 게시글
  저장을 막지 않는다"는 계약이 실제로 깨져 있던 자리다. 회귀는
  `tests/test_comment_failure_isolation.py`가 잡는다.

- threads 답글은 타임라인 GraphQL이 주지 않는다. 대신 게시물 문서의 SSR 페이로드가
  답글까지 담고 있고 로그인도 필요 없어서, persisted query 좌표(`doc_id`)를 새로 들지 않는다.
  단 `threads.net`으로 요청하면 리다이렉트 뒤 페이로드가 빠진 셸이 오므로 `threads.com`으로 받는다.
  같은 URL이라도 페이로드가 빠진 문서가 간헐적으로 와서 한 번 재시도한다.
- threads는 작성자 self-reply 연작을 답글과 같은 `edges`에 담는다. 그 연작은 이미 본문에
  있으므로 스레드 시작자가 원글 작성자면 통째로 건너뛴다. 대화 중 작성자가 남긴 답변은 남는다.
- `comments`(= `direct_reply_count`)가 0보다 커도 답글 섹션이 안 붙을 수 있다. 삭제되거나
  비공개 계정이 단 답글까지 세는 값이라, 실제 노출되는 답글이 없는 게시물이 있다
  (브라우저로 열어도 안 보인다). 이 불일치만으로 추출 실패로 판단하지 않는다.
- 그래서 `comments`를 조회 임계로 높게 잡으면 안 된다. 반대 방향 오차도 있어서, `comments=1`인
  글에서 답글 2건이 나오기도 한다. 임계는 "0건만 거른다"로 둔다.
- **threads 답글에 페이지네이션을 붙이지 않는다.** 문서가 한 번에 주는 만큼(실측 최대 24건)이
  전부이고, 그 이상은 `BarcelonaPostPageRefetchableDirectQuery`를 4건씩 반복 호출해야 한다.
  그 요청은 세션 쿠키와 `x-fb-lsd` 토큰을 요구해 **계정으로 식별된다**. 지금 방식은 로그인이
  필요 없어 계정이 노출되지 않으므로, 답글 수집량보다 계정 안전을 우선한 결정이다(2026-08-10).
- 상한은 댓글당 1200자다. 개수 상한(`MAX_COMMENTS`)은 플랫폼마다 다르고 threads는 없다
  (`None`이면 받은 만큼 전부). 15로 자르던 때는 문서에 24건이 와도 9건을 버렸다.
- 댓글 수집 실패는 게시글 저장을 막지 않는다. 본문만 저장하고 경고만 남긴다.

### Crawler 유형과 패턴

모든 크롤러는 `packages/skim-core/src/skim_core/crawlers/base.py`의 `Crawler` Protocol을 구현하고, `packages/skim-core/src/skim_core/crawlers/__init__.py`의 `REGISTRY`에 등록된다.

| 유형 | 위치 | 옵션 기준 | 플랫폼 |
|------|------|-----------|--------|
| Feed | `packages/skim-core/src/skim_core/crawlers/feed/` | `since` | hackernews, lobsters, geeknews, youtube, producthunt, arxiv, huggingface, everyto, blogs, ailabs |
| API | `packages/skim-core/src/skim_core/crawlers/api/` | `count` | threads, x, linkedin, reddit |

#### 좁은 창에서 0건이 나오는 소스

발행일이 실제 게시 시점보다 밀리는 소스가 있다. 데일리 배치는 `crawl all --days 1`로
돌기 때문에, 기본값에만 보정을 넣으면 정작 운영 경로에서는 매번 0건이 된다.
보정은 `skim_cli.cli.min_lookback_days()`에 **바닥값으로** 넣는다. `days is None`일 때만
적용되는 분기에 넣으면 `--days 1`이 그걸 덮어쓴다 (arxiv가 그래서 이틀간 멈춰 있었다).

거르는 기준 필드도 확인한다. 큐레이션 목록은 원문 발행일이 아니라 목록에 올린 날짜로
걸러야 한다 (huggingface는 `paper.submittedOnDailyAt`, `publishedAt`은 arXiv 발행일이라
며칠에서 몇 주 밀려 있다).

#### 새 소스를 넣기 전에

`fetch_feed`는 발행일이 없는 엔트리를 버린다. 200에 엔트리가 오더라도 날짜 필드가
없으면 등록해도 매번 0건인데 겉보기엔 멀쩡하다. 실측하지 않은 URL은 넣지 않는다.
떨어진 후보는 `docs/TODO.md`의 "Rejected Sources"에 이유와 함께 남긴다.

- Feed 크롤러: `since` 유무에 따라 RSS/API 모드 자동 전환
- API 크롤러: `data/sessions/{platform}_session.json` 세션 쿠키 재사용
- Reddit API 크롤러: subreddit listing은 verification challenge 해제 후 JSON endpoint 호출, 홈 피드는 로그인 세션 기반 `best.json` 호출

### 주요 모듈

- `packages/skim-cli/src/skim_cli/cli.py`: Typer CLI 엔트리포인트
- `packages/skim-core/src/skim_core/models.py`: `Post` Pydantic 모델
- `packages/skim-core/src/skim_core/db.py`: SQLite WAL 모드, `UNIQUE(platform, external_id)` 중복 제거.
  **연결을 여는 함수는 `try/finally`로 닫는다** — `commit()`/`close()`를 try 밖에 두면
  `sqlite3.Error`가 아닌 예외에서 RESERVED 락이 남아, 뒤따르는 쓰기가 60초를 기다리다
  `database is locked`로 죽으며 원래 오류를 덮는다.
  `canonical_body()`는 정본 본문 판정의 단일 소스다. 저장과 결손 집계가 함께 써야 한다
  (따로 판정하던 때 API형 4종이 정상 저장돼도 매일 "전량 실패"로 찍혔다).
  소비 상태(읽음/보관)는 `feedback` 테이블을 쓴다. `posts`에 컬럼을 더하지 않는다.
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
