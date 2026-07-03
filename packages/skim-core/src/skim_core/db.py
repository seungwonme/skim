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
import sys
from pathlib import Path
from typing import Optional

from .paths import DATA_DIR

DB_PATH = DATA_DIR / "skim.db"

# API형 플랫폼은 본문이 content_markdown이 아니라 content에 담긴다 (word_count 정규화용).
_API_BODY_PLATFORMS = {"linkedin", "threads", "x", "reddit"}

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
CREATE INDEX IF NOT EXISTS idx_posts_platform_url ON posts(platform, url)
    WHERE url IS NOT NULL AND TRIM(url) <> '';
CREATE INDEX IF NOT EXISTS idx_summaries_post_id ON summaries(post_id);
CREATE INDEX IF NOT EXISTS idx_feedback_post_id  ON feedback(post_id);
CREATE INDEX IF NOT EXISTS idx_feedback_action   ON feedback(action);
CREATE INDEX IF NOT EXISTS idx_tracked_sources_platform ON tracked_sources(platform);
CREATE INDEX IF NOT EXISTS idx_tracked_sources_enabled  ON tracked_sources(is_enabled);
CREATE INDEX IF NOT EXISTS idx_credentials_platform     ON platform_credentials(platform);
"""

# Phase 2 — research_runs 테이블 (auto refresh attempt log).
# `posts` 변경 금지. v1 schema 진입 시 PRAGMA user_version=1.
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
CREATE INDEX IF NOT EXISTS idx_research_runs_topic       ON research_runs(topic);
CREATE INDEX IF NOT EXISTS idx_research_runs_started_at  ON research_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_research_runs_backoff     ON research_runs(tokens_key, sources_key, started_at);
CREATE INDEX IF NOT EXISTS idx_research_runs_status      ON research_runs(status);
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
    _migrate_research_runs(conn)
    conn.close()


def migrate_canonical_body(db_path: Optional[Path] = None) -> dict:
    """기존 데이터를 정본 본문 모델로 이행한다 (일회성, 멱등).

    1. API형(linkedin/threads/x/reddit): 본문이 content에만 있던 과거 행을
       content_markdown으로 승격하고 word_count를 채운다.
    2. Feed형: content에 제목이 중복 저장된 과거 행에서 content를 비운다
       (제목은 title, 본문은 content_markdown에 있으므로).

    Returns: 변경 건수 요약 dict.
    """
    conn = get_connection(db_path)
    api_list = ",".join(f"'{p}'" for p in sorted(_API_BODY_PLATFORMS))
    try:
        promoted = 0
        rows = conn.execute(
            f"""SELECT id, content FROM posts
                WHERE platform IN ({api_list})
                  AND (content_markdown IS NULL OR TRIM(content_markdown) = '')
                  AND content IS NOT NULL AND TRIM(content) != ''"""
        ).fetchall()
        for row_id, content in rows:
            body = content.strip()
            conn.execute(
                "UPDATE posts SET content_markdown = ?, "
                "word_count = COALESCE(NULLIF(word_count, 0), ?) WHERE id = ?",
                (body, len(body.split()), row_id),
            )
            promoted += 1

        cleared = conn.execute(
            f"""UPDATE posts SET content = ''
                WHERE platform NOT IN ({api_list})
                  AND content IS NOT NULL AND content != '' AND content = title"""
        ).rowcount
        conn.commit()
        return {"api_promoted": promoted, "feed_content_cleared": cleared}
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """SQLite ADD COLUMN IF NOT EXISTS 대체 (3.43 까지도 미지원).

    `row_factory` 와 무관하게 동작 (PRAGMA table_info 의 인덱스 1 이 name).
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_research_runs(conn: sqlite3.Connection) -> None:
    """research_runs 멱등 migration. fresh DB / v0 / v1 모두 안전."""
    conn.executescript(RESEARCH_RUNS_CREATE_SQL)
    _ensure_column(conn, "research_runs", "days_per_platform", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "research_runs", "window_expanded", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "research_runs", "newly_fetched", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "research_runs", "runner_pid", "INTEGER")
    _ensure_column(conn, "research_runs", "runner_host", "TEXT")
    if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
        conn.execute("PRAGMA user_version = 1")
    conn.commit()


def _ensure_runs_columns(conn: sqlite3.Connection) -> None:
    """기존 DB의 runs 테이블에 누락된 컬럼을 추가합니다."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
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

    rows = conn.execute("""
        SELECT id, current_platform, runner_pid, runner_host
        FROM runs
        WHERE status = 'running' AND finished_at IS NULL
        """).fetchall()

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
    errors = 0
    last_error: Optional[str] = None
    for post in posts:
        data = post.model_dump() if hasattr(post, "model_dump") else post

        # 본문 정본화: API형 플랫폼은 본문이 content에 오므로 content_markdown으로 승격한다.
        # 이렇게 하면 content_markdown이 전 플랫폼 공통 본문 필드가 된다 (Feed형은 이미 그렇다).
        # 판정과 저장 모두 Post의 platform을 우선한다 (인자는 fallback).
        # 혼합 배치에서 인자 platform으로 저장하면 row가 오라벨링된다.
        row_platform = data.get("platform") or platform

        if row_platform in _API_BODY_PLATFORMS and not (
            data.get("content_markdown") or ""
        ).strip():
            body = (data.get("content") or "").strip()
            if body:
                data["content_markdown"] = body

        # word_count 정규화: 미계산이면 정본 본문(content_markdown)에서 센다.
        if not data.get("word_count"):
            body = (data.get("content_markdown") or "").strip()
            if body:
                data["word_count"] = len(body.split())

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

        url = (data.get("url") or "").strip()

        # external_id가 없으면 해시로 대체 (NULL은 UNIQUE 제약 무시됨).
        # URL이 있으면 URL 기준으로 해시해 "같은 작성자의 동일 본문, 다른 글" 충돌을 막고,
        # 재크롤 시 본문이 바뀌어도 같은 글로 인식되게 한다.
        ext_id = data.get("external_id")
        has_own_id = bool(ext_id)
        if not ext_id:
            if url:
                hash_src = f"{row_platform}:{data.get('author', '')}:{url}"
            else:
                hash_src = f"{row_platform}:{data.get('author', '')}:{data.get('content', '')}"
            ext_id = hashlib.sha256(hash_src.encode()).hexdigest()[:16]

        # 해시 id인 경우만 같은 URL의 기존 row에 병합한다. 크롤러가 준 진짜 id를
        # URL 일치만으로 덮어쓰면 같은 링크의 서로 다른 글(예: HN 중복 제출)이 유실된다.
        if url and not has_own_id:
            row = conn.execute(
                """
                SELECT external_id FROM posts
                WHERE platform = ? AND url = ?
                ORDER BY id
                LIMIT 1
                """,
                (row_platform, url),
            ).fetchone()
            if row and row["external_id"]:
                ext_id = row["external_id"]

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
                                OR json_extract(posts.extra, '$.enrichment_method') = 'failed'
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
                                OR json_extract(posts.extra, '$.enrichment_method') = 'failed'
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
                                OR json_extract(posts.extra, '$.enrichment_method') = 'failed'
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
                               OR json_extract(posts.extra, '$.enrichment_method') = 'failed'
                           )
                           AND excluded.content_markdown IS NOT NULL
                           AND TRIM(excluded.content_markdown) != ''
                       )
                       OR (posts.extra IS NULL OR TRIM(posts.extra) = '')
                       OR json_extract(posts.extra, '$.enrichment_method') = 'failed'""",
                (
                    row_platform,
                    source or data.get("source"),
                    ext_id,
                    data.get("author", ""),
                    data.get("title"),
                    data.get("content", ""),
                    url or None,
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
        except sqlite3.Error as e:
            # 개별 row 실패는 배치를 살리되, 전량 실패가 성공처럼 보이지 않게 집계한다.
            errors += 1
            last_error = str(e)
            continue
    conn.commit()
    conn.close()
    if errors:
        print(
            f"[skim] save_posts: {errors}개 저장 실패 (마지막 오류: {last_error})",
            file=sys.stderr,
        )
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
