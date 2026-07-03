"""
@file test_word_count_normalization.py
@description save_posts가 word_count 미계산 시 실제 본문에서 단어 수를 채우는지 회귀 테스트.
              API형(linkedin/reddit)은 content, Feed형은 content_markdown 기준.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from skim_core.db import get_connection, init_db, save_posts
from skim_core.models import Post

_TS = "2026-07-03T00:00:00+00:00"


def _wc(platform: str, db_path: Path) -> int | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT word_count FROM posts WHERE external_id = ?", (platform + "1",)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_word_count_filled_from_body() -> None:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    posts = [
        # API형: 본문이 content -> 단어 수 계산
        Post(platform="linkedin", author="a", content="one two three four five",
             timestamp=_TS, external_id="linkedin1"),
        Post(platform="reddit", author="b", content="alpha beta gamma",
             timestamp=_TS, external_id="reddit1"),
        # Feed형 본문 없음(제목만) -> content로 세지 않는다
        Post(platform="hackernews", author="c", content="just a title",
             content_markdown="", timestamp=_TS, external_id="hackernews1"),
        # Feed형 본문 있음 -> content_markdown 기준
        Post(platform="geeknews", author="d", content="title",
             content_markdown="body has four words", timestamp=_TS, external_id="geeknews1"),
    ]
    save_posts(posts, platform="feed", db_path=tmp)

    assert _wc("linkedin", tmp) == 5
    assert _wc("reddit", tmp) == 3
    assert _wc("hackernews", tmp) is None
    assert _wc("geeknews", tmp) == 4


def test_existing_word_count_preserved() -> None:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    post = Post(platform="arxiv", author="a", content="t", content_markdown="a b c",
                word_count=999, timestamp=_TS, external_id="arxiv1")
    save_posts([post], platform="arxiv", db_path=tmp)
    assert _wc("arxiv", tmp) == 999
