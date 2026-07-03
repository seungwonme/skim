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
- 따라서 추출 완결성은 크롤러의 책임이다. 저장 시점에 링크 원문 본문, 플랫폼 자체 본문(Ask/Show HN 텍스트, GeekNews 한국어 요약), 토론(HN 상위 댓글)까지 채워야 한다. "링크만 저장"은 계약 위반이다.
- 예외는 `--no-content` 명시 실행뿐이며, 그 행은 다음 크롤 upsert로 본문이 채워질 때까지 미완성으로 간주한다.

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
- `apps/desktop/`: SwiftUI desktop reader for local `data/skim.db`

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

- JS/TS: `pnpm` workspace + `turbo` + `biome`
- Python: `uv` workspace
- Swift desktop: `apps/desktop`
- Git hooks: `husky`
- Commit message validation: `commitlint`
