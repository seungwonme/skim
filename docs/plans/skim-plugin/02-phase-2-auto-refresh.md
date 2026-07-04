# Phase 2 — `research_runs` 테이블 + auto refresh 로직

## 목표

`--refresh auto|never|force` 플래그로 Phase 1의 정적 검색에 **on-demand 크롤**을 결합한다. 데이터 부족·stale 판정 시 자동 크롤 후 재검색한다.

## 산출물

- `packages/skim-core/src/skim_core/research/refresh.py` — 임계값 판정 + 크롤 트리거
- `packages/skim-core/src/skim_core/research/store.py` — `research_runs` CRUD (아래 "store.py API 계약" 참조)
- DB 스키마 v2 마이그레이션 (`db.py`)
- `tests/test_research_refresh.py`
- `tests/test_research_runs_store.py`

## DB 스키마 추가 & 마이그레이션 규율 (codex review #15 대응)

기존 `init_db()` 는 `CREATE TABLE IF NOT EXISTS` + `runs` 테이블에 대한 ad-hoc 컬럼 패치만 수행한다. `research_runs` 도 동일 패턴 적용하되, 향후 컬럼 추가 가능성을 고려해 최소 규율을 도입:

1. 모든 신규 컬럼은 **`PRAGMA table_info` 로 존재 여부 확인 후 조건부 `ALTER TABLE ADD COLUMN`** (5차 리뷰 P0-1). SQLite 는 `ADD COLUMN IF NOT EXISTS` 문법 미지원 (3.43 포함)
2. 스키마 버전은 `PRAGMA user_version` 으로 추적. v1 진입 시 `PRAGMA user_version = 1`
3. `db.py` 에 `_migrate_research_runs(conn)` 헬퍼 추가 — `init_db()` 가 호출
4. 삭제 / 타입 변경은 v1 에선 금지. 필요하면 v2 에서 별도 마이그레이션 함수로

```python
RESEARCH_RUNS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS research_runs (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  topic             TEXT NOT NULL,
  tokens_key        TEXT NOT NULL,
  sources_key       TEXT NOT NULL,
  refresh_mode      TEXT NOT NULL,
  days_requested    INTEGER NOT NULL,
  days_per_platform TEXT NOT NULL DEFAULT '{}',
  window_expanded   INTEGER NOT NULL DEFAULT 0,
  result_count      INTEGER NOT NULL DEFAULT 0,
  newly_fetched     INTEGER NOT NULL DEFAULT 0,
  crawled_platforms TEXT NOT NULL DEFAULT '[]',
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  status            TEXT NOT NULL,
  runner_pid        INTEGER,
  runner_host       TEXT,
  error_message     TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_runs_topic ON research_runs(topic);
CREATE INDEX IF NOT EXISTS idx_research_runs_started_at ON research_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_research_runs_backoff ON research_runs(tokens_key, sources_key, started_at);
CREATE INDEX IF NOT EXISTS idx_research_runs_status ON research_runs(status);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """SQLite ADD COLUMN IF NOT EXISTS 대체. row_factory 와 무관하게 동작 (6차 리뷰)."""
    # PRAGMA table_info 은 (cid, name, type, notnull, dflt_value, pk) 튜플. 인덱스 1 이 name.
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_research_runs(conn: sqlite3.Connection) -> None:
    """멱등 migration. fresh DB / 기존 v0 DB / 이미 v1 DB 모두 안전 (6차 리뷰 P3)."""
    conn.executescript(RESEARCH_RUNS_CREATE_SQL)
    # 기존 research_runs 가 있지만 새 컬럼이 없는 경우 대비
    _ensure_column(conn, "research_runs", "days_per_platform", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "research_runs", "window_expanded", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "research_runs", "newly_fetched", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "research_runs", "runner_pid", "INTEGER")
    _ensure_column(conn, "research_runs", "runner_host", "TEXT")
    if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
        conn.execute("PRAGMA user_version = 1")
    conn.commit()
```

```sql
CREATE TABLE IF NOT EXISTS research_runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  topic           TEXT NOT NULL,
  tokens_key      TEXT NOT NULL,             -- 정렬된 tokens JSON (backoff lookup 용)
  sources_key     TEXT NOT NULL,             -- 정렬된 sources JSON (backoff lookup 용)
  refresh_mode    TEXT NOT NULL,             -- 'auto'|'never'|'force'
  days_requested  INTEGER NOT NULL,          -- 사용자 요청 --days 값 (감사용)
  days_per_platform TEXT NOT NULL DEFAULT '{}', -- JSON {"reddit": 30, ...} (4차 리뷰 P2-7)
  window_expanded INTEGER NOT NULL DEFAULT 0,-- 확장 횟수 (0/1/2) — aggregate 요약 지표
  result_count    INTEGER NOT NULL,
  newly_fetched   INTEGER NOT NULL DEFAULT 0,-- 이 run 에서 새로 수집된 post 수
  crawled_platforms TEXT,                    -- JSON array (실제 크롤된 목록)
  started_at      TEXT NOT NULL,             -- UTC ISO 8601
  finished_at     TEXT,
  status          TEXT NOT NULL,             -- 'running'|'completed'|'failed'|'interrupted' (5차 리뷰 P2-11)
  runner_pid      INTEGER,                   -- thundering herd 방어 용
  runner_host     TEXT,
  error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_research_runs_topic ON research_runs(topic);
CREATE INDEX IF NOT EXISTS idx_research_runs_started_at ON research_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_research_runs_backoff ON research_runs(tokens_key, sources_key, started_at);
CREATE INDEX IF NOT EXISTS idx_research_runs_status ON research_runs(status);
```

**설계 변경 (2차 리뷰 #2-6, #2-8, #2-12 대응)**:
- `tokens_key`, `sources_key`: 정렬된 JSON. backoff lookup 에서 동일 쿼리 판정
- `window_expanded`: auto refresh 가 `days` 확장 중인지 추적 (무한 루프 방지)
- `newly_fetched`: refresh 후 JSON 응답의 `stats.newly_fetched` 로 노출
- `status='running'`: thundering herd 방어. `runner_pid`/`runner_host` 로 stale run 판정 가능

**주의**: `posts` 테이블은 건드리지 않는다. `research_runs`는 이력 조회용일 뿐 Claude 측 클러스터/요약은 저장 안 함.

## `store.py` API 계약 (5차 리뷰 P1-5)

`research_runs` 테이블 전용 CRUD. JSON 직렬화 규약을 한 곳에서 캡슐화.

```python
# packages/skim-core/src/skim_core/research/store.py
import json
import os
import socket
from datetime import datetime, timezone
from typing import Optional

from skim_core.db import get_connection

UTC = timezone.utc


def record_started(
    *,
    topic: str,
    tokens_key: str,
    sources_key: str,
    refresh_mode: str,
    days_requested: int,
) -> int:
    """running 상태로 INSERT. id 반환."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO research_runs
           (topic, tokens_key, sources_key, refresh_mode, days_requested,
            days_per_platform, window_expanded, newly_fetched, result_count,
            crawled_platforms, started_at, status, runner_pid, runner_host)
           VALUES (?, ?, ?, ?, ?, '{}', 0, 0, 0, '[]', ?, 'running', ?, ?)""",
        (topic, tokens_key, sources_key, refresh_mode, days_requested,
         datetime.now(UTC).isoformat(), os.getpid(), socket.gethostname()),
    )
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return run_id


def record_completed(
    run_id: int,
    *,
    result_count: int,
    newly_fetched: int,
    crawled_platforms: list[str],
    days_per_platform: dict[str, int],
    window_expanded: int,
) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE research_runs SET
               status = 'completed',
               finished_at = ?,
               result_count = ?,
               newly_fetched = ?,
               crawled_platforms = ?,
               days_per_platform = ?,
               window_expanded = ?
           WHERE id = ?""",
        (datetime.now(UTC).isoformat(), result_count, newly_fetched,
         json.dumps(crawled_platforms), json.dumps(days_per_platform),
         window_expanded, run_id),
    )
    conn.commit()
    conn.close()


def record_failed(run_id: int, error_message: str) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE research_runs SET status='failed', finished_at=?, error_message=?
           WHERE id=?""",
        (datetime.now(UTC).isoformat(), error_message[:500], run_id),
    )
    conn.commit()
    conn.close()


def load_days_per_platform(run_id: int) -> dict[str, int]:
    """읽기 시 JSON 역직렬화."""
    conn = get_connection()
    row = conn.execute(
        "SELECT days_per_platform FROM research_runs WHERE id = ?", (run_id,)
    ).fetchone()
    conn.close()
    return json.loads(row[0]) if row else {}
```

**불변식**:
- `days_per_platform` 은 항상 JSON object. 빈 값은 `'{}'`
- `crawled_platforms` 은 항상 JSON array. 빈 값은 `'[]'`
- `status` 값: `'running' | 'completed' | 'failed' | 'interrupted'` (4개)

**기록 정책 (9차 리뷰 P2-3)**: `research_runs` 는 **refresh attempt log** 이지 every-query log 가 아니다. 다음 경로는 row 를 생성하지 않는다.
- `refresh_mode = 'never'`
- stale 플랫폼 없음 (auto 모드에서 1차 검색이 충분)
- backoff cached hit (within_backoff 이 True)

즉 "읽기 전용 검색" 은 `research_runs` 에 흔적을 남기지 않는다. Claude Code 측에서 query audit 를 원하면 stderr `[skim research]` 라인을 수집하거나 별도 query log 를 추가해야 한다. v1 에서는 이 정책을 유지하고, audit 필요 시 v1.1 에서 별도 `query_runs` 테이블 검토.

`refresh_platforms` 는 기존 `runs` 테이블 (`save_run`/`finish_run`) + 새 `research_runs` (`record_started`/`record_completed`) 를 **둘 다** 기록. 이전 계획의 "runs 만 사용" 은 P1-5 지적대로 research 맥락 손실이라 바로잡음.

## 헬퍼 (3차 리뷰 executability + RISK-01 대응)

Phase 2 전용 헬퍼 시그니처. 실제 구현 파일: `packages/skim-core/src/skim_core/research/refresh.py`.

```python
import fcntl
import json
import os
import socket
import sqlite3
import warnings as stdlib_warnings
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from skim_core.crawlers import REGISTRY
from skim_core.db import get_connection, save_run, update_run_progress, finish_run, save_posts
from skim_core.paths import workspace_root
from skim_core.research import store  # 7차 리뷰 P1-4: store 모듈 import 누락 수정
from skim_core.research.search import search_posts  # _run_with_lock_and_refresh 가 재검색에 사용
from skim_core.research.types import SearchStats  # 단일 정의 위치 (5차 리뷰 P2-10)

UTC = timezone.utc


class ConcurrentResearchError(RuntimeError):
    """다른 프로세스가 research_lock 을 잡고 있을 때 raise (5차 리뷰 P1-4).

    top-level 실행 흐름에서 catch:
      - `--refresh auto`: warning 추가 후 initial search 결과 반환 (exit 0)
      - `--refresh force`: exit 4
    """


class NoSessionError(RuntimeError):
    """명시 --sources 인데 세션 없을 때 raise. exit 1 매핑 (7차 리뷰 P1-5).

    `_filter_by_session(explicit=True)` 가 이 예외를 raise 하고, `run_research`
    최상위가 catch → exit 1. 이전 버전은 `SystemExit` 였는데 exit code 매핑이
    `NoSessionError` 기준이라 경로 단절이었음.
    """


class AllPlatformsFailedError(RuntimeError):
    """모든 refresh 타겟 크롤 실패. exit 2 매핑."""


class DbWriteError(RuntimeError):
    """DB 파일 쓰기 실패. exit 3 매핑."""


def _count_by_platform(posts: list[dict]) -> dict[str, int]:
    return dict(Counter(p["platform"] for p in posts))


def _group_by_platform(posts: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        result[p["platform"]].append(p)
    return dict(result)


def _since_utc(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _merge_by_external_id(base: list[dict], extra: list[dict]) -> list[dict]:
    """(platform, external_id) 키로 dedup 하며 병합. extra 는 새 것 뒤에 append."""
    seen = {(r["platform"], r.get("external_id")) for r in base}
    merged = list(base)
    for r in extra:
        key = (r["platform"], r.get("external_id"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(r)
    return merged


def _build_response(
    *,
    topic: str,
    tokens: list[str],
    date_range: dict[str, str],
    sources_requested: list[str],
    posts: list[dict],
    search_stats: SearchStats,
    days: int,
    window_expanded: int = 0,
    days_per_platform: dict[str, int] | None = None,
    newly_fetched_ids: set[tuple[str, str]] | None = None,
    warnings_list: list[str] | None = None,
) -> dict:
    """최종 JSON 응답 빌드. Phase 1 단일 권위 스키마 준수 (5차 리뷰 P1-3).

    최상위 7 필드: topic, tokens, date_range, sources_requested, posts, stats, warnings.
    `days` 는 `stats.days_requested` 로 기록.
    """
    newly_fetched_ids = newly_fetched_ids or set()
    for p in posts:
        p["fetched_this_run"] = (p["platform"], p.get("external_id")) in newly_fetched_ids
    return {
        "topic": topic,
        "tokens": tokens,
        "date_range": date_range,
        "sources_requested": sources_requested,
        "posts": posts,
        "stats": {
            "total": len(posts),
            "by_platform": _count_by_platform(posts),
            "rows_scanned": search_stats.rows_scanned,
            "rows_returned": search_stats.rows_returned,
            "latency_ms": search_stats.latency_ms,
            "short_tokens": search_stats.short_tokens,
            "days_requested": days,
            "window_expanded": window_expanded,
            "days_per_platform": days_per_platform or {},
            "newly_fetched": len(newly_fetched_ids),
        },
        "warnings": list(warnings_list or []),
    }


def _canonical_key(items: list[str]) -> str:
    """정렬 + 중복 제거 + JSON 직렬화. tokens_key, sources_key 공용."""
    return json.dumps(sorted(set(items)), ensure_ascii=False)


def session_file_exists(platform: str) -> bool:
    """data/sessions/{platform}_session.json 존재 여부 (4차 리뷰 P1-2)."""
    return (workspace_root() / "data" / "sessions" / f"{platform}_session.json").exists()


def _reddit_requires_session(options: dict) -> bool:
    """subreddit 지정 없으면 홈 피드 → 세션 필요."""
    return not options.get("subreddit")


def _resolve_sources(requested: list[str]) -> list[str]:
    """'all' 또는 REGISTRY 검증된 플랫폼 목록으로 전개."""
    if not requested or requested == ["all"]:
        return list(REGISTRY.keys())
    unknown = [p for p in requested if p not in REGISTRY]
    if unknown:
        raise SystemExit(f"[skim] unknown sources: {unknown}")
    return list(requested)


def _filter_by_session(
    platforms: list[str],
    *,
    explicit: bool,
    options_by_platform: dict[str, dict] | None = None,
) -> tuple[list[str], list[str]]:
    """세션 필요한데 없는 플랫폼 제거. explicit 모드는 NoSessionError 로 변환 (7차 리뷰 P1-5).

    Returns: (kept, skipped)
    """
    options_by_platform = options_by_platform or {}
    kept, skipped = [], []
    for p in platforms:
        needs_session = p in {"threads", "x", "linkedin"}
        if p == "reddit":
            needs_session = _reddit_requires_session(options_by_platform.get("reddit", {}))
        if needs_session and not session_file_exists(p):
            if explicit:
                raise NoSessionError(f"{p}: no session. Run `uv run skim login {p}` first.")
            stdlib_warnings.warn(f"[skim] {p}: no session file, skipped. Run `skim login {p}`")
            skipped.append(p)
            continue
        kept.append(p)
    return kept, skipped


def _has_running(conn: sqlite3.Connection, tokens_key: str, sources_key: str) -> bool:
    """동일 (tokens, sources) 로 현재 status='running' row 존재 여부 (7차 리뷰 P1-3).

    `_cleanup_stale_research_runs` 뒤에 호출 — 살아있는 PID 만 running 으로 남음.
    connection 은 `get_connection()` 기반 (row_factory=sqlite3.Row 전제).
    """
    row = conn.execute(
        """SELECT 1 FROM research_runs
           WHERE tokens_key = ? AND sources_key = ? AND status = 'running'
           LIMIT 1""",
        (tokens_key, sources_key),
    ).fetchone()
    return row is not None


def _build_crawler_options(platform: str, days: int) -> dict:
    """플랫폼별 crawler.crawl(**options) 인자 빌드.

    Feed 크롤러: since=<ISO>, no_content=False
    API 크롤러: count=<int>  (days 기반 추정)
    Reddit: subreddit 미지정 → 세션 기반 홈 피드. topic 기반 subreddit 매핑은
    v1.1 의 별도 작업 (4차 리뷰 P1-3). v1 에서는 세션 있는 사용자만 reddit 재크롤
    가능하며, auto refresh 로 크롤된 홈 피드가 topic 과 무관할 수 있음을
    warnings[] 에 명시.
    """
    since_iso = _since_utc(days)
    if platform in {"hackernews", "geeknews", "youtube", "producthunt",
                    "arxiv", "huggingface", "everyto"}:
        return {"since": since_iso, "no_content": False}
    if platform in {"threads", "x", "linkedin"}:
        return {"count": max(30, days * 10)}
    if platform == "reddit":
        # 홈 피드 모드. topic-aware subreddit 매핑은 v1.1.
        return {"count": max(30, days * 10), "sort": "hot"}
    raise ValueError(f"no options mapping for platform: {platform}")
```

### Reddit topic-relevance 한계 (4차 리뷰 P1-3)

v1 의 reddit auto-refresh 는 세션 기반 **홈 피드** 를 크롤한다. topic 과 무관한 피드일 수 있음:

- `--sources reddit` explicit + 세션 없음 → exit 1 (기존 유지)
- `--sources reddit` explicit + 세션 있음 → 홈 피드 크롤 + warning `"reddit refresh fetched home feed, not topic-specific"`
- `--sources all` auto + reddit 세션 없음 → skip (기존 유지)
- `--sources all` auto + reddit 세션 있음 → 홈 피드 크롤 + 동일 warning

v1.1: topic → subreddit 매핑 테이블 또는 `--subreddit` 플래그 추가.

## Refresh 판정 로직

Phase 0 에서 전 row 가 **UTC ISO 8601** 로 정규화되었다는 전제. naive fallback 은 방어 코드로만 유지.

```python
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
AUTO_MIN_RESULTS = 5
AUTO_MAX_STALENESS_HOURS = 6
BACKOFF_WINDOW_MINUTES = 30    # 동일 쿼리 재크롤 방지 (2차 리뷰 #2-8)
WINDOW_EXPANSION = [7, 14, 30] # niche topic 자동 확장 (2차 리뷰 #2-8)


def _parse_iso(ts: str) -> datetime | None:
    """Phase 0 정규화 전제. 실패는 데이터 품질 이슈로 기록."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Phase 0 가 KST fallback 과 일치 (5차 리뷰 P2-8). Phase 0 이후엔 모든 row UTC
        dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
    return dt.astimezone(UTC)


def should_refresh_per_platform(
    results: list[dict],
    refresh_mode: str,
    requested_sources: list[str],
) -> list[str]:
    """플랫폼 단위로 refresh 필요 여부 판정 (2차 리뷰 #2-14).

    Returns:
        실제로 refresh 필요한 플랫폼 목록. force 면 전체, never 면 빈 리스트.
    """
    if refresh_mode == "never":
        return []
    if refresh_mode == "force":
        return list(requested_sources)

    # auto: per-platform 판정
    now = datetime.now(UTC)
    stale_platforms: list[str] = []
    by_platform = _group_by_platform(results)
    for platform in requested_sources:
        posts = by_platform.get(platform, [])
        if len(posts) < AUTO_MIN_RESULTS:
            stale_platforms.append(platform)
            continue
        parsed = [dt for dt in (_parse_iso(p["timestamp"]) for p in posts) if dt]
        if not parsed:
            stale_platforms.append(platform)
            continue
        if now - max(parsed) > timedelta(hours=AUTO_MAX_STALENESS_HOURS):
            stale_platforms.append(platform)
    return stale_platforms


def within_backoff(
    conn: sqlite3.Connection,
    tokens_key: str,
    sources_key: str,
    window_minutes: int = BACKOFF_WINDOW_MINUTES,
) -> bool:
    """최근 window 내 동일 (tokens, sources) 쿼리로 완료된 run 존재 여부.

    9차 리뷰 P3-4: `status='completed'` 만 체크. `interrupted` / `failed` 는 제외.
    비정상 종료된 run 은 "fresh data 확보 성공" 이 아니므로 backoff 근거로 쓰면
    안 되고, kill -9 직후 즉시 재시도가 가능해야 한다.
    """
    cutoff = (datetime.now(UTC) - timedelta(minutes=window_minutes)).isoformat()
    row = conn.execute(
        """SELECT 1 FROM research_runs
           WHERE tokens_key = ? AND sources_key = ?
             AND status = 'completed' AND started_at >= ?
           LIMIT 1""",
        (tokens_key, sources_key, cutoff),
    ).fetchone()
    return row is not None
```

### Window 자동 확장 (3차 리뷰 P1-4, RISK-03 대응)

per-platform 부족한 플랫폼에만 확장 적용. aggregate 기준 판정 금지. `days > 30` 입력 시 UnboundLocalError 방지 위해 동적으로 후보 생성.

```python
DEFAULT_WINDOW_EXPANSION = [7, 14, 30]
MAX_EXPANSION_STEPS = 2


def _expansion_candidates(requested_days: int) -> list[int]:
    """requested_days 이상인 확장 후보. 항상 max candidate 보장 (RISK-03)."""
    candidates = [d for d in DEFAULT_WINDOW_EXPANSION if d > requested_days]
    if not candidates:
        return [requested_days]  # 이미 최대 이상
    return [requested_days] + candidates


async def run_with_expansion(
    *,
    topic: str,
    tokens: list[str],
    sources: list[str],
    requested_days: int,
    limit: int,
    refresh_mode: str,
    explicit: bool,
    tokens_key: str,
    sources_key: str,
    warnings_list: list[str] | None = None,
) -> dict:
    """1차 검색 → 필요 시 refresh_platforms 호출 → 재검색 → per-platform window 확장 → record_completed.

    7차 리뷰 P0-1/P0-2 대응: 이전 버전은 `refresh_platforms` 를 호출하지 않아 auto-refresh
    가 동작하지 않았고, `store.record_completed` 가 빠져 `research_runs.status` 가 영구
    `running` 으로 남았다. 이 함수가 실행 경로의 유일한 종결자.
    """
    warnings_list = warnings_list if warnings_list is not None else []
    candidates = _expansion_candidates(requested_days)
    base_days = candidates[0]
    base_since = _since_utc(base_days)
    # 각 플랫폼이 실제로 검색된 최대 days 추적 (5차 리뷰 P2-9)
    max_days_by_platform: dict[str, int] = {p: base_days for p in sources}

    results, stats = search_posts(topic, base_since, sources, limit)
    now_iso = datetime.now(UTC).isoformat()

    # `never` 모드는 refresh/expansion 없이 바로 반환
    if refresh_mode == "never":
        return _build_response(
            topic=topic, tokens=tokens,
            date_range={"from": base_since, "to": now_iso},
            sources_requested=sources,
            posts=results, search_stats=stats,
            days=requested_days, window_expanded=0,
            days_per_platform=max_days_by_platform,
            warnings_list=warnings_list,
        )

    # 1차 stale 판정 → refresh_platforms 호출
    stale = should_refresh_per_platform(results, refresh_mode, sources)
    research_run_id: int | None = None
    newly_fetched_ids: set[tuple[str, str]] = set()
    crawled_total: list[str] = []
    window_expanded = 0

    if stale:
        try:
            crawled, _newly_count, inserted_ids, research_run_id = await refresh_platforms(
                stale, requested_days, explicit,
                topic=topic, tokens_key=tokens_key, sources_key=sources_key,
                refresh_mode=refresh_mode,
            )
        except AllPlatformsFailedError:
            raise
        except NoSessionError:
            raise
        crawled_total.extend(crawled)
        newly_fetched_ids.update(inserted_ids)
        # 재검색 — 신규 posts 반영
        results, stats = search_posts(topic, base_since, sources, limit)
        stale = should_refresh_per_platform(results, refresh_mode, sources)

    # per-platform window 확장: 여전히 stale 한 플랫폼만 넓은 window 로 병합 검색
    for candidate_days in candidates[1:]:
        if not stale:
            break
        if window_expanded >= MAX_EXPANSION_STEPS:
            break
        window_expanded += 1
        extra, extra_stats = search_posts(topic, _since_utc(candidate_days), stale, limit)
        for p in stale:
            max_days_by_platform[p] = candidate_days
        results = _merge_by_external_id(results, extra)
        stats.rows_scanned += extra_stats.rows_scanned
        stats.rows_returned = len(results)
        stale = should_refresh_per_platform(results, "auto", stale)

    if stale:
        warnings_list.append(f"window expanded but still <5 per-platform results: {stale}")

    # research_runs 종결 (7차 리뷰 P0-2): refresh 가 실제 일어났던 경우만 완료 기록
    if research_run_id is not None:
        try:
            store.record_completed(
                research_run_id,
                result_count=len(results),
                newly_fetched=len(newly_fetched_ids),
                crawled_platforms=crawled_total,
                days_per_platform=max_days_by_platform,
                window_expanded=window_expanded,
            )
        except Exception as exc:  # pylint: disable=broad-except
            stdlib_warnings.warn(f"record_completed failed: {exc}")

    # top-level date_range 는 requested_days 기준 유지 (6차 리뷰 P2-6)
    return _build_response(
        topic=topic, tokens=tokens,
        date_range={"from": base_since, "to": datetime.now(UTC).isoformat()},
        sources_requested=sources,
        posts=results, search_stats=stats,
        days=requested_days, window_expanded=window_expanded,
        days_per_platform=max_days_by_platform,
        newly_fetched_ids=newly_fetched_ids,
        warnings_list=warnings_list,
    )
```

**per-platform 확장 의미**: fresh 플랫폼은 `days=7` 결과 유지, stale 플랫폼만 14d/30d 로 확장 병합. `days_per_platform` 필드로 응답에 노출.

## 크롤 트리거 로직

**기존 `runs` 테이블 bookkeeping 재사용 + Phase 0 실측 시그니처 적용** (2차 리뷰 #2-3 대응).

```python
from skim_core.db import save_run, update_run_progress, finish_run, save_posts
from skim_core.crawlers import REGISTRY


async def refresh_platforms(
    platforms_to_refresh: list[str],
    days: int,
    explicit: bool,
    *,
    topic: str,
    tokens_key: str,
    sources_key: str,
    refresh_mode: str,
) -> tuple[list[str], int, set[tuple[str, str]], int]:
    """
    필요한 플랫폼을 크롤. 세션 없는 플랫폼은 skip (explicit=True면 에러).

    Args:
        platforms_to_refresh: per-platform staleness 판정 결과
        days: 사용자가 요청한 --days 값 그대로 forward (3차 리뷰 RISK-04)
        explicit: 사용자가 --sources 로 명시 지정했는지.
    Returns:
        (실제 크롤된 플랫폼, 신규 insert 수, 신규 insert 된 (platform, external_id) 집합)

    newly_fetched 는 save_posts 반환값이 아니라 **pre/post snapshot diff** 로 계산
    (3차 리뷰 P1-5): save_posts 의 ON CONFLICT DO UPDATE 는 기존 row enrichment 시에도
    changes() > 0 를 반환하므로 반환값이 insert 수를 의미하지 않음.
    """
    available, skipped = _filter_by_session(platforms_to_refresh, explicit=explicit)
    if not available:
        return [], 0, set(), 0

    # 두 테이블 동시 기록 (6차 리뷰 P2-4)
    run_id = save_run()  # 기존 runs 테이블 (플랫폼 별 진행상황용)
    research_run_id = store.record_started(
        topic=topic, tokens_key=tokens_key, sources_key=sources_key,
        refresh_mode=refresh_mode, days_requested=days,
    )
    crawled: list[str] = []
    inserted_ids: set[tuple[str, str]] = set()
    try:
        for platform in available:
            update_run_progress(run_id, platform, f"research refresh: {platform}")
            try:
                options = _build_crawler_options(platform, days)
                posts = await REGISTRY[platform]().crawl(**options)
                # incoming post 의 external_id 집합 계산
                incoming = {_post_external_id(p, platform) for p in posts}
                # 이 중 이미 존재하는 것을 조회 (incoming 개수만큼만 스캔, P2-6)
                existed = _fetch_existing_subset(platform, incoming)
                new_ids = incoming - existed
                # 실측 시그니처: save_posts(posts, platform, source=None)
                save_posts(posts, platform)
                inserted_ids.update({(platform, eid) for eid in new_ids})
                crawled.append(platform)
            except Exception as exc:  # pylint: disable=broad-except
                update_run_progress(run_id, platform, f"{platform} failed: {exc}")
                continue
        # 실측 시그니처: finish_run(run_id, status, posts_count, summary=None)
        finish_run(
            run_id,
            "completed",
            len(inserted_ids),
            summary=f"research refresh: {','.join(crawled)}",
        )
        # research_runs 는 run_with_expansion 이 최종 메타와 함께 completed 처리
        # (days_per_platform, window_expanded 는 여기서 아직 모름)
    except Exception as exc:  # pylint: disable=broad-except
        finish_run(run_id, "failed", len(inserted_ids), summary=str(exc)[:500])
        store.record_failed(research_run_id, str(exc)[:500])
        raise
    if not crawled and available:
        # 7차 리뷰 P0-2 보강: 모든 플랫폼이 개별적으로 실패(continue) 하고 try 블록을
        # 정상 종료한 경로. research_runs row 가 running 으로 남지 않도록 record_failed
        # 직접 호출 후 raise.
        store.record_failed(research_run_id, f"all platforms failed: {available}")
        raise AllPlatformsFailedError(f"all platforms failed: {available}")
    return crawled, len(inserted_ids), inserted_ids, research_run_id


def _fetch_existing_subset(platform: str, candidate_ids: set[str]) -> set[str]:
    """candidate_ids 중 이미 DB 에 있는 것만 반환 (4차 리뷰 P2-6).

    플랫폼 풀스캔(P2-6) 대신 incoming post 수에 비례한 점검. SQLite 파라미터
    제한(기본 999) 때문에 배치로 나눠 실행.
    """
    if not candidate_ids:
        return set()
    conn = get_connection()
    existing: set[str] = set()
    batch = 900
    ids = list(candidate_ids)
    try:
        for i in range(0, len(ids), batch):
            chunk = ids[i:i + batch]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT external_id FROM posts "
                f"WHERE platform = ? AND external_id IN ({placeholders})",
                [platform, *chunk],
            ).fetchall()
            existing.update(r[0] for r in rows if r[0])
    finally:
        conn.close()
    return existing


def _post_external_id(post, platform: str) -> str:
    """save_posts 의 external_id 계산 규칙(db.py:247-251) 과 동일."""
    data = post.model_dump() if hasattr(post, "model_dump") else post
    ext = data.get("external_id")
    if ext:
        return ext
    import hashlib
    hash_src = f"{platform}:{data.get('author', '')}:{data.get('content', '')}"
    return hashlib.sha256(hash_src.encode()).hexdigest()[:16]
```

**`fetched_this_run` 플래그 계산**: 최종 search 결과 각 post 의 `(platform, external_id)` 가 `inserted_ids` 에 있으면 True. upsert enrichment 만 된 기존 row 는 False 유지 (P1-5 해결).

**주의 (2차 리뷰 #2-3 실측 반영)**:
- `save_posts` 는 `(posts, platform, source=None, db_path=None)` — platform 위치 필수
- `finish_run` 은 `(run_id, status, posts_count, summary=None, db_path=None)` — status 와 posts_count 모두 위치 필수
- 이전 계획의 `save_posts(posts)`, `finish_run(run_id, status="completed", summary=...)` 은 모두 TypeError

### 세션 체크 (codex review #8 대응)

실제 크롤러 요구사항과 일치시킨다.

| 플랫폼 | 세션 필요 조건 |
|---|---|
| `threads` | 항상 필요 |
| `x` | 항상 필요 |
| `linkedin` | 항상 필요 |
| `reddit` | **홈 피드** 수집 시에만 필요. `--subreddit` 지정되면 세션 없이도 동작 (실제 `reddit` API 크롤러 동작) |
| 나머지(feed류) | 불필요 |

전체 정의는 "헬퍼" 섹션의 `_filter_by_session` 참조. 7차 리뷰 P1-5 수정으로 `SystemExit` 대신 `NoSessionError` 를 raise 하며, `run_research` 최상위가 catch 해서 exit code 1 로 매핑한다.

### Top-level 진입점 + exit code 매핑 (5차 리뷰 P1-4, 6차 리뷰 P1-2/3)

```python
# AllPlatformsFailedError, ConcurrentResearchError, DbWriteError, NoSessionError 는
# 본 파일(refresh.py) 상단 import 섹션에 이미 정의되어 있음. 아래 코드는 같은 모듈 내부
# 연장이라 별도 import 불필요 (8차 리뷰 P1: self-import 제거).

DEFAULT_LIMIT = 50  # --limit 기본값 (Phase 1 CLI 스펙과 동일)


def _tokenize(topic: str) -> list[str]:
    """공백 split + lower + 빈 토큰 제거."""
    return [t.lower() for t in topic.split() if t.strip()]


async def _run_with_lock_and_refresh(
    *,
    topic: str,
    tokens: list[str],
    date_range: dict[str, str],
    sources: list[str],
    days: int,
    limit: int,  # 7차 리뷰 P1-8: CLI 의 --limit 을 run_with_expansion 까지 forward
    refresh_mode: str,
    explicit: bool,
    initial_results: list[dict],
    initial_stats: SearchStats,
) -> dict:
    """실행 흐름 섹션 1-15 단계를 구현하는 내부 함수.

    Raises:
        ConcurrentResearchError: lock 획득 실패 또는 running row 충돌
        NoSessionError: explicit + 세션 없음
        AllPlatformsFailedError: 모든 플랫폼 크롤 실패
    """
    tokens_key = _canonical_key(tokens)
    sources_key = _canonical_key(sources)
    with research_lock(workspace_root()):
        conn = get_connection()
        try:
            _cleanup_stale_research_runs(conn)
            if within_backoff(conn, tokens_key, sources_key):
                return _build_response(
                    topic=topic, tokens=tokens, date_range=date_range,
                    sources_requested=sources,
                    posts=initial_results, search_stats=initial_stats,
                    days=days,
                    warnings_list=["recent refresh within 30m, using cached"],
                )
            if _has_running(conn, tokens_key, sources_key):
                raise ConcurrentResearchError("another research run is active")
        finally:
            conn.close()

        # 여기서부터 run_with_expansion 이 refresh_platforms 를 호출하고
        # record_completed 로 research_runs 를 종결한다 (7차 리뷰 P0-1/P0-2)
        return await run_with_expansion(
            topic=topic, tokens=tokens, sources=sources,
            requested_days=days, limit=limit,
            refresh_mode=refresh_mode, explicit=explicit,
            tokens_key=tokens_key, sources_key=sources_key,
        )


async def run_research(
    *,
    topic: str,
    sources: list[str],
    days: int,
    limit: int = DEFAULT_LIMIT,  # 7차 리뷰 P1-8
    refresh_mode: str,
    explicit: bool,
) -> tuple[int, dict]:
    """최상위 진입점. exit code 와 JSON 응답 반환.

    Exit code 매핑 (6차 리뷰 P1-3):
      0 = 정상 (빈 결과·warning 포함)
      1 = NoSessionError (explicit source 세션 없음)
      2 = AllPlatformsFailedError (모든 refresh 실패)
      3 = DbWriteError (sqlite3.OperationalError 등)
      4 = ConcurrentResearchError + force mode
    """
    tokens = _tokenize(topic)
    base_since = _since_utc(days)
    date_range = {"from": base_since, "to": datetime.now(UTC).isoformat()}
    try:
        initial_results, initial_stats = search_posts(topic, base_since, sources, limit)
    except sqlite3.OperationalError as exc:
        raise DbWriteError(str(exc)) from exc

    if refresh_mode == "never":
        return 0, _build_response(
            topic=topic, tokens=tokens, date_range=date_range,
            sources_requested=sources,
            posts=initial_results, search_stats=initial_stats,
            days=days,
        )

    try:
        resp = await _run_with_lock_and_refresh(
            topic=topic, tokens=tokens, date_range=date_range,
            sources=sources, days=days, limit=limit,
            refresh_mode=refresh_mode, explicit=explicit,
            initial_results=initial_results, initial_stats=initial_stats,
        )
        return 0, resp
    except ConcurrentResearchError as exc:
        if refresh_mode == "force":
            return 4, {}
        return 0, _build_response(
            topic=topic, tokens=tokens, date_range=date_range,
            sources_requested=sources,
            posts=initial_results, search_stats=initial_stats,
            days=days,
            warnings_list=[f"concurrent refresh in progress: {exc}"],
        )
    except NoSessionError:
        return 1, {}
    except AllPlatformsFailedError:
        return 2, {}
    except (sqlite3.OperationalError, DbWriteError):
        return 3, {}
```

### CLI 어댑터 (6차 리뷰 P1-3)

`packages/skim-cli/src/skim_cli/cli.py` 의 `research` 서브커맨드:

```python
import asyncio
import json
import sys
import typer

from skim_core.research.refresh import run_research

@app.command()
def research(
    topic: str = typer.Argument(...),
    days: int = typer.Option(7, "--days"),
    sources: str = typer.Option("all", "--sources"),
    limit: int = typer.Option(50, "--limit"),
    emit: str = typer.Option("json", "--emit"),
    refresh: str = typer.Option("auto", "--refresh"),
) -> None:
    if not topic.strip():
        typer.echo("Usage: skim research TOPIC [OPTIONS]", err=True)
        raise typer.Exit(code=2)
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    explicit = source_list != ["all"]
    exit_code, response = asyncio.run(run_research(
        topic=topic, sources=source_list, days=days, limit=limit,
        refresh_mode=refresh, explicit=explicit,
    ))
    if exit_code == 0 and emit == "json":
        typer.echo(json.dumps(response, ensure_ascii=False, indent=2))
    elif exit_code != 0:
        sys.stderr.write(f"[skim] exited with code {exit_code}\n")
    raise typer.Exit(code=exit_code)
```

### Concurrency & Thundering Herd 방어 (2차 리뷰 #2-6, 3차 리뷰 P1-3 대응)

v1 부터 **방어 구현**. 1차 리뷰 때 "v1 에선 안 함" 했던 결정 철회 (아래 "Concurrency v1 적용 완료" 섹션도 참조).

### Stale `running` row 정리 (3차 리뷰 P1-3)

kill -9 이후 flock 은 OS 가 자동 해제해도, `research_runs.status='running'` row 는 DB 에 그대로 남아 5분간 같은 쿼리를 차단한다. `_cleanup_stale_research_runs()` 를 lock 획득 직후 호출:

```python
STALE_RUNNING_TTL_MINUTES = 10  # flock window (5분) 초과. OS 가 놓친 프로세스만 대상


def _cleanup_stale_research_runs(conn: sqlite3.Connection) -> int:
    """runner_pid 가 죽었거나 TTL 초과한 running row 를 interrupted 로 전환.

    7차 리뷰 P2-9: `conn` 은 반드시 `conn.row_factory = sqlite3.Row` 가 설정된
    connection (`skim_core.db.get_connection()` 반환물) 이어야 한다. plain
    `sqlite3.connect(...)` 을 그대로 넘기면 `row["runner_pid"]` 접근에서
    TypeError 가 발생한다. 호출자(`_run_with_lock_and_refresh`) 는 항상
    `get_connection()` 을 경유한다.
    """
    cutoff = (datetime.now(UTC) - timedelta(minutes=STALE_RUNNING_TTL_MINUTES)).isoformat()
    rows = conn.execute(
        """SELECT id, runner_pid, runner_host, started_at
           FROM research_runs
           WHERE status = 'running'""",
    ).fetchall()
    cleaned = 0
    local_host = socket.gethostname()
    for row in rows:
        dead = False
        if row["runner_host"] == local_host:
            # 같은 호스트면 PID 생존 확인
            try:
                os.kill(row["runner_pid"], 0)
            except (ProcessLookupError, PermissionError):
                dead = True
        if dead or row["started_at"] < cutoff:
            conn.execute(
                "UPDATE research_runs SET status='interrupted', finished_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), row["id"]),
            )
            cleaned += 1
    conn.commit()
    return cleaned
```

호출 순서: `research_lock` acquire → `_cleanup_stale_research_runs` → `within_backoff` 체크 → `running` SQL 체크.

**두 단계 방어**:

1. **Advisory lock (프로세스 간 hard gate)** — `$SKIM_WORKSPACE_ROOT/data/skim.research.lock` 파일 + `fcntl.flock(LOCK_EX | LOCK_NB)`. 획득 실패 시:
   - `--refresh auto`: refresh skip + warning `"another research refresh is in progress, returning cached results"`. 초기 검색 결과 그대로 반환 (exit 0)
   - `--refresh force`: exit code 4 (concurrent run)
2. **`research_runs.status='running'` (SQL 사전 체크)** — lock 획득 후, 같은 `(tokens_key, sources_key)` 의 최근 5분 내 `running` run 존재하면 같은 대응. lock 과 SQL 체크 모두 통과해야 crawl 진입.

```python
import fcntl
from contextlib import contextmanager

@contextmanager
def research_lock(workspace: Path):
    lock_path = workspace / "data" / "skim.research.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConcurrentResearchError("another research refresh is in progress") from exc
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
```

**Backoff (2차 리뷰 #2-8)**: `within_backoff()` 가 True 이면 refresh skip + warning `"recent refresh within 30m, using cached"`. 무한 재크롤 루프 방지.

**쿨링 단계 요약**:

```text
initial search
  → within_backoff? yes → skip refresh (warning)
  → within_backoff? no
    → research_lock acquire?
      → fail → skip refresh (warning)
      → ok → running SQL check
        → running exists → release lock, skip refresh (warning)
        → clear → crawl + finish_run
```

**세션 경합**: 같은 플랫폼 세션 파일(Playwright CDP 등)을 두 프로세스가 동시 접근하는 문제는 lock 으로 막힘.

## 실행 흐름 (auto 모드, 7차 리뷰 P0-1/P0-2 반영)

함수 호출 그래프:

```text
cli.research
  → run_research(topic, sources, days, limit, refresh_mode, explicit)
      → search_posts  (초기 결과 확보 — backoff/lock 실패 시 바로 반환용)
      → _run_with_lock_and_refresh(...)
          → research_lock (fcntl.flock)
          → get_connection()
          → _cleanup_stale_research_runs(conn)
          → within_backoff(conn, tokens_key, sources_key)    # cached → return
          → _has_running(conn, tokens_key, sources_key)      # ConcurrentResearchError
          → conn.close()
          → run_with_expansion(...)
              → search_posts (1차, requested_days)
              → should_refresh_per_platform → stale
              → refresh_platforms(stale, days, explicit, ...)
                    → _filter_by_session
                    → save_run / store.record_started
                    → REGISTRY[platform].crawl(**_build_crawler_options)
                    → save_posts(posts, platform)
                    → finish_run(run_id, 'completed', ...)
                      (실패 시 store.record_failed)
              → search_posts (2차, requested_days)
              → per-platform window 확장 루프 (최대 2회)
              → store.record_completed(research_run_id, ...)   # P0-2
              → _build_response
```

단계별 상태 전이:

```text
 1. tokens_key, sources_key 계산 (정렬된 JSON)
 2. search_posts(topic, days=7, sources) → results_v1 (+ search_stats) — 캐시 반환용
 3. refresh_mode == 'never' → results_v1 반환 (끝)
 4. research_lock 획득
    - 실패 (auto) → results_v1 + warning 반환
    - 실패 (force) → exit 4
    - 성공 → 5
 5. _cleanup_stale_research_runs (dead PID / TTL 초과 running row 정리, P1-3)
 6. within_backoff(tokens_key, sources_key, 30min)?
    - yes → results_v1 + warning "recent refresh cached" 반환 (끝)
    - no → 7
 7. _has_running(conn, tokens_key, sources_key)?
    - yes → ConcurrentResearchError → auto 는 results_v1 + warning, force 는 exit 4
    - no → 8
 8. conn.close() → run_with_expansion 진입
 9. search_posts(topic, base_days, sources) → results (1차)
10. stale = should_refresh_per_platform(results, refresh_mode, sources)
    - empty → record_completed 호출 없이 results 반환 (research_run_id 없음)
    - 존재 → 11
11. refresh_platforms(stale, requested_days, explicit, topic, tokens_key, sources_key, refresh_mode)
    - 내부에서 save_run() + store.record_started(...) → running row 생성
    - REGISTRY[platform].crawl(...) → save_posts(posts, platform)
    - finish_run(run_id, 'completed', inserted_count, ...) — `runs` 테이블 종결
    - research_run_id 반환 (research_runs 는 아직 running — run_with_expansion 이 종결)
12. search_posts(topic, base_days, sources) → results (2차, 신규 posts 반영)
13. window 확장 판정: stale 여전? window_expanded < 2?
    - yes → stale 플랫폼만 넓은 window (14, 30) 로 search_posts + merge_by_external_id
    - no → 14
14. store.record_completed(research_run_id, result_count, newly_fetched, crawled_platforms,
     days_per_platform, window_expanded) — `research_runs` 종결 (P0-2)
15. 각 post 에 fetched_this_run 플래그 부여 (inserted_ids 기반, `_build_response` 내부)
16. lock release → results 반환
```

주의: 단계 11 의 `refresh_platforms` 가 `runs` 테이블 한정 `finish_run` 까지 내부에서 수행하지만, `research_runs` 쪽은 `run_with_expansion` 이 window 확장까지 끝낸 뒤 `store.record_completed` 로 종결한다. 두 테이블이 서로 다른 시점에 완료되는 이유는 `research_runs.days_per_platform`/`window_expanded` 가 확장 루프 이후에만 확정되기 때문.

**`stats.newly_fetched`**, **`stats.window_expanded`**, **`stats.days_per_platform`** 은 Phase 1 의 단일 권위 스키마에 정의된 `stats` 아래 nested 필드 (4차 리뷰 P2-5). Phase 2 refresh 완료 후 `_build_response` 에서 채운다.

## CLI 변경사항 (Phase 1 확장)

```bash
uv run skim research "topic" \
  --days 7 \
  --sources all \
  --refresh auto          # default
  --emit json

# 크롤 강제
uv run skim research "topic" --refresh force

# 크롤 절대 안 함 (CI, offline)
uv run skim research "topic" --refresh never
```

stderr 로그 예시:

```text
[skim research] topic=nvidia tokens=['nvidia'] days=7 refresh=auto
[skim research] initial search: 2 posts (min=5)
[skim research] triggering refresh: hackernews, reddit, x
[skim research] x: no session file, skipped
[skim research] hackernews: 18 new posts
[skim research] reddit: 7 new posts
[skim research] final search: 27 posts
```

## Edge Cases + 에러 계약 (codex review #14, 2차 리뷰 #2-6 대응)

CLI 종료 코드 규약:

| 상황 | exit code | stderr | JSON `warnings` |
|---|---|---|---|
| 정상 (빈 결과 포함) | 0 | 없음 | 빈 배열 |
| 토큰 0개 | 0 | warning | `"no searchable tokens in topic"` |
| 짧은 토큰 (≤2) 존재 | 0 | warning | `"short tokens (<=2 chars): [...]"` |
| 명시 `--sources X` 인데 세션 없음 | 1 | 에러 | — (JSON 미출력) |
| 일부 플랫폼 크롤 실패 (partial success) | 0 | warning | `"refresh failed for: <list>"` |
| backoff 로 refresh skip | 0 | warning | `"recent refresh within 30m, using cached"` |
| concurrent run 으로 refresh skip (auto) | 0 | warning | `"another research refresh in progress"` |
| concurrent run (`--refresh force`) | 4 | 에러 | — |
| window 확장 후에도 결과 <5 | 0 | warning | `"window expanded to 30d but still <5 results"` |
| 모든 플랫폼 크롤 실패 | 2 | 에러 | — |
| `--sources` 에 알 수 없는 플랫폼 | 2 | 에러 | — |
| DB 파일 쓰기 실패 | 3 | 에러 | — |

**원칙**: exit 0 이면 stdout JSON 파싱 가능. warning 은 모두 `warnings[]` 에 수록해서 플러그인 소비자가 구조적으로 파악.

### Edge 상세

- `--refresh force` + 세션 없음 (암묵) → 가능한 플랫폼만 크롤, warning + exit 0
- `--refresh force` + 세션 없음 (`--sources x` 명시) → exit 1
- `--refresh auto` + 토큰이 흔한 단어("the") → stopword 아니면 매칭은 되지만 stale 판정돼 크롤. 의도된 동작
- `--refresh auto` + 토큰 전부 제거 (stopword) → Phase 1 규칙에 따라 바로 빈 결과 + warning
- 동시 실행 (다른 터미널에서 `skim research` 둘) → "Concurrency & Thundering Herd 방어" 섹션 참조. `research_lock` + `research_runs.status='running'` 이중 방어로 차단됨. 1차 리뷰 당시 "v1 은 방어 없음" 결정은 폐기됨 (3차 리뷰 P2-03 대응)

## TDD 체크리스트

- [ ] `test_should_refresh_force_refreshes_all_sources`
- [ ] `test_should_refresh_never_returns_empty_list`
- [ ] `test_should_refresh_auto_per_platform_stale_only` — 한 플랫폼만 stale 이면 그것만 반환 (2차 리뷰 #2-14)
- [ ] `test_should_refresh_auto_skips_when_fresh_and_enough`
- [ ] `test_should_refresh_auto_invalid_timestamp_triggers_refresh`
- [ ] `test_within_backoff_true_for_recent_same_key`
- [ ] `test_within_backoff_false_for_different_tokens`
- [ ] `test_within_backoff_false_for_different_sources`
- [ ] `test_within_backoff_ignores_failed_runs`
- [ ] `test_research_lock_acquire_when_free`
- [ ] `test_research_lock_blocks_concurrent_acquire`
- [ ] `test_research_lock_auto_mode_returns_cached_on_conflict`
- [ ] `test_research_lock_force_mode_exits_4_on_conflict`
- [ ] `test_running_status_blocks_refresh_in_same_window`
- [ ] `test_window_expansion_stops_after_two_tries`
- [ ] `test_window_expansion_records_expanded_count`
- [ ] `test_refresh_skips_missing_session_with_warning`
- [ ] `test_refresh_explicit_source_missing_session_errors` — `--sources x` 인데 세션 없으면 exit 1
- [ ] `test_refresh_calls_save_posts_with_platform_positional` — 실측 시그니처 회귀 (2차 리뷰 #2-3)
- [ ] `test_refresh_calls_finish_run_with_posts_count_positional`
- [ ] `test_refresh_marks_fetched_this_run_on_new_posts`
- [ ] `test_newly_fetched_excludes_upsert_enrichment` — 기존 row 의 enrichment update 는 newly_fetched 에서 제외 (3차 리뷰 P1-5)
- [ ] `test_stats_newly_fetched_exposed_in_json`
- [ ] `test_cleanup_stale_running_row_dead_pid` — runner_pid 가 죽은 row 를 interrupted 로 전환 (3차 리뷰 P1-3)
- [ ] `test_cleanup_stale_running_row_ttl_exceeded` — TTL 10분 초과 row 정리
- [ ] `test_cleanup_preserves_live_pid` — 살아있는 PID 는 보존
- [ ] `test_expansion_only_for_stale_platforms` — fresh 플랫폼은 days=7 유지 (3차 리뷰 P1-4)
- [ ] `test_expansion_candidates_handle_days_gt_30` — `days=60` 입력 시 UnboundLocalError 없음 (RISK-03)
- [ ] `test_refresh_forwards_requested_days_not_hardcoded` — `--days 14` → crawler options 에 14 (RISK-04)
- [ ] `test_canonical_key_order_insensitive` — `['x','y']` 와 `['y','x']` 가 동일 키 (RISK-01)
- [ ] `test_canonical_key_dedup` — `['x','x','y']` 와 `['x','y']` 동일 키
- [ ] `test_ensure_column_idempotent` — 이미 있는 컬럼 재추가 시 no-op (6차 리뷰 P3-7)
- [ ] `test_migrate_research_runs_on_fresh_db` — `research_runs` 없는 DB 에서 CREATE + 컬럼 추가 성공
- [ ] `test_migrate_research_runs_on_existing_v1_db` — 이미 v1 schema 인 DB 에서 멱등 동작
- [ ] `test_run_research_exit_code_0_success` (6차 리뷰 P1-3)
- [ ] `test_run_research_exit_code_1_no_session` — explicit + 세션 없음
- [ ] `test_run_research_exit_code_2_all_platforms_failed` — 모든 플랫폼 exception
- [ ] `test_run_research_exit_code_3_db_write_error` — sqlite3.OperationalError
- [ ] `test_run_research_exit_code_4_concurrent_force` — lock 실패 + force mode
- [ ] `test_run_research_auto_on_concurrent_returns_cached` — lock 실패 + auto mode
- [ ] `test_refresh_platforms_records_both_tables` — `runs` + `research_runs` 동시 기록 (6차 리뷰 P2-4)
- [ ] `test_research_run_recorded_on_success`
- [ ] `test_research_run_recorded_on_crawl_failure`
- [ ] `test_partial_success_when_one_platform_fails`
- [ ] `test_run_with_expansion_invokes_refresh_platforms_when_stale` — stale 감지 시 `refresh_platforms` 가 실제로 호출 (7차 리뷰 P0-1)
- [ ] `test_run_with_expansion_records_completed_on_success` — `store.record_completed` 로 `research_runs.status='completed'` 전이 (7차 리뷰 P0-2)
- [ ] `test_run_with_expansion_never_mode_skips_refresh` — `refresh_mode='never'` 면 refresh_platforms 미호출
- [ ] `test_run_with_expansion_skips_record_completed_when_no_refresh` — stale 없으면 research_run_id 생성 안 하고 record_completed 도 호출 안 함
- [ ] `test_has_running_returns_true_for_active_row` — 동일 (tokens, sources) 에 running row 있을 때 (7차 리뷰 P1-3)
- [ ] `test_has_running_returns_false_after_cleanup` — stale cleanup 후 running row 정리되면 False
- [ ] `test_filter_by_session_raises_no_session_error_when_explicit` — `SystemExit` 대신 `NoSessionError` (7차 리뷰 P1-5)
- [ ] `test_run_research_forwards_limit_to_run_with_expansion` — CLI `--limit 10` 이 initial search + run_with_expansion 까지 전파 (7차 리뷰 P1-8)
- [ ] `test_cleanup_stale_runs_requires_row_factory` — plain connection 넘기면 TypeError 재현 (7차 리뷰 P2-9, 계약 회귀)
- [ ] `test_within_backoff_ignores_interrupted_rows` — interrupted/failed row 는 backoff 근거에서 제외 (9차 리뷰 P3-4)
- [ ] `test_research_runs_not_created_when_no_stale` — stale 없으면 research_runs row 생성 안 함 (9차 리뷰 P2-3, 정책 회귀)

## 수동 검증

```bash
# force: 세션 있는 플랫폼만 크롤 확인
uv run skim login reddit
uv run skim research "llm" --refresh force --sources reddit,x 2>&1 | tee run.log

# never: DB만 조회
uv run skim research "llm" --refresh never

# 이력 확인
sqlite3 data/skim.db 'SELECT topic, result_count, crawled_platforms FROM research_runs ORDER BY id DESC LIMIT 5;'
```

## 의존성

- **Phase 0 완료 필수** (timestamp 정규화 + DB API 시그니처 확정)
- Phase 1 완료 필요 (`search_posts` 함수 + `search_stats` 반환)
- skim의 기존 `save_run`/`finish_run` 패턴 준수

## TODO

- research_runs → Swift desktop UI에 이력 탭으로 노출 (v1)
- 크롤 결과 중 방금 저장된 post만 별도 표기 (새 것 vs 기존)
- 크롤 타임아웃 옵션 (`--refresh-timeout 60`)
