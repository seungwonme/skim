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
uv run ruff check --fix packages tests scripts   # import 정렬
uv run ruff format packages tests scripts        # 포맷터 (편집 훅과 같은 설정)
uv run flake8
uv run pylint packages/skim-core/src/skim_core packages/skim-cli/src/skim_cli

# 크롤링
uv run skim crawl hackernews --count 10
uv run skim crawl all --days 1
uv run skim crawl all --days 1 --catch-up   # 데일리: 놓친 회차까지 창을 넓힘 (최대 7일)
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
uv run skim source sync                             # feed_config -> tracked_sources (멱등, blogs/everyto)
uv run python scripts/import_feed_config.py --preview   # YouTube 채널 seed 확인 (--preview 빼면 등록)
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
uv run python scripts/backfill_geeknews_topics.py --dry-run    # GN 요약, 댓글이 빠진 행 수
uv run python scripts/backfill_geeknews_topics.py --limit 100  # 남은 토픽 한도만큼 채움
uv run python scripts/backfill_github_releases.py --dry-run   # GitHub 릴리스 노트, PDF 본문 재수집 대상 (#46)
uv run python scripts/recover_geeknews_originals.py --dry-run # GN 조각 행 원문 복구 대상 (#33, 데일리 직후에 실행)

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
| geeknews | `## GeekNews Comments` | 없음. GN 요약과 같은 토픽 페이지 1건에서 받는다. 지표와 원문 링크는 `/newest` 목록 |
| x | `## X Replies` | 스레드는 없음(TweetDetail 재사용). 단독 트윗은 답글 3개 이상인 것만, 회차당 20건까지 |
| reddit | `## Reddit Comments` | 게시글당 1건 (초당 1요청 간격) |
| linkedin | `## LinkedIn Comments` | 게시글당 1건 (Voyager `feed/comments`) |
| youtube | `## YouTube Comments` | 영상당 yt-dlp 1회 |
| producthunt | `## Product Hunt Comments` | 제품당 1건 (PH 제품 페이지) |
| threads | `## Threads Replies` | 답글 1개 이상인 게시물 전부, 게시물당 1건(스레드 25개씩, 4쪽까지). 로그인 브라우저로 게시물 페이지를 한 번 열어 웹앱으로 부른다 |
| lobsters | `## Lobsters Comments` | 게시물당 1건 (초당 1요청). 같은 응답의 `description_plain`이 본문 폴백 |
| huggingface | `## Hugging Face Comments` | 논문당 1건. 페이지 SSR 페이로드(`data-props`)라 로그인 불필요 |

- **댓글 조회는 `comments`가 0보다 클 때만 한다.** 0건인 글을 조회하면 "유효 댓글 없음"과
  "HTTP 실패"가 둘 다 `None`이라 구분되지 않는다. reddit은 그 때문에 조용한 서브레딧에서
  0건 글 3개가 연속되면 서킷브레이커가 남은 게시글 전체의 댓글 수집을 끊었다.
- **파싱까지 `try` 안에 넣는다.** HTTP 호출만 감싸면 상류 응답 구조가 바뀔 때 파싱 예외가
  크롤 루프까지 올라가 그 회차의 게시글이 통째로 저장 0건이 된다. "댓글 실패가 게시글
  저장을 막지 않는다"는 계약이 실제로 깨져 있던 자리다. API형 4종을 먼저 고쳤는데
  feed형(hackernews/geeknews/producthunt)에 같은 결함이 남아 있었다. 새 크롤러를
  만들 때 HTTP만 감싸고 끝내지 않는다. 회귀는
  `tests/test_comment_failure_isolation.py`가 잡는다.

- **threads 답글은 로그인 세션으로 받고, 요청은 계정으로 식별된다.** 2026-09-08에 로그인
  없이 받던 게시물 문서의 SSR 페이로드에서 답글이 빠졌다. 2026-08-10에는 계정 안전을 이유로
  계정 요청을 쓰지 않기로 했지만, 로그인 없이는 답글이 오지 않게 되자 2026-09-30에 사용자가
  계정 요청을 쓰기로 결정했다(#26). 9/8부터 9/30까지 저장된 threads 글에는 답글이 없다.
- **답글은 웹앱의 네트워크 계층으로만 부른다.** 게시물 페이지를 한 번 열고, React fiber를
  타고 올라가 Relay 환경을 찾은 뒤 `BarcelonaPostPageDirectRepliesRefetchQuery.graphql`
  모듈의 `params`로 `getNetwork().execute()`를 부른다. 같은 `doc_id`, 변수, provider
  플래그 35개, 웹앱 폼 필드를 손으로 맞춘 요청은 파이썬에서든 페이지 안 `fetch`에서든
  오류 없이 `direct_replies: null`로 온다(2026-09-30 실측). 원인을 못 찾았으므로 손으로 만든
  요청으로 돌아가지 않는다. 모듈 이름으로 부르므로 `doc_id`와 플래그는 웹앱이 재배포돼도
  따라간다. 모듈이나 Relay 환경을 못 찾으면 부팅 단계에서 멈추고 본문만 저장한다.
- 스크롤로 웹앱이 다음 쪽을 부르게 하는 방식은 쓰지 않는다. 같은 스크립트가 어떤 때는
  요청을 보내고 어떤 때는 안 보내서 재현되지 않았다. 로그인 문서를 `fetch`로 받으면
  답글이 빠진 다른 문서가 온다(페이지 이동으로 받은 문서에만 첫 10개 스레드가 있다).
- 한 요청에 최상위 스레드 25개를 받는다. 웹앱은 4개씩 받지만 서버는 큰 값도 받아 준다.
  답글 54개(스레드 17개) 게시물이 요청 1건에 끝났다. 게시물당 4쪽(스레드 100개)까지
  넘기고 요청 사이에 1초를 둔다. 스레드 안 대화(`posts.edges`)는 응답이 준 만큼만 담는다.
  게시물 ID는 URL의 shortcode를 64진수로 풀어 얻는다.
- threads는 작성자 self-reply 연작을 답글 목록에 함께 담는다. 그 연작은 이미 본문에
  있으므로 스레드 시작자가 원글 작성자면 통째로 건너뛴다. 대화 중 작성자가 남긴 답변은 남는다.
- `comments`(= `direct_reply_count`)가 0보다 커도 답글 섹션이 안 붙을 수 있다. 삭제되거나
  비공개 계정이 단 답글까지 세는 값이라, 실제 노출되는 답글이 없는 게시물이 있다
  (브라우저로 열어도 안 보인다). 이 불일치만으로 추출 실패로 판단하지 않는다.
- 그래서 `comments`를 조회 임계로 높게 잡으면 안 된다. 반대 방향 오차도 있어서, `comments=1`인
  글에서 답글 2건이 나오기도 한다. 임계는 "0건만 거른다"로 둔다.
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
arXiv 메일링은 09:00 KST라 00:02 배치보다 늦고 주말에는 없다. 화요일을 2일 창에
두면 금요일분이 잘려 0건이 된다 (2026-09-01 00:06 회차). 월/화/토/일은 4일이다.

거르는 기준 필드도 확인한다. 큐레이션 목록은 원문 발행일이 아니라 목록에 올린 날짜로
걸러야 한다 (huggingface는 `paper.submittedOnDailyAt`, `publishedAt`은 arXiv 발행일이라
며칠에서 몇 주 밀려 있다).

#### 0건을 회귀로 볼 때

0건으로 끝난 플랫폼은 그 플랫폼의 최근 60일 게시 간격으로 판정한다
(`_zero_result_verdict`, #47). 최근 유입 이력만 보던 때는 2~4일에 한 편 올라오는
everyto가 거의 매일 회귀로 잡혀 run이 상시 `degraded`였고, 그 속에 9/24~29 arxiv
장애가 묻혔다.

- 공백은 **이번 회차가 본 창보다 긴 것만** 센다. 그런 공백이 2번(`MISSED_RUN_ALLOWANCE`,
  회차를 놓친 날 몫) 이하인 소스의 0건은 바로 회귀다. 하루 기준으로 세면 7일 창인
  producthunt가 뜸한 소스로 분류돼, 고장 나도 이틀 뒤에야 잡힌다.
- 그보다 많으면 원래 창이 비는 날이 있는 소스다. 마지막 글 이후 침묵이 60일 최장
  공백보다 길 때만 회귀다.
- SNS는 최근 N건을 받으므로 0건이면 늘 회귀다.
- 수~금의 arxiv(2일 창)는 주말 공백 때문에 뜸한 소스로 분류된다. 장애로 생긴 공백도
  60일 동안 최장 공백에 남아, 그동안은 그보다 짧은 다음 장애를 정상 공백으로 본다.
  arxiv 전면 실패는 예외로 올라오므로(아래 폴백 절) 이 판정에 기대지 않는다.

#### 놓친 회차 채우기 (`--catch-up`)

데일리는 `crawl all --days 1 --catch-up`으로 돈다. 창이 "전날 0시부터"로 고정이라,
맥북이 잠들어 회차를 통째로 놓치거나 한 플랫폼이 실패하면 그날 글이 다음 창에 다시
들어오지 않았다 (#21).

- feed 플랫폼마다 창을 끝까지 채운 마지막 회차의 시작 시각을 `crawl_checkpoints`에
  남긴다. 다음 회차는 그날 0시부터 본다. 놓친 날이 없으면 `--days 1` 창과 같아서 이미
  받은 날을 다시 덮지 않는다. GeekNews 토픽 한도처럼 요청 수가 걸린 소스를 위해서다.
- 체크포인트는 크롤러가 예외 없이 끝나고 저장까지 마친 플랫폼만 옮긴다. 최근 14일
  유입 이력이 있는 플랫폼이 0건이면 고장일 수 있어 옮기지 않는다. 0건 회귀 경고보다
  넓은 기준이다. 고장을 조용한 날로 보고 옮기면 그 구간을 다시 못 보지만, 조용한 날에
  안 옮기면 다음 창이 하루 넓어질 뿐이다.
- 거슬러 보는 폭은 7일(`CATCH_UP_MAX_DAYS`)까지다. 넘으면 채우지 못하는 날수를 로그에
  남긴다. 피드가 실어 주는 건수가 그보다 적은 소스는 7일 안이라도 다 못 채운다.
- `--count`, `--no-content`와 함께 쓸 수 없다. 창을 본문까지 끝까지 채운 회차만
  체크포인트를 옮길 수 있어서다. `--catch-up` 없이 돈 수동 실행은 체크포인트를 읽지도
  옮기지도 않는다.
- SNS(threads, x, linkedin, reddit)는 창이 아니라 최근 N건이라 대상이 아니다.
  못 받은 날은 되찾지 못한다.
- 체크포인트를 못 읽으면 catch-up을 끄고 기본 창으로 돈다. 빈 체크포인트로 이어 가면
  이번 회차가 놓친 구간을 건너뛴 채 오늘로 옮겨 버린다.

#### 크롤러 사이의 기능 편차

새 크롤러를 만들거나 고칠 때, 다른 크롤러가 이미 하는 것을 안 하고 있지 않은지 본다.
2026-08-10 전수 조사에서 나온 것들이다.

- **본문이 비면 버리기 전에 폴백을 본다.** threads/linkedin은 이미지만 올린 글에서
  `None`을 돌려 DB에 행 자체를 안 만들었다. 사다리는 본문 -> alt text -> 미디어 링크 ->
  버림이고, 폴백으로 채웠으면 `content_status` extras로 표시한다.
- **링크 게시물은 원문 URL을 보존하고 추출한다.** 제목만 남기면 소비자가 원문으로 갈
  방법이 없다. 애그리게이터(hackernews/lobsters/everyto/reddit)는 `_enrich_article_item`의
  3단 사다리를 타되 `min_words`를 낮춘다. 짧은 릴리스 노트도 정당한 본문이다.
  원문이 PDF면 HTML 추출기는 늘 실패하므로 `_pdf_fallback`으로 넘어간다. GeekNews는
  원문을 `geeknews.extract_original`로 따로 추출해서 이게 빠져 있었다 (2026-07 이후
  PDF 원문 6건 중 5건 failed).
- **GitHub 페이지는 작성자가 쓴 부분만 본문으로 센다.** 릴리스 페이지를 페이지째
  추출하면 로그인 안내와 저장소 머리말 같은 화면 문구가 노트를 감싸고, 패치 릴리스의
  짧은 노트(30단어 안팎)는 60단어 게이트에 걸려 빈 본문이 됐다 (#46). 릴리스 링크는
  노트 영역(`.markdown-body`)만 받고 단어 수로 거르지 않는다. 저장소의 릴리스 피드를
  구독한 blogs는 피드 본문이 노트 그 자체라 페이지를 열지 않는다. 애그리게이터의 피드
  본문은 그 사이트의 설명이라 노트로 쓰지 않는다. 게이트는 GitHub 화면 문구를 빼고
  센다. 화면 문구도 60단어를 넘겨서, GitHub PDF 링크(`blob/.../*.pdf`)가 화면 문구로
  게이트를 통과해 PDF 폴백까지 못 갔다. PDF 폴백은 blob 주소를 raw 주소로 바꿔 받는다.
  GeekNews 원문은 defuddle을 먼저 보는 별도 경로라 릴리스 노트 경로를 타지 않는다.
- **상류가 주는 고유 id를 버리지 않는다.** 없으면 `db.py`가 URL로 병합해서, 같은 URL의
  서로 다른 글이 통째로 사라진다 (producthunt 재런치). 반대로 id 체계를 바꾸면 같은 글이
  두 행으로 갈라지므로(ailabs 182행) 전환할 때는 백필이 함께 가야 한다.
- **`count`는 enrichment 앞에서 자른다.** CLI가 마지막에 `posts[:count]`로 자르므로,
  그 전에 안 자르면 버려질 항목까지 원문 추출과 댓글 조회를 돈다.
- **창이 한 페이지보다 넓으면 페이징한다.** 상한에 닿아 창을 못 덮으면 경고를 남긴다.
  조용히 넘어가면 "그날 그만큼밖에 없었다"로 읽힌다.
- **불완전한 본문은 표시한다.** everyto는 구독자 벽까지만 저장되므로
  `content_status="paywalled"`를 단다. 표시가 없으면 반쪽을 완결된 글로 요약한다.
  geeknews는 토픽 페이지를 못 받아 GN 요약 대신 RSS 요약 조각이 들어간 글에
  `content_status="partial"`을 단다.
- **요청량으로 막히는 호스트는 요청을 한 모듈에 모으고 한도를 센다.** news.hada.io는
  토픽 페이지를 (IP, UA) 단위 요청 수로 막는다. 간격은 상관없다: 1초 간격 33건
  (2026-09-22), 3초 간격 31건(2026-09-29 21:24)에서 똑같이 막혔다. 풀리는 시간은
  들쭉날쭉하다: 2026-09-29 00:14 차단은 10분 안에 풀렸고, 21:24 차단은 다음 날
  00:33에도 그대로였다 (#33). 2026-09에는 enrichment.py가 같은 토픽 페이지를 글마다
  두 번씩 따로 열어 매 회차 24요청쯤에서 막히고, 9월 저장분 1,082건 중 959건이 RSS
  요약 조각만 남았다 (#29).
  지금은 원문 링크와 지표를 브라우저 확인이 걸리지 않는 `/newest` 목록에서 받고,
  토픽 페이지는 `geeknews.fetch_topic`만 연다. 크롤, 백필, 수동 실행이 20시간 창에
  25건(`TOPIC_BUDGET`)을 `data/geeknews_topic_budget.json`으로 나눠 쓰고, 한도가
  모자라면 원문 링크 없는 자체 글, 댓글 많은 글부터 받는다. 한 번 막히면 차단 시각을
  그 파일에 적어 창이 지날 때까지 어느 프로세스도 요청하지 않는다. 막힌 뒤의 요청은
  차단을 연장할 수 있다. 멈출 때는 차단인지 한도 소진인지, 다시 요청하는 시각, 차단
  종류(403, Forbidden, 브라우저 확인)를 로그에 남긴다 (`topic_pause_reason`). 다른
  모듈과 테스트에서 news.hada.io를 부르지 않는다 (테스트는 `conftest.py`가 한도
  파일을 격리하고, doctor 테스트는 `probe_user_agent`를 막는다).
  하루 글 수(50건 안팎)가 한도보다 많아 GN 요약과 댓글이 빠진 `partial` 행은 매일
  생긴다. 원문은 붙어 있으므로 doctor는 보여만 준다. doctor 경고는 두 가지다: 원문조차
  없는 "피드 요약 조각뿐인 본문"이 최근 7일 20%를 넘을 때, 그리고 한도 파일에 차단이
  기록됐을 때(이때는 probe도 보내지 않는다).
  막혔던 기간에 쌓인 조각 행은 `scripts/recover_geeknews_originals.py`가 목록을
  거슬러 읽어 원문을 붙인다(#33). 목록도 같은 규칙으로 물러난다(`geeknews.ListingScan`:
  차단 기록이 있으면 시작하지 않고, 막히면 멈추고 기록한다). 목록에 한도가 있는지는
  아직 모르므로 데일리 직후에 돌린다.
- **서브피드 이름을 `platform`에 넣지 않는다.** `db.py`는 Post의 `platform`을 인자보다
  우선하므로, `fetch_feed`가 넣는 피드 이름(`hackernews/show`)을 그대로 넘기면 DB에
  별도 플랫폼 행이 생긴다. 서브피드는 `source`에 남긴다 (blogs가 쓰는 방식).
  2026-08-10에 Show/Ask HN 60행이 그렇게 갈렸다. hnrss show/ask가 실제로 저장된
  회차가 그때가 처음이라 도입 시점(#14)에는 안 드러났다.
- **한 호스트에 물린 소스는 폴백 경로를 둔다.** `--catch-up`은 플랫폼이 실패한 날만
  다음 회차에 다시 본다. 성공으로 끝난 회차 안에서 빠진 피드는 다시 보지 않으므로
  그대로 영구 유실이다. hackernews는 피드 세
  장이 전부 hnrss.org라 502에 같이 넘어가므로, 0건인 피드를 Algolia
  `search_by_date`로 다시 채운다.
  **판정은 피드 단위여야 한다.** "전부 0건일 때만"으로 두면 한 장만 죽은 흔한
  경우를 못 잡는다 (2026-08-10 프로덕션: newest만 502, show/ask는 정상 -> 30점
  이상 글 전량 유실). HN은 하루 창에 어느 피드도 0건이 되는 날이 없으므로 0건은
  곧 장애 신호이고, 정말 없었다면 폴백도 0건을 주므로 무해하다.
  Algolia `tags`의 콤마는 AND다. `(story,show_hn)`처럼 괄호로 싸면 OR이 돼
  필터가 통째로 풀리고 전체 글이 온다.
  arxiv는 export.arxiv.org가 2026-09 중순부터 406을 섞어 돌려준다 (같은 시기 다른
  프로젝트들도 보고했다). 첫 장이 거절된 분야는 `rss.arxiv.org` 공지 목록으로 받는다.
  이때 링크를 API와 같은 `https://arxiv.org/abs/{id}v{n}`로 맞춰야 같은 논문이 두
  행으로 갈리지 않는다. 네 분야가 API와 RSS 모두 실패하면 예외를 올린다. 빈 리스트로
  끝내면 run에 "0건 회귀"로만 남아 주말의 정상 0건과 구분되지 않는다 (9/24~9/29
  여섯 회차가 그렇게 묻혔다).

#### 새 소스를 넣기 전에

`fetch_feed`는 발행일이 없는 엔트리를 버린다. 200에 엔트리가 오더라도 날짜 필드가
없으면 등록해도 매번 0건인데 겉보기엔 멀쩡하다. 실측하지 않은 URL은 넣지 않는다.
떨어진 후보는 `docs/TODO.md`의 "Rejected Sources"에 이유와 함께 남긴다.

- Feed 크롤러: `since` 유무에 따라 RSS/API 모드 자동 전환
- API 크롤러: `data/sessions/{platform}_session.json` 세션 쿠키 재사용
- **threads For You 타임라인과 답글은 브라우저를 태운다.** Meta가 2026-09-08부터 클라이언트
  지문으로 거른다. 브라우저가 방금 7건을 받은 요청을 payload와 헤더까지 그대로 즉시
  재전송해도 edges가 0으로 오고 오류도 없다(2026-09-22 실측). 요청을 더 정교하게
  흉내내는 방향으로는 못 고치므로, 같은 증상을 만나면 그쪽으로 시간을 쓰지 않는다.
  같은 날 `X-IG-App-ID` 헤더도 거부 대상이 됐다(error 1357054). 이 헤더는 세션 기본
  헤더에 두지 않는다. 문서 GET은 통과하고 GraphQL만 죽어서 세션 만료처럼 보인다.
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
  기본 경로는 부를 때 `skim_core.db.DB_PATH`에서 읽는다. `from skim_core.db import DB_PATH`로
  들여오면 테스트가 바꾼 경로를 따르지 않는다. 메인 체크아웃에서 그 경로는 운영 DB다 (#32).
  테스트는 `tests/conftest.py`가 이 경로를 `tmp_path`로 돌리고, 작업공간 `data/`에 닿으면 실패시킨다.
- `packages/skim-core/src/skim_core/enrichment.py`: `bunx defuddle`, `yt-dlp`, transcript 정리
- `packages/skim-core/src/skim_core/comments.py`: 플랫폼 중립 `Comment`와 본문 댓글 섹션 합성
- `packages/skim-core/src/skim_core/feed_utils.py`: RSS/Atom 파싱, KST 변환. `FEED_HEADERS`의 Chrome 버전은 news.hada.io가 UA 문자열 단위 요청량으로 토픽 페이지를 막을 때 걸린다. 버전을 올리는 건 임시방편이고, 차단은 쓰지 않으면 몇십 분에 풀리므로 물러나는 쪽이 정답이다 (geeknews 크롤러의 간격·서킷브레이커). `CHALLENGE_MARKER`는 2026-09 차단의 형태였던 Turnstile 브라우저 확인 페이지의 표지다
- `packages/skim-core/src/skim_core/feed_config.py`: RSS URL, YouTube 채널 ID, API endpoint 설정
- `apps/desktop/`: SwiftUI desktop reader for local `data/skim.db`

### 소스 목록의 정본

`youtube`와 `blogs`는 **DB의 `tracked_sources` 테이블이 정본**이고, `feed_config.py`는 레지스트리가 비었거나 DB를 못 읽을 때만 쓰이는 폴백 겸 seed다. 나머지 플랫폼은 아직 `feed_config.py`가 정본이다.

- 새 소스는 `skim source add <url>`로 등록한다. probe가 피드를 찾고 관측한 `fetch_tier`를 함께 기록한다.
- `fetch_tier`는 사람이 선언하는 값이 아니라 probe가 관측한 값이다: `rss`(피드에 본문 포함) > `rss+enrich`(HTTP 추출) > `rss+render`(playwright 필요) > `scrape`(피드 없음).
- `feed_config.py`를 직접 고쳤으면 `skim source sync`로 레지스트리에 반영한다. sync는 blogs, everyto만
  다룬다. `YOUTUBE_CHANNELS`는 `scripts/import_feed_config.py`로 가져온다. youtube 크롤러는 레지스트리에
  youtube 행이 하나라도 있으면 `YOUTUBE_CHANNELS`를 보지 않으므로, 가져오지 않은 채널은 수집되지 않는다.
- 계정 팔로우가 소스 목록을 소유하는 플랫폼(reddit, threads, x, linkedin)은 레지스트리에 넣지 않는다.
- 소스를 추가·갱신했으면 `docs/SOURCES.md`를 재생성해 함께 커밋한다. 목록이 DB에 있어 저장소 diff에 안 남으므로, 이 문서가 "언제 무엇을 추가했는지"의 유일한 기록이다.
- 추출 회귀는 `skim doctor`가 소스별로 잡는다. 판정은 절대 임계가 아니라 그 소스의 지난 120일 대비다 (`source_health.py`).
  분량은 평균이 아니라 중앙값으로 비교한다. 평균은 긴 글 몇 건에 끌려가서, 알파 릴리스 노트 3건이 섞인 LangChain Releases의 짧은 패치 노트가 회귀로 잡혔다 (#46).

### 새 크롤러 추가 방법

1. `packages/skim-core/src/skim_core/crawlers/{type}/` 아래에 크롤러 클래스 생성 (`async crawl(**options) -> List[Post]`)
2. `packages/skim-core/src/skim_core/crawlers/__init__.py`의 `REGISTRY`에 등록
3. Feed 크롤러면 `packages/skim-core/src/skim_core/feed_config.py`에 소스 추가

## 데일리 운영

- `scripts/run_daily_feed.sh`가 하루 한 번 돈다. 이 맥에서는 launchd `com.seungwonan.skim-daily`
  (`~/Library/LaunchAgents/`, 매일 00:02)가 부른다. 등록이 저장소 밖이라 diff에 남지 않으므로
  일정이나 경로를 바꾸면 이 절도 함께 고친다.
- 순서는 네트워크 대기(최대 10분), `skim backup --keep 3`, `crawl all --days 1 --catch-up`,
  지표 백필, GeekNews 토픽 백필, `doctor --strict`다. `data/daily/.run.lock`으로 겹쳐 돌지 않는다.
- 회차 기록은 `data/daily/cron.log`에 `======= start <로컬 시각> =======`부터
  `======= end ... exit=N =======`까지 쌓인다. 10MB를 넘으면 `cron.log.1`로 돌린다.
- `end` 줄의 `exit`는 크롤 결과다(네트워크 대기에서 포기해도 1). 백필과 doctor 결과는
  그 위 `지표 백필 exit=`, `GeekNews 토픽 백필 exit=`, `doctor exit=` 줄에 따로 찍힌다.
  `end` 줄 없이 다음 `start`가 오는 구간은 프로세스가 중간에 죽은 회차다.
- 마지막 회차의 `doctor --strict` 결과는 `data/daily/doctor.txt`에 따로 남는다.
- 손으로 돌린 `skim crawl`은 `cron.log`에 남지 않고 `runs` 테이블에만 남는다.
- plist의 `StandardOutPath`(`~/.local/log/skim-daily.log`)는 스크립트가 출력을 `cron.log`로
  돌리기 전에 죽을 때만 쌓인다. 비어 있는 게 정상이다.
- DB 시각(`runs.started_at`, `posts.crawled_at`)은 UTC이고 `cron.log`는 로컬 시각이다.
  00:02 KST 회차는 DB에 전날 15:02로 찍힌다. `crawled_at`은 처음 저장된 시각이라 upsert가 바꾸지 않는다.
- 상태 점검 절차와 경고 판정은 skim 스킬의 `.claude/skills/skim/references/health-signals.md`에 있다.

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
- 포맷터: `ruff format` 하나 (88자, `pyproject.toml`의 `[tool.ruff]`). 편집 훅도 같은 설정으로 돈다.
  import 정렬도 ruff(`ruff check --fix`, I 규칙만)다. `just lint`가 둘 다 확인한다
- import 줄 끝에 `# pylint: disable=...`를 달지 않는다. 줄이 길면 정렬기가 주석을 괄호 안으로
  옮겨 pylint가 읽지 못한다. 윗줄에 `# pylint: disable-next=...`로 쓴다
- 포맷만 바꾼 커밋은 `.git-blame-ignore-revs`에 적는다. 저장소 전체를 다시 포맷하면 그 커밋을 추가한다
- Swift desktop: `apps/desktop`
- Git hooks: `husky`. pre-commit `just lint`, commit-msg `commitlint`, pre-push `just test && just build`
- 훅은 체크아웃마다 `pnpm install`로 붙는다. 새 worktree에는 `.husky/_`가 없어 훅이 오류 없이
  건너뛰어진다. worktree를 만들면 먼저 `pnpm install`을 돌린다. CI는 PR 커밋 메시지를 다시 검사한다
- 훅 안에서 다른 git 저장소를 다루는 명령(SwiftPM 의존성 checkout 등)을 부르기 전에 `GIT_DIR`을 지운다.
  git이 훅에 넘기는 이 값이 worktree에서는 절대 경로라, 그 명령까지 이 저장소를 본다
- Commit message validation: `commitlint` (`commitlint.config.cjs`, config-conventional).
  한국어 제목을 대문자로 시작하는 영어 단어로 열면 subject-case에 걸린다(`GeekNews 한도를...`,
  `README를...`). 한국어나 소문자로 시작하게 쓴다
