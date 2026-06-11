"""Phase 2 — `--refresh auto|never|force` 의 핵심 로직.

다음 책임을 담당:
  - per-platform staleness 판정 (`should_refresh_per_platform`)
  - thundering herd 방어 (advisory lock + research_runs.status='running')
  - per-platform window 자동 확장 (7→14→30)
  - refresh_platforms (`runs` + `research_runs` 동시 기록)
  - top-level `run_research` (exit code 0-4 매핑)
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import sqlite3
import warnings as stdlib_warnings
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from skim_core.crawlers import REGISTRY
from skim_core.db import finish_run, get_connection, save_posts, save_run, update_run_progress
from skim_core.paths import workspace_root
from skim_core.research import store
from skim_core.research.search import search_posts
from skim_core.research.serializer import build_response, utc_now_iso
from skim_core.research.types import SearchStats

UTC = timezone.utc

AUTO_MIN_RESULTS = 5
AUTO_MAX_STALENESS_HOURS = 6
BACKOFF_WINDOW_MINUTES = 30
DEFAULT_WINDOW_EXPANSION = (7, 14, 30)
MAX_EXPANSION_STEPS = 2
STALE_RUNNING_TTL_MINUTES = 10
DEFAULT_LIMIT = 50

SESSION_REQUIRED = {"threads", "x", "linkedin"}


# ──────────────────────────────────────────────────────────────────────
# 에러 클래스 (exit code 매핑)
# ──────────────────────────────────────────────────────────────────────


class ConcurrentResearchError(RuntimeError):
    """다른 프로세스가 research_lock 을 잡고 있을 때 raise.

    `--refresh auto`: warning 추가 후 initial search 결과 반환 (exit 0).
    `--refresh force`: exit 4.
    """


class NoSessionError(RuntimeError):
    """명시 --sources 인데 세션 없을 때 raise. exit 1."""


class AllPlatformsFailedError(RuntimeError):
    """모든 refresh 타겟 크롤 실패. exit 2."""


class DbWriteError(RuntimeError):
    """DB 파일 쓰기 실패. exit 3."""


# ──────────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────────


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
    """(platform, external_id) 키로 dedup 하며 병합."""
    seen = {(r["platform"], r.get("external_id")) for r in base}
    merged = list(base)
    for r in extra:
        key = (r["platform"], r.get("external_id"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(r)
    return merged


def _canonical_key(items: list[str]) -> str:
    """정렬 + 중복 제거 + JSON 직렬화. tokens_key, sources_key 공용."""
    return json.dumps(sorted(set(items or [])), ensure_ascii=False)


def _extend_unique(target: list[str], extra: list[str]) -> None:
    """order-preserving extend that skips duplicates already in target."""
    for item in extra:
        if item not in target:
            target.append(item)


def _session_skip_warnings(platforms: list[str]) -> list[str]:
    """Build structured response warnings for session-gated skipped platforms."""
    return [
        f"{platform}: no session file, skipped. Run `uv run skim login {platform}` first."
        for platform in platforms
    ]


def _tokenize(topic: str) -> list[str]:
    return [t.lower() for t in topic.split() if t.strip()]


def session_file_exists(platform: str, *, workspace: Optional[Path] = None) -> bool:
    base = workspace or workspace_root()
    return (base / "data" / "sessions" / f"{platform}_session.json").exists()


def _reddit_requires_session(options: dict) -> bool:
    """subreddit 지정 없으면 홈 피드 → 세션 필요."""
    return not options.get("subreddit")


def _resolve_sources(requested: list[str]) -> list[str]:
    if not requested or requested == ["all"]:
        return list(REGISTRY.keys())
    unknown = [p for p in requested if p not in REGISTRY]
    if unknown:
        raise ValueError(f"unknown sources: {unknown}")
    return list(requested)


def _filter_by_session(
    platforms: list[str],
    *,
    explicit: bool,
    options_by_platform: Optional[dict[str, dict]] = None,
    workspace: Optional[Path] = None,
) -> tuple[list[str], list[str]]:
    """세션 필요한데 없는 플랫폼 제거. explicit 모드면 NoSessionError."""
    options_by_platform = options_by_platform or {}
    kept, skipped = [], []
    for p in platforms:
        needs_session = p in SESSION_REQUIRED
        if p == "reddit":
            needs_session = _reddit_requires_session(options_by_platform.get("reddit", {}))
        if needs_session and not session_file_exists(p, workspace=workspace):
            if explicit:
                raise NoSessionError(f"{p}: no session. Run `uv run skim login {p}` first.")
            stdlib_warnings.warn(f"[skim] {p}: no session file, skipped. Run `skim login {p}`")
            skipped.append(p)
            continue
        kept.append(p)
    return kept, skipped


def _build_crawler_options(platform: str, days: int) -> dict:
    since_iso = _since_utc(days)
    if platform in {
        "hackernews",
        "geeknews",
        "youtube",
        "producthunt",
        "arxiv",
        "huggingface",
        "everyto",
        "blogs",
        "ailabs",
    }:
        return {"since": since_iso, "no_content": False}
    if platform in {"threads", "x", "linkedin"}:
        return {"count": max(30, days * 10)}
    if platform == "reddit":
        return {"count": max(30, days * 10), "sort": "hot"}
    raise ValueError(f"no options mapping for platform: {platform}")


# ──────────────────────────────────────────────────────────────────────
# 판정 / backoff
# ──────────────────────────────────────────────────────────────────────


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
    return dt.astimezone(UTC)


def should_refresh_per_platform(
    results: list[dict],
    refresh_mode: str,
    requested_sources: list[str],
) -> list[str]:
    """플랫폼 단위 stale 판정.

    `never` → 빈 리스트, `force` → 전체, `auto` → per-platform.
    """
    if refresh_mode == "never":
        return []
    if refresh_mode == "force":
        return list(requested_sources)

    now = datetime.now(UTC)
    stale: list[str] = []
    by_platform = _group_by_platform(results)
    for platform in requested_sources:
        posts = by_platform.get(platform, [])
        if len(posts) < AUTO_MIN_RESULTS:
            stale.append(platform)
            continue
        parsed = [dt for dt in (_parse_iso(p["timestamp"]) for p in posts) if dt]
        if not parsed:
            stale.append(platform)
            continue
        if now - max(parsed) > timedelta(hours=AUTO_MAX_STALENESS_HOURS):
            stale.append(platform)
    return stale


def within_backoff(
    conn: sqlite3.Connection,
    tokens_key: str,
    sources_key: str,
    window_minutes: int = BACKOFF_WINDOW_MINUTES,
) -> bool:
    """최근 window 내 동일 (tokens, sources) 의 completed run 존재 여부.

    `status='completed'` 만 backoff 근거. interrupted/failed 는 제외해서
    kill -9 직후 즉시 재시도 가능하게 한다.
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


def _expansion_candidates(requested_days: int) -> list[int]:
    """requested_days 이상인 확장 후보 (RISK-03 대응)."""
    bigger = [d for d in DEFAULT_WINDOW_EXPANSION if d > requested_days]
    if not bigger:
        return [requested_days]
    return [requested_days, *bigger]


# ──────────────────────────────────────────────────────────────────────
# Lock + stale cleanup + running check
# ──────────────────────────────────────────────────────────────────────


@contextmanager
def research_lock(workspace: Path):
    """`data/skim.research.lock` 위에 fcntl.flock(LOCK_EX | LOCK_NB).

    획득 실패 시 ConcurrentResearchError.
    """
    lock_path = workspace / "data" / "skim.research.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("w")
    acquired = False
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise ConcurrentResearchError("another research refresh is in progress") from exc
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def _cleanup_stale_research_runs(conn: sqlite3.Connection) -> int:
    """runner_pid 가 죽었거나 TTL 초과한 running row 를 interrupted 로 전환.

    `conn` 은 `get_connection()` 반환물 (row_factory=sqlite3.Row 전제).
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
            try:
                pid = row["runner_pid"]
                if pid:
                    os.kill(pid, 0)
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


def _has_running(conn: sqlite3.Connection, tokens_key: str, sources_key: str) -> bool:
    """동일 (tokens, sources) 로 현재 status='running' row 존재 여부."""
    row = conn.execute(
        """SELECT 1 FROM research_runs
           WHERE tokens_key = ? AND sources_key = ? AND status = 'running'
           LIMIT 1""",
        (tokens_key, sources_key),
    ).fetchone()
    return row is not None


# ──────────────────────────────────────────────────────────────────────
# refresh_platforms + helpers
# ──────────────────────────────────────────────────────────────────────


def _post_external_id(post, platform: str) -> str:
    """save_posts 의 external_id 계산 규칙(db.py) 과 동일."""
    data = post.model_dump() if hasattr(post, "model_dump") else post
    ext = data.get("external_id")
    if ext:
        return ext
    hash_src = f"{platform}:{data.get('author', '')}:{data.get('content', '')}"
    return hashlib.sha256(hash_src.encode()).hexdigest()[:16]


def _fetch_existing_subset(
    platform: str,
    candidate_ids: set[str],
    *,
    db_path: Optional[Path] = None,
) -> set[str]:
    """candidate_ids 중 이미 DB 에 있는 것만 반환 (플랫폼 풀스캔 회피)."""
    if not candidate_ids:
        return set()
    conn = get_connection(db_path)
    existing: set[str] = set()
    batch = 900
    ids = list(candidate_ids)
    try:
        for i in range(0, len(ids), batch):
            chunk = ids[i : i + batch]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT external_id FROM posts "
                f"WHERE platform = ? AND external_id IN ({placeholders})",
                [platform, *chunk],
            ).fetchall()
            existing.update(r["external_id"] for r in rows if r["external_id"])
    finally:
        conn.close()
    return existing


async def refresh_platforms(
    platforms_to_refresh: list[str],
    days: int,
    explicit: bool,
    *,
    topic: str,
    tokens_key: str,
    sources_key: str,
    refresh_mode: str,
    db_path: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> tuple[list[str], int, set[tuple[str, str]], Optional[int], list[str]]:
    """필요한 플랫폼만 크롤 + DB 저장.

    Returns:
        (
            crawled_platforms,
            newly_fetched_count,
            newly_fetched_ids,
            research_run_id,
            skipped_platforms,
        )
        research_run_id 는 record_started 가 호출되었으면 int, 아니면 None.
    """
    available, skipped = _filter_by_session(
        platforms_to_refresh, explicit=explicit, workspace=workspace
    )
    if not available:
        return [], 0, set(), None, skipped

    run_id = save_run(db_path=db_path)
    try:
        research_run_id = store.record_started(
            topic=topic,
            tokens_key=tokens_key,
            sources_key=sources_key,
            refresh_mode=refresh_mode,
            days_requested=days,
            db_path=db_path,
        )
    except Exception as exc:  # pylint: disable=broad-except
        # record_started 실패 시 runs row 가 leak 되지 않도록 즉시 종결 (codex Phase 2 review)
        finish_run(
            run_id, "failed", 0, summary=f"record_started failed: {exc!s}"[:500], db_path=db_path
        )
        raise

    crawled: list[str] = []
    inserted_ids: set[tuple[str, str]] = set()
    try:
        for platform in available:
            update_run_progress(run_id, platform, f"research refresh: {platform}", db_path=db_path)
            try:
                options = _build_crawler_options(platform, days)
                posts = await REGISTRY[platform]().crawl(**options)
                incoming = {_post_external_id(p, platform) for p in posts}
                existed = _fetch_existing_subset(platform, incoming, db_path=db_path)
                new_ids = incoming - existed
                save_posts(posts, platform, db_path=db_path)
                inserted_ids.update({(platform, eid) for eid in new_ids})
                crawled.append(platform)
            except Exception as exc:  # pylint: disable=broad-except
                update_run_progress(run_id, platform, f"{platform} failed: {exc}", db_path=db_path)
                continue
    except Exception as exc:  # pylint: disable=broad-except
        finish_run(run_id, "failed", len(inserted_ids), summary=str(exc)[:500], db_path=db_path)
        store.record_failed(research_run_id, str(exc)[:500], db_path=db_path)
        raise

    if not crawled and available:
        # 모든 플랫폼 individually fail. runs 도 'failed' 로 마감 (codex Phase 2 review).
        finish_run(
            run_id,
            "failed",
            len(inserted_ids),
            summary=f"all platforms failed: {available}",
            db_path=db_path,
        )
        store.record_failed(research_run_id, f"all platforms failed: {available}", db_path=db_path)
        raise AllPlatformsFailedError(f"all platforms failed: {available}")

    # 일부 또는 전체 성공 — runs 는 'completed'
    finish_run(
        run_id,
        "completed",
        len(inserted_ids),
        summary=f"research refresh: {','.join(crawled)}",
        db_path=db_path,
    )
    return crawled, len(inserted_ids), inserted_ids, research_run_id, skipped


# ──────────────────────────────────────────────────────────────────────
# Expansion + run_research
# ──────────────────────────────────────────────────────────────────────


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
    warnings_list: Optional[list[str]] = None,
    db_path: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> dict:
    """1차 검색 → refresh_platforms → 재검색 → per-platform window 확장 → record_completed."""
    warnings_list = warnings_list if warnings_list is not None else []
    candidates = _expansion_candidates(requested_days)
    base_days = candidates[0]
    base_since = _since_utc(base_days)
    max_days_by_platform: dict[str, int] = {p: base_days for p in sources}

    results, stats, search_warnings = search_posts(
        topic, base_since, sources, limit, db_path=db_path
    )
    _extend_unique(warnings_list, search_warnings)

    if refresh_mode == "never":
        return build_response(
            topic=topic,
            tokens=tokens,
            date_range={"from": base_since, "to": utc_now_iso()},
            sources_requested=sources,
            posts=results,
            search_stats=stats,
            days_requested=requested_days,
            window_expanded=0,
            days_per_platform=max_days_by_platform,
            newly_fetched=0,
            warnings=warnings_list,
        )

    stale = should_refresh_per_platform(results, refresh_mode, sources)
    research_run_id: Optional[int] = None
    newly_fetched_ids: set[tuple[str, str]] = set()
    crawled_total: list[str] = []
    window_expanded = 0

    if stale:
        crawled, _, inserted_ids, research_run_id, skipped = await refresh_platforms(
            stale,
            requested_days,
            explicit,
            topic=topic,
            tokens_key=tokens_key,
            sources_key=sources_key,
            refresh_mode=refresh_mode,
            db_path=db_path,
            workspace=workspace,
        )
        _extend_unique(warnings_list, _session_skip_warnings(skipped))
        crawled_total.extend(crawled)
        newly_fetched_ids.update(inserted_ids)
        # 재검색 — 신규 posts 반영
        results, stats, search_warnings = search_posts(
            topic, base_since, sources, limit, db_path=db_path
        )
        _extend_unique(warnings_list, search_warnings)
        stale = should_refresh_per_platform(results, refresh_mode, sources)

    # per-platform window 확장
    for candidate_days in candidates[1:]:
        if not stale:
            break
        if window_expanded >= MAX_EXPANSION_STEPS:
            break
        window_expanded += 1
        extra, extra_stats, extra_warnings = search_posts(
            topic, _since_utc(candidate_days), stale, limit, db_path=db_path
        )
        _extend_unique(warnings_list, extra_warnings)
        for p in stale:
            max_days_by_platform[p] = candidate_days
        results = _merge_by_external_id(results, extra)
        stats = SearchStats(
            rows_scanned=stats.rows_scanned + extra_stats.rows_scanned,
            rows_returned=len(results),
            latency_ms=stats.latency_ms + extra_stats.latency_ms,
            short_tokens=stats.short_tokens,
        )
        stale = should_refresh_per_platform(results, "auto", stale)

    if stale:
        warnings_list.append(
            f"window expanded but still <{AUTO_MIN_RESULTS} per-platform results: {stale}"
        )

    # fetched_this_run flag 부여
    for p in results:
        p["fetched_this_run"] = (p["platform"], p.get("external_id")) in newly_fetched_ids

    # research_runs 종결 (refresh 가 실제로 일어났던 경우만)
    if research_run_id is not None:
        try:
            store.record_completed(
                research_run_id,
                result_count=len(results),
                newly_fetched=len(newly_fetched_ids),
                crawled_platforms=crawled_total,
                days_per_platform=max_days_by_platform,
                window_expanded=window_expanded,
                db_path=db_path,
            )
        except Exception as exc:  # pylint: disable=broad-except
            stdlib_warnings.warn(f"record_completed failed: {exc}")

    return build_response(
        topic=topic,
        tokens=tokens,
        date_range={"from": base_since, "to": utc_now_iso()},
        sources_requested=sources,
        posts=results,
        search_stats=stats,
        days_requested=requested_days,
        window_expanded=window_expanded,
        days_per_platform=max_days_by_platform,
        newly_fetched=len(newly_fetched_ids),
        warnings=warnings_list,
    )


async def _run_with_lock_and_refresh(
    *,
    topic: str,
    tokens: list[str],
    sources: list[str],
    days: int,
    limit: int,
    refresh_mode: str,
    explicit: bool,
    initial_results: list[dict],
    initial_stats: SearchStats,
    initial_warnings: list[str],
    db_path: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> dict:
    tokens_key = _canonical_key(tokens)
    sources_key = _canonical_key(sources)
    base_since = _since_utc(days)
    ws = workspace or workspace_root()
    with research_lock(ws):
        conn = get_connection(db_path)
        try:
            _cleanup_stale_research_runs(conn)
            if within_backoff(conn, tokens_key, sources_key):
                return build_response(
                    topic=topic,
                    tokens=tokens,
                    date_range={"from": base_since, "to": utc_now_iso()},
                    sources_requested=sources,
                    posts=initial_results,
                    search_stats=initial_stats,
                    days_requested=days,
                    warnings=[*initial_warnings, "recent refresh within 30m, using cached"],
                )
            if _has_running(conn, tokens_key, sources_key):
                raise ConcurrentResearchError("another research run is active")
        finally:
            conn.close()

        return await run_with_expansion(
            topic=topic,
            tokens=tokens,
            sources=sources,
            requested_days=days,
            limit=limit,
            refresh_mode=refresh_mode,
            explicit=explicit,
            tokens_key=tokens_key,
            sources_key=sources_key,
            warnings_list=list(initial_warnings),
            db_path=db_path,
            workspace=workspace,
        )


async def run_research(
    *,
    topic: str,
    sources: list[str],
    days: int,
    limit: int = DEFAULT_LIMIT,
    refresh_mode: str,
    explicit: bool,
    db_path: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> tuple[int, dict]:
    """최상위 진입점. (exit_code, response) 반환.

    Exit codes:
      0 = 정상
      1 = NoSessionError
      2 = AllPlatformsFailedError
      3 = DbWriteError / OperationalError
      4 = ConcurrentResearchError + force
    """
    tokens = _tokenize(topic)
    base_since = _since_utc(days)
    date_range = {"from": base_since, "to": utc_now_iso()}
    try:
        initial_results, initial_stats, initial_search_warnings = search_posts(
            topic, base_since, sources, limit, db_path=db_path
        )

        if refresh_mode == "never":
            return 0, build_response(
                topic=topic,
                tokens=tokens,
                date_range=date_range,
                sources_requested=sources,
                posts=initial_results,
                search_stats=initial_stats,
                days_requested=days,
                warnings=initial_search_warnings,
            )

        resp = await _run_with_lock_and_refresh(
            topic=topic,
            tokens=tokens,
            sources=sources,
            days=days,
            limit=limit,
            refresh_mode=refresh_mode,
            explicit=explicit,
            initial_results=initial_results,
            initial_stats=initial_stats,
            initial_warnings=initial_search_warnings,
            db_path=db_path,
            workspace=workspace,
        )
        return 0, resp
    except ConcurrentResearchError as exc:
        if refresh_mode == "force":
            return 4, {}
        return 0, build_response(
            topic=topic,
            tokens=tokens,
            date_range=date_range,
            sources_requested=sources,
            posts=initial_results,
            search_stats=initial_stats,
            days_requested=days,
            warnings=[*initial_search_warnings, f"concurrent refresh in progress: {exc}"],
        )
    except NoSessionError:
        return 1, {}
    except AllPlatformsFailedError:
        return 2, {}
    except (sqlite3.OperationalError, DbWriteError):
        return 3, {}
