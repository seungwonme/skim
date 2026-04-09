"""
@file db.py
@description SQLite 데이터베이스 관리 모듈

크롤링 데이터, AI 요약, 사용자 피드백을 SQLite에 저장합니다.

@dependencies
- sqlite3 (stdlib)
"""

import hashlib
import json
import os
import socket
import sqlite3
from pathlib import Path
from typing import Optional

from .paths import DATA_DIR

DB_PATH = DATA_DIR / "skim.db"

SCHEMA = """\
CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,
    source      TEXT,
    external_id TEXT,
    author      TEXT NOT NULL,
    title       TEXT,
    content     TEXT NOT NULL,
    url         TEXT,
    timestamp   TEXT,
    likes       INTEGER,
    comments    INTEGER,
    reposts     INTEGER,
    views       INTEGER,
    summary     TEXT,
    content_markdown TEXT,
    word_count  INTEGER,
    extra       TEXT,
    crawled_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, external_id)
);

CREATE TABLE IF NOT EXISTS summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     INTEGER NOT NULL REFERENCES posts(id),
    model       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    tags        TEXT,
    relevance   REAL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     INTEGER NOT NULL REFERENCES posts(id),
    action      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    posts_count INTEGER DEFAULT 0,
    summary     TEXT,
    current_platform TEXT,
    runner_pid  INTEGER,
    runner_host TEXT
);

CREATE TABLE IF NOT EXISTS tracked_sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    canonical_id  TEXT NOT NULL,
    handle_or_url TEXT,
    is_enabled    INTEGER NOT NULL DEFAULT 1,
    focus_level   INTEGER NOT NULL DEFAULT 0,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, canonical_id)
);

CREATE TABLE IF NOT EXISTS platform_credentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    account_label   TEXT NOT NULL,
    login_identifier TEXT NOT NULL,
    secret_service  TEXT NOT NULL,
    secret_account  TEXT NOT NULL,
    session_path    TEXT,
    session_status  TEXT NOT NULL DEFAULT 'missing',
    last_verified_at TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, login_identifier)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_posts_platform    ON posts(platform);
CREATE INDEX IF NOT EXISTS idx_posts_crawled_at  ON posts(crawled_at);
CREATE INDEX IF NOT EXISTS idx_summaries_post_id ON summaries(post_id);
CREATE INDEX IF NOT EXISTS idx_feedback_post_id  ON feedback(post_id);
CREATE INDEX IF NOT EXISTS idx_feedback_action   ON feedback(action);
CREATE INDEX IF NOT EXISTS idx_tracked_sources_platform ON tracked_sources(platform);
CREATE INDEX IF NOT EXISTS idx_tracked_sources_enabled  ON tracked_sources(is_enabled);
CREATE INDEX IF NOT EXISTS idx_credentials_platform     ON platform_credentials(platform);
"""


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """SQLite 연결을 반환합니다. WAL 모드 + foreign keys 활성화."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """스키마를 초기화합니다. 이미 존재하면 무시."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    _ensure_runs_columns(conn)
    conn.close()


def _ensure_runs_columns(conn: sqlite3.Connection) -> None:
    """기존 DB의 runs 테이블에 누락된 컬럼을 추가합니다."""
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(runs)").fetchall()
    }
    required = {
        "current_platform": "TEXT",
        "runner_pid": "INTEGER",
        "runner_host": "TEXT",
    }
    for name, definition in required.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
    conn.commit()


def _pid_is_alive(pid: Optional[int]) -> bool:
    """현재 호스트에서 PID가 살아 있는지 확인합니다."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_runs(db_path: Optional[Path] = None) -> int:
    """비정상 종료로 남은 running run을 interrupted로 정리합니다."""
    conn = get_connection(db_path)
    _ensure_runs_columns(conn)
    host = socket.gethostname()
    stale_ids: list[int] = []

    rows = conn.execute(
        """
        SELECT id, current_platform, runner_pid, runner_host
        FROM runs
        WHERE status = 'running' AND finished_at IS NULL
        """
    ).fetchall()

    for row in rows:
        runner_host = row["runner_host"]
        runner_pid = row["runner_pid"]
        if runner_host and runner_host != host:
            continue
        if _pid_is_alive(runner_pid):
            continue
        stale_ids.append(row["id"])
        current_platform = row["current_platform"]
        detail = (
            f"프로세스 비정상 종료로 stale run 정리"
            f"{f' (중단 지점: {current_platform})' if current_platform else ''}"
        )
        conn.execute(
            """
            UPDATE runs
            SET finished_at = datetime('now'),
                status = 'interrupted',
                summary = ?
            WHERE id = ?
            """,
            (detail, row["id"]),
        )

    conn.commit()
    conn.close()
    return len(stale_ids)


def save_posts(
    posts: list,
    platform: str,
    source: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Post 객체 리스트를 DB에 저장합니다. 중복은 무시. 저장된 건수 반환."""
    conn = get_connection(db_path)
    saved = 0
    for post in posts:
        data = post.model_dump() if hasattr(post, "model_dump") else post
        # extra 필드: Post 모델의 extra="allow"로 들어온 추가 필드
        known_fields = {
            "platform",
            "author",
            "content",
            "timestamp",
            "url",
            "likes",
            "comments",
            "reposts",
            "views",
            "title",
            "source",
            "external_id",
            "summary",
            "content_markdown",
            "word_count",
        }
        extra_data = {k: v for k, v in data.items() if k not in known_fields}
        extra_json = json.dumps(extra_data, ensure_ascii=False) if extra_data else None

        # external_id가 없으면 content 해시로 대체 (NULL은 UNIQUE 제약 무시됨)
        ext_id = data.get("external_id")
        if not ext_id:
            hash_src = f"{platform}:{data.get('author', '')}:{data.get('content', '')}"
            ext_id = hashlib.sha256(hash_src.encode()).hexdigest()[:16]

        try:
            conn.execute(
                """INSERT INTO posts
                   (platform, source, external_id, author, title, content,
                    url, timestamp, likes, comments, reposts, views,
                    summary, content_markdown, word_count, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, external_id) DO UPDATE SET
                       title = CASE
                           WHEN posts.title IS NULL OR TRIM(posts.title) = '' THEN excluded.title
                           ELSE posts.title
                       END,
                       summary = CASE
                           WHEN posts.summary IS NULL OR TRIM(posts.summary) = '' THEN excluded.summary
                           ELSE posts.summary
                       END,
                       content_markdown = CASE
                           WHEN (
                                posts.content_markdown IS NULL
                                OR TRIM(posts.content_markdown) = ''
                                OR json_extract(posts.extra, '$.subtitle_lang') = 'summary'
                           )
                                AND excluded.content_markdown IS NOT NULL
                                AND TRIM(excluded.content_markdown) != ''
                           THEN excluded.content_markdown
                           ELSE posts.content_markdown
                       END,
                       word_count = CASE
                           WHEN (
                                posts.content_markdown IS NULL
                                OR TRIM(posts.content_markdown) = ''
                                OR json_extract(posts.extra, '$.subtitle_lang') = 'summary'
                           )
                                AND excluded.content_markdown IS NOT NULL
                                AND TRIM(excluded.content_markdown) != ''
                           THEN excluded.word_count
                           ELSE posts.word_count
                       END,
                       extra = CASE
                           WHEN (
                                posts.extra IS NULL
                                OR TRIM(posts.extra) = ''
                                OR json_extract(posts.extra, '$.subtitle_lang') = 'summary'
                           )
                           THEN excluded.extra
                           ELSE posts.extra
                       END
                   WHERE
                       (posts.title IS NULL OR TRIM(posts.title) = '')
                       OR (posts.summary IS NULL OR TRIM(posts.summary) = '')
                       OR (
                           (
                               posts.content_markdown IS NULL
                               OR TRIM(posts.content_markdown) = ''
                               OR json_extract(posts.extra, '$.subtitle_lang') = 'summary'
                           )
                           AND excluded.content_markdown IS NOT NULL
                           AND TRIM(excluded.content_markdown) != ''
                       )
                       OR (posts.extra IS NULL OR TRIM(posts.extra) = '')""",
                (
                    platform,
                    source or data.get("source"),
                    ext_id,
                    data.get("author", ""),
                    data.get("title"),
                    data.get("content", ""),
                    data.get("url"),
                    data.get("timestamp"),
                    data.get("likes"),
                    data.get("comments"),
                    data.get("reposts"),
                    data.get("views"),
                    data.get("summary"),
                    data.get("content_markdown"),
                    data.get("word_count"),
                    extra_json,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                saved += 1
        except sqlite3.Error:
            continue
    conn.commit()
    conn.close()
    return saved


def save_run(status: str = "running", db_path: Optional[Path] = None) -> int:
    """실행 기록을 생성하고 run_id를 반환합니다."""
    cleanup_stale_runs(db_path)
    conn = get_connection(db_path)
    _ensure_runs_columns(conn)
    cursor = conn.execute(
        """
        INSERT INTO runs (status, runner_pid, runner_host)
        VALUES (?, ?, ?)
        """,
        (status, os.getpid(), socket.gethostname()),
    )
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return run_id


def update_run_progress(
    run_id: int,
    current_platform: str,
    summary: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    """현재 처리 중인 플랫폼과 진행 상황을 기록합니다."""
    conn = get_connection(db_path)
    _ensure_runs_columns(conn)
    conn.execute(
        """
        UPDATE runs
        SET current_platform = ?, summary = COALESCE(?, summary)
        WHERE id = ?
        """,
        (current_platform, summary, run_id),
    )
    conn.commit()
    conn.close()


def finish_run(
    run_id: int,
    status: str,
    posts_count: int,
    summary: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    """실행 기록을 완료 상태로 업데이트합니다."""
    conn = get_connection(db_path)
    _ensure_runs_columns(conn)
    conn.execute(
        """UPDATE runs
           SET finished_at = datetime('now'),
               status = ?,
               posts_count = ?,
               summary = COALESCE(?, summary),
               current_platform = NULL
           WHERE id = ?""",
        (status, posts_count, summary, run_id),
    )
    conn.commit()
    conn.close()


def add_feedback(post_id: int, action: str) -> None:
    """사용자 피드백을 저장합니다."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO feedback (post_id, action) VALUES (?, ?)",
        (post_id, action),
    )
    conn.commit()
    conn.close()
