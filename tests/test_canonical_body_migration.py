"""
@file test_canonical_body_migration.py
@description migrate_canonical_body가 API형 본문을 content_markdown으로 승격하고
              Feed형 content 제목 중복을 제거하는지, 멱등한지 회귀 테스트.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from skim_core.db import get_connection, init_db, migrate_canonical_body, save_posts
from skim_core.models import Post

_TS = "2026-07-03T00:00:00+00:00"


def _seed_legacy(db_path: Path) -> None:
    """save 정규화를 우회해 '과거' 데이터(정본화 전 상태)를 직접 삽입한다."""
    conn = get_connection(db_path)
    conn.executemany(
        "INSERT INTO posts (platform, external_id, author, title, content, content_markdown,"
        " word_count, timestamp) VALUES (?,?,?,?,?,?,?,?)",
        [
            # API형: 본문이 content에만, content_markdown 비어있음
            ("reddit", "r1", "u1", None, "one two three four", "", None, _TS),
            # Feed형: content == title (중복), 본문은 content_markdown
            ("geeknews", "g1", "u2", "A Great Title", "A Great Title", "full body here", 3, _TS),
        ],
    )
    conn.commit()
    conn.close()


def test_migration_promotes_and_clears() -> None:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    _seed_legacy(tmp)

    result = migrate_canonical_body(tmp)
    assert result == {"api_promoted": 1, "feed_content_cleared": 1}

    conn = get_connection(tmp)
    reddit = conn.execute(
        "SELECT content_markdown, word_count FROM posts WHERE external_id='r1'"
    ).fetchone()
    geeknews = conn.execute(
        "SELECT content, title, content_markdown FROM posts WHERE external_id='g1'"
    ).fetchone()
    conn.close()

    # API형: content -> content_markdown 승격 + word_count 계산
    assert reddit[0] == "one two three four"
    assert reddit[1] == 4
    # Feed형: content 비워지고 title/body는 유지
    assert geeknews[0] == ""
    assert geeknews[1] == "A Great Title"
    assert geeknews[2] == "full body here"


def test_migration_idempotent() -> None:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    _seed_legacy(tmp)
    migrate_canonical_body(tmp)
    # 두 번째 실행은 변경 없음
    assert migrate_canonical_body(tmp) == {"api_promoted": 0, "feed_content_cleared": 0}


def test_save_posts_produces_canonical_body() -> None:
    # 새로 저장되는 데이터는 마이그레이션 없이도 정본 상태여야 한다.
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    save_posts(
        [Post(platform="linkedin", author="a", content="alpha beta gamma",
              timestamp=_TS, external_id="l1")],
        platform="linkedin", db_path=tmp,
    )
    conn = get_connection(tmp)
    row = conn.execute(
        "SELECT content_markdown, word_count FROM posts WHERE external_id='l1'"
    ).fetchone()
    conn.close()
    assert row[0] == "alpha beta gamma"
    assert row[1] == 3
