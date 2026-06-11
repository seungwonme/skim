"""Topic 검색 (`skim research`) 기본 구현.

Phase 1 — LIKE 기반 단순 검색. FTS5·댓글 본문은 TODO.

검색 필드: `title`, `content`, `content_markdown`, `summary` (Post 모델 기준).
`content` 가 NOT NULL 이므로 항상 포함된다.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from skim_core.db import get_connection
from skim_core.research.types import SearchStats

SEARCH_FIELDS = ("title", "content", "content_markdown", "summary")
SHORT_TOKEN_THRESHOLD = 2


def _escape_like(token: str) -> str:
    """LIKE wildcard 이스케이프. backslash 먼저 (순서 중요)."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _unescape_like(token: str) -> str:
    """이스케이프된 token 을 raw substring 으로 복원 (matched_fields 판정용)."""
    return token.replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\")


def _canonical_utc(since_utc_iso: str) -> str:
    """UTC ISO 8601 입력 검증 + canonical 형태 (`+00:00`) 로 정규화.

    `Z` suffix 는 `+00:00` 으로 치환 (사전순 비교 일관성).
    KST 등 다른 offset 은 ValueError.
    """
    if not since_utc_iso:
        raise ValueError("since_utc_iso must be non-empty UTC ISO 8601")
    raw = since_utc_iso
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"invalid ISO 8601: {since_utc_iso!r}") from exc
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(
            f"since_utc_iso must be UTC (got tzinfo={dt.tzinfo}). "
            "Use datetime.now(timezone.utc) - timedelta(days=N)."
        )
    return dt.astimezone(timezone.utc).isoformat()


def _attach_matched_fields(row: sqlite3.Row, raw_tokens: list[str]) -> dict:
    """row 에 matched_fields 리스트 추가. 모든 토큰이 매칭된 필드만 수록.

    토큰 0개면 빈 리스트 — `all([])` 진리값으로 모든 필드를 가짜 매칭하지 않도록.
    """
    data = dict(row)
    matched: list[str] = []
    if raw_tokens:
        for field_name in SEARCH_FIELDS:
            text = (data.get(field_name) or "").lower()
            if not text:
                continue
            if all(tok in text for tok in raw_tokens):
                matched.append(field_name)
    data["matched_fields"] = matched
    return data


def _validate_timestamps(rows: list[dict]) -> list[str]:
    """각 row 의 timestamp 가 ISO 8601 인지 검증. 실패는 warning 으로 보고."""
    warnings: list[str] = []
    for row in rows:
        ts = row.get("timestamp")
        if not ts:
            continue
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            warnings.append(
                f"unparseable_timestamp: {row.get('platform')} {row.get('external_id')}"
            )
    return warnings


def search_posts(
    topic: str,
    since_utc_iso: str,
    sources: Optional[list[str]],
    limit: int,
    *,
    db_path: Optional[Path] = None,
) -> tuple[list[dict], SearchStats, list[str]]:
    """topic 토큰 AND 매칭으로 posts 를 필터링.

    Args:
        topic:          공백 구분 토큰
        since_utc_iso:  UTC ISO 8601 (`+00:00` 또는 `Z`). 다른 tz 는 ValueError
        sources:        플랫폼 화이트리스트. None 또는 빈 리스트면 전 플랫폼
        limit:          플랫폼별 최대 반환 수 (윈도우 함수로 강제)
        db_path:        테스트용 DB override

    Returns:
        (rows, stats, warnings) — rows 는 matched_fields 포함 dict,
        warnings 는 timestamp 파싱 실패 같은 데이터 품질 이슈.
    """
    canonical_since = _canonical_utc(since_utc_iso)
    raw_tokens = [t.lower() for t in topic.split() if t.strip()]
    short_tokens = [t for t in raw_tokens if len(t) <= SHORT_TOKEN_THRESHOLD]
    escaped_tokens = [_escape_like(t) for t in raw_tokens]

    where: list[str] = []
    params: list = []
    for token in escaped_tokens:
        clauses = " OR ".join(
            f"LOWER(COALESCE({fname}, '')) LIKE ? ESCAPE '\\'" for fname in SEARCH_FIELDS
        )
        where.append(f"({clauses})")
        like = f"%{token}%"
        params.extend([like] * len(SEARCH_FIELDS))

    where.append("timestamp >= ?")
    params.append(canonical_since)

    active_sources = sources if sources else None
    if active_sources:
        placeholders = ",".join("?" * len(active_sources))
        where.append(f"platform IN ({placeholders})")
        params.extend(active_sources)

    where_sql = " AND ".join(where)

    # ROW_NUMBER() PARTITION BY platform 으로 플랫폼별 cap 을 SQL 단계에서 강제.
    # 한 플랫폼이 다른 플랫폼을 starve 하는 문제 (codex Phase 1 review) 방지.
    window_sql = (
        f"SELECT * FROM (SELECT *, ROW_NUMBER() OVER "
        f"(PARTITION BY platform ORDER BY timestamp DESC, id DESC) AS _rn "
        f"FROM posts WHERE {where_sql}) WHERE _rn <= ? "
        f"ORDER BY timestamp DESC, id DESC"
    )

    conn = get_connection(db_path)
    try:
        t0 = time.perf_counter()
        raw_rows = conn.execute(window_sql, [*params, limit]).fetchall()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        # rows_scanned: WHERE 매칭 row 총 수 (cap 전). 측정/디버깅용.
        scanned = conn.execute(f"SELECT COUNT(*) FROM posts WHERE {where_sql}", params).fetchone()[
            0
        ]
    finally:
        conn.close()

    raw_tokens_unescaped = [_unescape_like(t) for t in escaped_tokens]
    rows = [_attach_matched_fields(r, raw_tokens_unescaped) for r in raw_rows]
    ts_warnings = _validate_timestamps(rows)

    stats = SearchStats(
        rows_scanned=scanned,
        rows_returned=len(rows),
        latency_ms=elapsed_ms,
        short_tokens=short_tokens,
    )
    return rows, stats, ts_warnings
