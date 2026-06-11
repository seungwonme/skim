"""`research_runs` 테이블 CRUD.

기록 정책 (refresh attempt log):
  - read-only search (refresh_mode='never' OR stale 없음 OR backoff cached) 는 row 생성 안 함
  - refresh 가 실제 시도된 경우에만 record_started → record_completed/failed

불변식:
  - days_per_platform: 항상 JSON object, 빈 값은 `'{}'`
  - crawled_platforms: 항상 JSON array, 빈 값은 `'[]'`
  - status ∈ {'running', 'completed', 'failed', 'interrupted'}
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
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
    db_path: Optional[Path] = None,
) -> int:
    """running 상태로 INSERT. id 반환."""
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO research_runs
           (topic, tokens_key, sources_key, refresh_mode, days_requested,
            days_per_platform, window_expanded, newly_fetched, result_count,
            crawled_platforms, started_at, status, runner_pid, runner_host)
           VALUES (?, ?, ?, ?, ?, '{}', 0, 0, 0, '[]', ?, 'running', ?, ?)""",
        (
            topic,
            tokens_key,
            sources_key,
            refresh_mode,
            days_requested,
            datetime.now(UTC).isoformat(),
            os.getpid(),
            socket.gethostname(),
        ),
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
    db_path: Optional[Path] = None,
) -> None:
    """run 종결 (status='completed')."""
    conn = get_connection(db_path)
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
        (
            datetime.now(UTC).isoformat(),
            result_count,
            newly_fetched,
            json.dumps(crawled_platforms),
            json.dumps(days_per_platform),
            window_expanded,
            run_id,
        ),
    )
    conn.commit()
    conn.close()


def record_failed(
    run_id: int,
    error_message: str,
    *,
    db_path: Optional[Path] = None,
) -> None:
    """run 종결 (status='failed'). error_message 는 500 자로 잘림."""
    conn = get_connection(db_path)
    conn.execute(
        """UPDATE research_runs SET status='failed', finished_at=?, error_message=?
           WHERE id=?""",
        (datetime.now(UTC).isoformat(), error_message[:500], run_id),
    )
    conn.commit()
    conn.close()


def load_days_per_platform(
    run_id: int,
    *,
    db_path: Optional[Path] = None,
) -> dict[str, int]:
    """읽기 시 JSON 역직렬화."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT days_per_platform FROM research_runs WHERE id = ?", (run_id,)
    ).fetchone()
    conn.close()
    return json.loads(row[0]) if row else {}


def load_run(
    run_id: int,
    *,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    """run row 를 dict 으로 반환. JSON 컬럼은 파싱됨."""
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    data = dict(row)
    data["crawled_platforms"] = json.loads(data.get("crawled_platforms") or "[]")
    data["days_per_platform"] = json.loads(data.get("days_per_platform") or "{}")
    return data
