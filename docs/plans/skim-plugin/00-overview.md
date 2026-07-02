# skim Claude Code 플러그인 구현 계획 — Overview

> Current implementation note: the repository now ships the plugin skill at
> `.claude/skills/skim/SKILL.md` and `.agents/skills/skim/SKILL.md` with `.claude-plugin/plugin.json`. This document is a
> historical implementation plan; command examples that mention `uv run --from`
> predate the current uv CLI, where out-of-tree tool execution uses
> `uv tool run --from ...`.

## 목표

1. **`skim research` CLI 추가**: 수집된 posts를 topic/날짜/플랫폼 기준으로 필터해 구조화 JSON 반환
2. **skim을 Claude Code 플러그인(`/skim`)으로 배포**: 사용자가 `uv run --from git+...@v${VERSION}` 기반으로 실행, Claude가 skim을 데이터 소스로 사용 (7차 리뷰 P3-12)

## 설계 원칙

- **skim은 dumb data provider**: 수집·저장·필터만. LLM 분석(planner, rerank, cluster, summary)은 Claude Code 전담
- **자원 재사용**: 기존 crawler REGISTRY, SQLite posts 테이블, enrichment(defuddle/yt-dlp transcript) 파이프라인을 그대로 활용
- **세션 기반**: ScrapeCreators 같은 외부 유료 API 없이 본인 세션(Threads/Reddit/X/LinkedIn)으로 수집
- **최소 침습**: 신규 테이블은 `research_runs` 하나. posts 스키마 변경 없음

## 책임 분담

| 레이어 | 역할 |
|---|---|
| **skim CLI** | 수집, 저장, topic/date/platform 필터, JSON 반환 |
| **Claude Code SKILL.md** | 사용자 대화, topic 분해, 결과 클러스터링·요약·인용 |

## 확정된 결정사항

| # | 항목 | 결정 |
|---|---|---|
| 1 | Topic 검색 방식 | 단순 LIKE (토큰별 AND). FTS5·댓글 검색은 TODO |
| 2 | Refresh 임계값 | `결과 < 5개 OR 최신 post > 6h` → auto 크롤 |
| 3 | 세션 미존재 | 기본 skip + warning. `--sources`로 명시 지정 시 에러 |
| 4 | 슬래시 커맨드 | `/skim TOPIC` 단일 커맨드 (last30days 방식) |
| 5 | 배포 | git install (`uv run --from git+...`) → 안정화 후 PyPI |
| 6 | LLM | skim에 없음. Claude 전담 |
| 7 | 플랫폼 | 무료 feed + 세션 기반 SNS 전부 포함 (Threads/X/LinkedIn/Reddit) |
| 8 | desktop 통합 | v1 이후 |

## Phase 구성

0. **[Phase 0](./00a-phase-0-prerequisites.md)**: Timestamp 정규화 + DB API 시그니처 확정 (Phase 1 진입 전 필수)
1. **[Phase 1](./01-phase-1-research-cli.md)**: `skim research` CLI 기본 구현 (검색·필터·JSON·측정 인프라)
2. **[Phase 2](./02-phase-2-auto-refresh.md)**: `research_runs` 테이블 + auto refresh + thundering herd 방어
3. **[Phase 3](./03-phase-3-plugin-structure.md)**: `.claude-plugin/` 구조 + SKILL.md (버전 고정)
4. **[Phase 4](./04-phase-4-distribution.md)**: `sync-plugin.sh` + 배포 타겟 + 설치 경로 충돌 검증

## 전체 흐름도

```text
User: /skim "nvidia earnings"
          ↓
Claude Code SKILL.md
          ↓
uv run --from "git+https://github.com/seungwonme/skim@v${VERSION}" skim research "nvidia earnings" --days 7 --emit json
          ↓
┌─────────────────────────────────────────────────┐
│ skim research                                   │
│  1. posts 테이블에서 topic 토큰 LIKE 검색       │
│  2. auto refresh 판단                           │
│     - 결과 < 5 OR 최신 > 6h → 크롤 실행         │
│  3. 필터 결과를 JSON으로 stdout 출력            │
│  4. research_runs에 이력 기록                   │
└─────────────────────────────────────────────────┘
          ↓
JSON {topic, date_range, stats, posts[]}
          ↓
Claude가 파싱 → 플랫폼별 그룹 → 클러스터·요약·인용
          ↓
User에게 마크다운 리포트
```

## 성공 기준

**Operational**:
- `uv run skim research "AI video" --emit json` 으로 최근 7일 관련 post 묶음 반환
- `/skim AI video` 슬래시 커맨드로 Claude Code에서 바로 리포트 생성
- 세션 없어도 무료 소스만으로 동작 (HN/GeekNews/arXiv/YouTube/HF/PH/Every.to)
- `skim_research` 관련 pytest 회귀 통과

**Measurable (2차 리뷰 #15 대응)**:
- Phase 0: `posts.timestamp NOT LIKE '____-__-__T%'` 이 0 건
- Phase 1: 2 만 행 규모 DB 기준 search latency p50 < 200ms, p95 < 500ms (stderr 로그로 확인)
- Phase 1: 토큰 2 개 topic 으로 10 회 쿼리 → rows scanned p95 < 5000
- Phase 1: 골든셋 20 개 topic 에 대해 `matched_fields` 중 title/summary 비율 ≥ 40% (raw content-only hit 이 과반이면 substring false positive 의심)
- Phase 2: 동일 topic 연속 5 회 호출 시 refresh 트리거 ≤ 2 회 (backoff 정상 동작)

## 리뷰 대응 기록

### Codex review (2026-04-19)

Codex CLI 리뷰에서 18건 지적 사항 수신. 사용자 결정으로 **스키마 불일치(#1, #2, #4) 만 먼저 수정, 구조는 유지**.

| # | 지적 | 대응 | 위치 |
|---|---|---|---|
| 1 | `content_markdown` 만 검색하면 본문 누락 (실제 NOT NULL 은 `content`) | 검색 필드에 `title + content + content_markdown + summary` 전부 포함 | [01](./01-phase-1-research-cli.md) 검색 로직 |
| 2 | `Post.timestamp` 는 `str` 인데 datetime 연산 시도 | `_parse_iso()` 헬퍼로 `fromisoformat()` 파싱, naive 는 KST fallback | [02](./02-phase-2-auto-refresh.md) refresh 판정 |
| 4 | JSON 스키마에 가상 필드(`engagement.score`, `metadata`) | Post 모델과 1:1 매핑 (flat `likes/comments/reposts/views`, `extra` JSON 파싱) | [01](./01-phase-1-research-cli.md) JSON 스키마 |
| 5 | stopword fallback (최근 posts 덤프) | 토큰 0개면 빈 결과 + warning. 덤프 금지 | [01](./01-phase-1-research-cli.md) Edge Cases |
| 6 | `refresh_platforms` 가 runs 테이블 bookkeeping 우회 | `save_run/update_run_progress/finish_run` 재사용 | [02](./02-phase-2-auto-refresh.md) 크롤 트리거 |
| 7 | concurrency "동일 전략" 주장 거짓 | v1은 동시 실행 방어 안 함 명시, TODO로 남김 | [02](./02-phase-2-auto-refresh.md) Concurrency |
| 8 | Reddit 전체 세션 필수로 묶음 | `--subreddit` 지정 시 세션 불필요. `_reddit_requires_session()` 분기 | [02](./02-phase-2-auto-refresh.md) 세션 체크 |
| 9 | `SKIM_WORKSPACE_ROOT` 없으면 uv 임시 checkout 에 DB 생성됨 | SKILL.md 가 반드시 `export SKIM_WORKSPACE_ROOT=$HOME/.skim` | [03](./03-phase-3-plugin-structure.md) Step 2, 환경변수 |
| 10 | `uvx` vs `uv run --from` 혼재, `LAST30DAYS_PYTHON` cargo cult | `uv run --from` 로 통일, `LAST30DAYS_PYTHON` 제거 | [03](./03-phase-3-plugin-structure.md) Step 2 |
| 11 | first-run wizard 가상 | wizard 삭제, stderr warning 으로 안내 | [03](./03-phase-3-plugin-structure.md) 본문 흐름 |
| 12 | `~/.agents`, `~/.codex` 배포 타겟은 skim 플러그인과 무관 | `~/.claude/plugins/cache/skim` 단일 타겟 | [04](./04-phase-4-distribution.md) sync-plugin.sh |
| 13 | Tauri desktop 버전 bump 강제는 release train 결합 | 플러그인 릴리즈와 desktop 릴리즈 분리 | [04](./04-phase-4-distribution.md) 버전 관리 |
| 14 | exit code 규약 모순 | partial success=0, 명시 세션 누락=1, 전체 실패=2, 알 수 없는 source=2, DB 오류=3 | [02](./02-phase-2-auto-refresh.md) Edge Cases |
| 15 | 스키마 마이그레이션 규율 없음 | `PRAGMA user_version` + `_migrate_research_runs()` 헬퍼 + 컬럼 추가 규약 | [02](./02-phase-2-auto-refresh.md) DB 스키마 |

**미대응 (#3, #16, #17)**: 성능·테스트 범위·성공 기준은 Phase 1 구현 이후 계측 결과 기반으로 재평가.

### Codex review 2차 (2026-04-19 PM)

재리뷰에서 P1 9 건·P2 14 건·P3 3 건 추가 수신. 사용자 결정 **B (전부 반영)** 에 따라 계획 전면 수정.

| # | 지적 | 대응 | 위치 |
|---|---|---|---|
| 2-1 | Phase 1 "의존성 없음" 주장 거짓 — HN Firebase epoch, GeekNews 한국어 상대시간은 ISO 8601 사전순 비교 불가 | **Phase 0 신설**: timestamp 정규화 + 마이그레이션 스크립트 | [00a](./00a-phase-0-prerequisites.md) 전체 |
| 2-2 | Phase 1/2 timestamp 주장 충돌 (사전순 vs KST/UTC 혼재) | `since_iso` UTC 기준 변환, 저장도 UTC 권장 | [00a](./00a-phase-0-prerequisites.md) 규약, [01](./01-phase-1-research-cli.md) 검색 로직 |
| 2-3 | Phase 2 API 시그니처 불일치 — `save_posts(posts)`, `finish_run(run_id, status=)` 은 실제 존재 안 함 | 실측 시그니처 반영: `save_posts(posts, platform)`, `finish_run(run_id, status, posts_count, summary)` | [00a](./00a-phase-0-prerequisites.md) DB API 표, [02](./02-phase-2-auto-refresh.md) refresh_platforms |
| 2-4 | Phase 3 수동 검증이 `git+...@main` hardcode → 로컬 변경 검증 불가 | `SKIM_LOCAL_PATH` override + 태그 기반 실행 (`@v0.3.0`) | [03](./03-phase-3-plugin-structure.md) Step 2 |
| 2-5 | `~/.claude/plugins/local/` 과 `~/.claude/plugins/cache/` 우선순위 미정의 | cache 단일 타겟, local 존재 시 사전 검증 스크립트로 차단 | [03](./03-phase-3-plugin-structure.md) 수동 검증, [04](./04-phase-4-distribution.md) sync 스크립트 |
| 2-6 | `research_runs` 가 thundering herd 방어 안 함 (사후 로깅만) | advisory lock (`data/skim.research.lock`) + `research_runs.status='running'` 사전 체크 | [02](./02-phase-2-auto-refresh.md) Concurrency |
| 2-7 | plugin version 과 실행 CLI 영구 불일치 가능 (`@main` mutable) | 릴리즈 태그 고정 실행, CI 에서 version 4 파일 = git tag 일치 검증 | [04](./04-phase-4-distribution.md) 버전 관리 |
| 2-8 | 7일 window + `<5` 임계값 → niche 토픽 무한 재크롤 루프 | refresh backoff: 최근 30분 내 동일 `(tokens, sources)` 크롤 skip + window 자동 확장 (7→14→30) | [02](./02-phase-2-auto-refresh.md) Refresh 판정, [02](./02-phase-2-auto-refresh.md) 크롤 트리거 |
| 2-9 | LIKE `%`, `_` 이스케이프 누락 | `ESCAPE '\'` 절 추가, 토큰에서 `%`/`_` escape | [01](./01-phase-1-research-cli.md) 검색 로직 |
| 2-10 | 짧은 토큰 substring false positive (`ai` → `said`) | 2글자 이하 토큰 경고 `warnings[]` 수록 | [01](./01-phase-1-research-cli.md) Edge Cases |
| 2-11 | match provenance 없음 → Claude 가 매칭 근거 재발견 | JSON 스키마에 `matched_fields: [...]` 추가 | [01](./01-phase-1-research-cli.md) JSON 스키마 |
| 2-12 | refresh 된 신선 포스트 vs 아카이브 hit 구분 불가 | post 별 `fetched_this_run: bool` 플래그 + 응답 `stats.newly_fetched` | [02](./02-phase-2-auto-refresh.md) 실행 흐름 |
| 2-13 | `/tmp/skim-result.json` 고정 경로 unsafe | `mktemp` per-invocation | [03](./03-phase-3-plugin-structure.md) Step 2 |
| 2-14 | weak source 하나 때문에 전체 `--sources all` 재크롤 | per-platform staleness 판정 + 필요한 플랫폼만 refresh | [02](./02-phase-2-auto-refresh.md) Refresh 판정 |
| 2-15 | 성공 기준이 operational 만 있음 (#17 재지적) | Phase 1 성공 기준에 latency p50/p95, relevance target 포함 | [00-overview](./00-overview.md) 성공 기준, [01](./01-phase-1-research-cli.md) 측정 섹션 |
| 2-16 | 측정 인프라 Phase 1 부재 (#3 재지적) | Phase 1 에 search latency + rows scanned 로깅 의무화 | [01](./01-phase-1-research-cli.md) 측정 섹션 |
| 2-17 | integration layer 테스트 부재 (#16 재지적) | Phase 3 TDD 에 SKILL.md → CLI end-to-end subprocess 테스트 추가 | [03](./03-phase-3-plugin-structure.md) TDD |
| 2-18 | 네트워크 없으면 설치된 플러그인 실행 불가 | `SKIM_LOCAL_PATH` 지원 + 설치 시 `uv tool install` 옵션 문서화 | [03](./03-phase-3-plugin-structure.md), [04](./04-phase-4-distribution.md) |

**P3 미반영 (3건)**: 구절 의미 토큰화, 크로스 플랫폼 dedup, 짧은 토큰 성능 최적화는 Phase 1 측정 결과 기반 v1.1 이후 재평가.

### Codex review 3차 (2026-04-19 저녁)

2차 반영 후 **v3 실측 기반 재리뷰**에서 새 P1 6건, P2 3건, RISK 4건 발견. 전부 반영 (사용자 결정 A).

| # | 지적 | 대응 위치 |
|---|---|---|
| 3-P1-1 | `epoch_to_iso(13_digit_ms)` 가 `/1000` 안 함 | [00a](./00a-phase-0-prerequisites.md) timestamp.py |
| 3-P1-2 | `relative_ko_to_iso` 가 multi-unit (`'1시간 30분 전'`) 누락 | [00a](./00a-phase-0-prerequisites.md) timestamp.py |
| 3-P1-3 | kill -9 후 stale `research_runs.status='running'` row 청소 경로 없음 | [02](./02-phase-2-auto-refresh.md) `_cleanup_stale_research_runs` |
| 3-P1-4 | window 확장이 aggregate 판정, per-platform 아님 | [02](./02-phase-2-auto-refresh.md) `run_with_expansion` per-platform |
| 3-P1-5 | `save_posts` 반환값은 upsert enrichment 포함 → `newly_fetched` overcount | [02](./02-phase-2-auto-refresh.md) pre/post snapshot diff |
| 3-P1-6 | `SKIM_PLUGIN_VERSION="${...:-0.3.0}"` fallback drift | [03](./03-phase-3-plugin-structure.md) SKILL frontmatter 파싱, fallback 제거 |
| 3-P2-1 | Phase 3 에 "uvx 미설치" 잔존 | [03](./03-phase-3-plugin-structure.md) "uv 미설치" 로 정정 |
| 3-P2-2 | 수동 검증 `plugins/local/skim` 이 conflict exit 5 와 충돌 | [03](./03-phase-3-plugin-structure.md) `SKIM_LOCAL_PATH` + sync-plugin.sh 방식 |
| 3-P2-3 | Phase 2 하단 "v1 은 방어 없음" 문장 잔존 | [02](./02-phase-2-auto-refresh.md) 라인 552 업데이트 |
| 3-R1 | `tokens_key`/`sources_key` canonicalization 헬퍼 미정의 | [02](./02-phase-2-auto-refresh.md) `_canonical_key` + TDD |
| 3-R2 | Phase 0 UTC "권장" → KST RSS 허용되어 Phase 1 UTC 전제와 충돌 | [00a](./00a-phase-0-prerequisites.md) UTC 강제로 변경 |
| 3-R3 | `run_with_expansion` 이 `days > 30` 입력 시 UnboundLocalError | [02](./02-phase-2-auto-refresh.md) `_expansion_candidates` 동적 생성 |
| 3-R4 | `refresh_platforms(stale_platforms, days=7)` 하드코드 | [02](./02-phase-2-auto-refresh.md) `days=requested_days` forward |
| 3-Exec | `_filter_by_session`, `_build_crawler_options` 정의 없음 | [02](./02-phase-2-auto-refresh.md) 신규 "헬퍼" 섹션 |

### Codex review 4차 (2026-04-19 심야)

v4 재리뷰. P1 4건, P2 4건, P3 1건 발견. 전부 반영.

| # | 지적 | 대응 |
|---|---|---|
| 4-P1-1 | `_fetch_external_ids` 가 `get_connection` import 없음 | [02](./02-phase-2-auto-refresh.md) 헬퍼 섹션 import 추가 |
| 4-P1-2 | `session_file_exists()` 정의 없음 | [02](./02-phase-2-auto-refresh.md) 헬퍼 정의 + `SESSIONS_DIR` 경로 |
| 4-P1-3 | reddit 홈 피드가 topic 무관 | [02](./02-phase-2-auto-refresh.md) v1 한계 명시 + warning, topic→subreddit 매핑은 v1.1 |
| 4-P1-4 | SKILL `allowed-tools: Write` 불필요 (prompt injection 경로) | [03](./03-phase-3-plugin-structure.md) `Bash, Read` 만 |
| 4-P2-5 | `search_stats` vs `stats.newly_fetched` 이중 스키마 | [01](./01-phase-1-research-cli.md) 단일 권위 `stats` 로 통합 |
| 4-P2-6 | `_fetch_external_ids` 가 플랫폼 풀스캔 2N | [02](./02-phase-2-auto-refresh.md) `_fetch_existing_subset` 로 incoming ID 만 조회 |
| 4-P2-7 | `research_runs.days` 단일 int 로 per-platform 저장 불가 | [02](./02-phase-2-auto-refresh.md) `days_per_platform TEXT JSON` 추가 |
| 4-P2-8 | `_since_utc`, `_merge_by_external_id`, `_build_response`, `ConcurrentResearchError` 미정의 | [02](./02-phase-2-auto-refresh.md) 헬퍼 섹션 전부 정의 |
| 4-P3-9 | 12-digit epoch 누락, digit-기반 분기의 edge | [00a](./00a-phase-0-prerequisites.md) magnitude 기반 (`>= 10**12` → ms) |

### Codex review 5차 (2026-04-19 새벽)

v5 재리뷰. P0 2건, P1 5건, P2 4건. 전부 반영.

| # | 지적 | 대응 |
|---|---|---|
| 5-P0-1 | SQLite `ADD COLUMN IF NOT EXISTS` 는 존재하지 않는 문법 | [02](./02-phase-2-auto-refresh.md) `_ensure_column` (PRAGMA table_info 체크) |
| 5-P0-2 | `_build_response` 시그니처에 `days=` 없는데 호출부가 전달 | [02](./02-phase-2-auto-refresh.md) keyword-only 시그니처 + `days`, `tokens`, `date_range`, `sources_requested` 파라미터 |
| 5-P1-3 | `_build_response` 가 Phase 1 권위 스키마 (7 필드) 깸 | [02](./02-phase-2-auto-refresh.md) `topic/tokens/date_range/sources_requested` 포함 |
| 5-P1-4 | `ConcurrentResearchError` catch 경로 미정의 | [02](./02-phase-2-auto-refresh.md) `run_research` 최상위 try/except (auto→warning, force→exit 4) |
| 5-P1-5 | `research_runs` store API 시그니처/JSON 직렬화 규약 미정의 | [02](./02-phase-2-auto-refresh.md) `store.py API 계약` 섹션 (`record_started/completed/failed`) |
| 5-P1-6 | `_count_by_platform`/`_group_by_platform`/`warnings` import 없음 | [02](./02-phase-2-auto-refresh.md) 헬퍼 정의 + `stdlib_warnings` alias |
| 5-P1-7 | `04:18-25` 가 여전히 `git clone ... plugins/local/` 제시 | [04](./04-phase-4-distribution.md) `claude plugin install` 단일 경로 |
| 5-P2-8 | naive timestamp fallback KST (Phase 0) vs UTC (Phase 2) | [02](./02-phase-2-auto-refresh.md) Phase 0 와 일치하게 KST fallback |
| 5-P2-9 | `days_per_platform` 이 stale 빠진 플랫폼을 7일로 잘못 기록 | [02](./02-phase-2-auto-refresh.md) `max_days_by_platform` per-iteration tracking |
| 5-P2-10 | `SearchStats` 가 Phase 1/2 분리 정의 | [01](./01-phase-1-research-cli.md) `research/types.py` 단일 정의, Phase 2 import |
| 5-P2-11 | `status` 허용값에 `interrupted` 누락 | [02](./02-phase-2-auto-refresh.md) 스키마 주석에 4개 전부 |

### Codex review 6차 (2026-04-19 아침)

v6 재리뷰. P0 없음. P1 3건, P2 3건, P3 1건. 전부 반영.

| # | 지적 | 대응 |
|---|---|---|
| 6-P1-1 | `RESEARCH_RUNS_CREATE_SQL` 미정의 | [02](./02-phase-2-auto-refresh.md) 헬퍼 섹션 상단 상수 선언 + `executescript` |
| 6-P1-2 | `_tokenize`, `DEFAULT_LIMIT`, `_run_with_lock_and_refresh` 미정의 | [02](./02-phase-2-auto-refresh.md) 3개 심볼 전부 정의 |
| 6-P1-3 | Exit code 1/2/3 경로 + CLI 어댑터 미정의 | [02](./02-phase-2-auto-refresh.md) `NoSessionError/AllPlatformsFailedError/DbWriteError` + CLI `research` 커맨드 |
| 6-P2-4 | `refresh_platforms` 가 `research_runs` 기록 안 함 | [02](./02-phase-2-auto-refresh.md) `store.record_started/failed` 호출 추가 |
| 6-P2-5 | Phase 3 ↔ Phase 4 conflict 규칙 불일치 | [03](./03-phase-3-plugin-structure.md) local 단독 존재도 exit 5 로 통일 |
| 6-P2-6 | `date_range.from` 이 max expanded window 로 과대표현 | [02](./02-phase-2-auto-refresh.md) `base_since` (requested_days 기준) 유지, per-platform 은 `stats.days_per_platform` |
| 6-P3-7 | Migration TDD 3건 + run_research exit code TDD 누락 | [02](./02-phase-2-auto-refresh.md) TDD 12건 추가 |

### Codex review 7차 (2026-04-20)

v6 반영본 대상 재리뷰. P0 2건, P1 6건, P2 3건, P3 1건. 전부 반영.

| # | 지적 | 대응 |
|---|---|---|
| 7-P0-1 | `run_with_expansion` 이 `refresh_platforms` 를 호출하지 않음 (auto-refresh 실질 미동작) | [02](./02-phase-2-auto-refresh.md) `run_with_expansion` 재작성 + 실행 흐름 섹션 재작도 |
| 7-P0-2 | `store.record_completed` 가 실행 경로에서 호출 안 됨 → `research_runs.status` 영구 `running` | [02](./02-phase-2-auto-refresh.md) `run_with_expansion` 종결 시점에 `store.record_completed` 호출 |
| 7-P1-3 | `_has_running` 미정의 | [02](./02-phase-2-auto-refresh.md) 헬퍼 섹션에 정의 추가 |
| 7-P1-4 | `store` 모듈 import 누락 | [02](./02-phase-2-auto-refresh.md) import 블록 보강 + 중복 에러 클래스 정의 제거 |
| 7-P1-5 | `_filter_by_session` 이 `SystemExit` raise → `NoSessionError` 기준 exit code 경로 단절 | [02](./02-phase-2-auto-refresh.md) `NoSessionError` 로 교체 + 에러 클래스를 상단 import 섹션으로 이동 |
| 7-P1-6 | Phase 0 `_REL_KO` 정규식 미정의인데 geeknews 가 import | [00a](./00a-phase-0-prerequisites.md) `_REL_KO` 통합 패턴 정의 + TDD 2건 |
| 7-P1-7 | Phase 3 bash `$0` / `$TOPIC` 대입 누락 (slash command 환경 미정의) | [03](./03-phase-3-plugin-structure.md) Step 2 bash 재작성 — `TOPIC="${ARGUMENTS:-}"`, `SKIM_PLUGIN_VERSION` Claude 가 Read 로 주입 |
| 7-P1-8 | `--limit` CLI 옵션이 `run_research` 로 forward 안 됨 | [02](./02-phase-2-auto-refresh.md) `run_research(limit=...)` + `_run_with_lock_and_refresh(limit=...)` + CLI 어댑터 forward |
| 7-P2-9 | `_cleanup_stale_research_runs` 의 `row["..."]` 접근은 row_factory 계약 미문서화 | [02](./02-phase-2-auto-refresh.md) docstring 에 `get_connection()` 경유 전제 명시 |
| 7-P2-10 | Phase 1 JSON 예시 블록에 한국어 문단 삽입 + `date_range` 가 `+09:00` (UTC 규약 충돌) | [01](./01-phase-1-research-cli.md) JSON 블록 분리 + 예시 전부 UTC 로 교체 |
| 7-P2-11 | 경로 충돌 exit code — Phase 3 preflight 는 exit 5, Phase 4 sync 는 exit 2 | [04](./04-phase-4-distribution.md) sync-plugin.sh 와 TDD 항목을 exit 5 로 통일 |
| 7-P3-12 | Phase 3 서두가 여전히 `uvx` 전제 | [03](./03-phase-3-plugin-structure.md) 서두 교체 + 00-overview 목표·흐름도 동기화 |

### Codex review 8차 (2026-04-20)

v7 반영본 대상 재리뷰. 7차 12건 전부 clean 회귀 확인. 신규 P1 1건, P3 2건만 잔존.

| # | 지적 | 대응 |
|---|---|---|
| 8-P1-1 | `Top-level 진입점` 코드 블록이 `from skim_core.research.refresh import ...` 로 self-import | [02](./02-phase-2-auto-refresh.md) import 4줄 제거, 주석으로 "상단 import 섹션 이미 정의" 명시 |
| 8-P3-2 | 플러그인 레이아웃 주석에 `# 실제 CLI (uvx 대상)` 잔재 | [03](./03-phase-3-plugin-structure.md) `# 실제 CLI (uv run --from 대상)` 로 교체 |
| 8-P3-3 | Phase 4 TODO 에 `uvx skim research ...` 잔재 | [04](./04-phase-4-distribution.md) `skim research ... 직접 실행 (uv tool install skim 경유)` 로 교체 |

### Codex review 9차 (2026-04-20)

v8 반영본 재리뷰. 7점 중 4점 clean, P1 1건 + P2 1건 + P3 2건 잔존. 전부 반영.

| # | 지적 | 대응 |
|---|---|---|
| 9-P1-6 | Phase 3 bash 의 SKIM_PLUGIN_VERSION 주입이 Claude 실행 행동에 의존 (runtime 신뢰성 약함) | [03](./03-phase-3-plugin-structure.md) step 2 선행 작업 지시 강화 + 사용자 shell rc 수동 export 경로 문서화 + end-to-end 테스트 회귀로 보강 |
| 9-P2-3 | `research_runs` 기록 정책 문서화 부족 (read-only search 는 row 미생성) | [02](./02-phase-2-auto-refresh.md) store 섹션에 "refresh attempt log only" 정책 명시 + TDD 1건 |
| 9-P3-4 | `within_backoff` 이 `completed` 만 체크한다는 의도 미문서화 | [02](./02-phase-2-auto-refresh.md) docstring 1문장 + TDD 1건 |
| 9-P3-7 | Phase 0 `_REL_KO` 의 suffix text case (`'3시간 전 작성'`) 테스트 누락 | [00a](./00a-phase-0-prerequisites.md) TDD 1건 추가 |
