"""선택 행의 원문 복구가 댓글, 출처, 동시 변경을 보존하는지 검증한다."""

import importlib.util
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from skim_cli import cli
from skim_core import db
from skim_core.db import init_db, save_posts
from skim_core.models import Post

SPEC = importlib.util.spec_from_file_location(
    "missing_content_repair",
    Path(__file__).parents[1] / "scripts/backfill_missing_content.py",
)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


@pytest.fixture()
def connection(tmp_path):
    db_path = tmp_path / "skim.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def insert(conn, platform="hackernews", body="", extra=None, external_id="123"):
    cur = conn.execute(
        "INSERT INTO posts(platform,source,external_id,author,content,title,url,timestamp,content_markdown,extra) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            platform,
            platform + "/show" if platform == "hackernews" else platform,
            external_id,
            "writer",
            "",
            "An article",
            "https://news.ycombinator.com/item?id=123"
            if platform == "hackernews"
            else "https://www.producthunt.com/products/demo",
            "2026-10-16T00:00:00+00:00",
            body,
            json.dumps(extra or {}),
        ),
    )
    conn.commit()
    return cur.lastrowid


def test_explicit_ids_limit_failed_recovery_and_allow_producthunt(connection):
    wanted = insert(
        connection,
        "producthunt",
        "Tagline and comments",
        {"enrichment_method": "failed"},
    )
    insert(connection, body="", external_id="another")
    assert [
        r["id"]
        for r in repair.fetch_targets(connection, ids=[wanted], include_failed=True)
    ] == [wanted]
    assert all(
        r["platform"] != "producthunt"
        for r in repair.fetch_targets(connection, include_failed=True)
    )


def test_original_url_is_used_and_existing_comments_survive(connection):
    comments = "## Hacker News Comments\n\n- A comment worth keeping."
    row_id = insert(
        connection,
        body=comments,
        extra={
            "original_url": "https://example.com/original",
            "enrichment_method": "failed",
            "other_metadata": "keep",
        },
    )
    row = repair.fetch_targets(connection, ids=[row_id], include_failed=True)[0]

    def enrich(items):
        assert items[0]["url"] == "https://example.com/original"
        items[0].update(
            content_markdown="Recovered original article.",
            enrichment_method="trafilatura",
        )

    with (
        patch.object(repair, "enrich_with_content", side_effect=enrich),
        patch.object(repair, "fetch_hn_discussion", return_value=None),
    ):
        updates = repair.run_batch([row])
    assert repair.save(connection, updates) == 1
    stored = connection.execute("SELECT * FROM posts WHERE id=?", (row_id,)).fetchone()
    assert "Recovered original article." in stored["content_markdown"]
    assert comments in stored["content_markdown"]
    assert stored["url"] == row["url"]
    extra = json.loads(stored["extra"])
    assert extra["other_metadata"] == "keep"
    assert extra["enrichment_method"] == "trafilatura"


def test_failure_keeps_existing_body_and_records_partial(connection):
    body = "## Hacker News Comments\n\n- Existing discussion."
    row_id = insert(
        connection,
        body=body,
        extra={
            "original_url": "https://example.com/blocked",
            "enrichment_method": "failed",
        },
    )
    row = repair.fetch_targets(connection, ids=[row_id], include_failed=True)[0]
    update = repair._update(
        row,
        {
            "content_markdown": "",
            "enrichment_method": "failed",
            "enrichment_error": "HTTP 403",
        },
    )
    repair.save(connection, [update])
    stored = connection.execute("SELECT * FROM posts WHERE id=?", (row_id,)).fetchone()
    assert stored["content_markdown"] == body
    assert json.loads(stored["extra"])["content_status"] == "partial"


def test_concurrent_edit_is_not_overwritten(connection):
    row_id = insert(connection)
    row = repair.fetch_targets(connection, ids=[row_id])[0]
    update = repair._update(
        row,
        {"content_markdown": "A recovered article", "enrichment_method": "trafilatura"},
    )
    connection.execute(
        "UPDATE posts SET content_markdown='Another writer filled this' WHERE id=?",
        (row_id,),
    )
    connection.commit()
    assert repair.save(connection, [update]) == 0
    assert (
        connection.execute(
            "SELECT content_markdown FROM posts WHERE id=?", (row_id,)
        ).fetchone()[0]
        == "Another writer filled this"
    )


def test_title_only_source_is_not_retried_as_missing_article(connection):
    row_id = insert(connection)
    row = repair.fetch_targets(connection, ids=[row_id])[0]
    with (
        patch.object(
            repair,
            "fetch_hn_discussion",
            return_value={"story_text": "", "comments": [], "url": None},
        ),
        patch.object(repair, "enrich_with_content") as article,
    ):
        updates = repair.run_batch([row])
    article.assert_not_called()
    repair.save(connection, updates)
    assert repair.fetch_targets(connection, ids=[row_id], include_failed=True) == []
    assert (
        json.loads(
            connection.execute(
                "SELECT extra FROM posts WHERE id=?", (row_id,)
            ).fetchone()[0]
        )["content_status"]
        == "source_empty"
    )


def test_source_empty_status_clears_when_text_later_arrives(tmp_path):
    db_path = tmp_path / "skim.db"
    init_db(db_path)
    values = dict(
        platform="hackernews",
        external_id="123",
        author="writer",
        content="",
        title="Title",
        url="https://example.com/story",
        timestamp="2026-10-16",
    )
    save_posts(
        [Post(**values, content_status="source_empty")], "hackernews", db_path=db_path
    )
    save_posts(
        [Post(**values, content_markdown="A discussion appeared")],
        "hackernews",
        db_path=db_path,
    )
    with sqlite3.connect(db_path) as conn:
        body, extra = conn.execute(
            "SELECT content_markdown,extra FROM posts"
        ).fetchone()
    assert body == "A discussion appeared"
    assert "source_empty" not in (extra or "")


def test_product_recovery_uses_feed_outbound_and_keeps_comments(connection):
    comments = "## Product Hunt Comments\n\n- Keep this review."
    row_id = insert(
        connection,
        "producthunt",
        "Old tagline\n\n---\n\n" + comments,
        {"enrichment_method": "failed"},
        external_id="tag:ph:1",
    )
    row = repair.fetch_targets(connection, ids=[row_id], include_failed=True)[0]
    feed = [
        {
            "external_id": "tag:ph:1",
            "url": row["url"],
            "content_html": '<p>Product tagline</p><a href="https://www.producthunt.com/r/p/1">Link</a>',
        }
    ]

    def enrich(items):
        assert items[0]["enrich_url"] == "https://www.producthunt.com/r/p/1"
        items[0].update(
            content_markdown="Complete product description.",
            enrichment_method="trafilatura",
        )

    with (
        patch.object(repair, "fetch_feed", return_value=feed),
        patch.object(repair, "enrich_with_content", side_effect=enrich),
    ):
        updates = repair.run_batch([row])
    repair.save(connection, updates)
    body = connection.execute(
        "SELECT content_markdown FROM posts WHERE id=?", (row_id,)
    ).fetchone()[0]
    assert body.startswith("Complete product description.")
    assert comments in body


def test_successful_execution_reports_failed_article_even_with_comments(
    monkeypatch, tmp_path
):
    post = Post(
        platform="hackernews",
        author="writer",
        content="",
        content_markdown="## Hacker News Comments\nA discussion",
        timestamp="2026-10-16",
        url="https://example.com/story",
        enrichment_method="failed",
    )
    monkeypatch.setattr(cli, "run_single_crawler", AsyncMock(return_value=[post]))
    monkeypatch.setattr(cli, "save_posts_to_file", lambda *args: None)
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    result = CliRunner().invoke(cli.app, ["crawl", "hackernews", "--count", "1"])
    assert result.exit_code == 0
    assert "원문 추출 실패" in result.output
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT status,summary FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["status"] == "success"
    assert "원문 추출 실패 1건" in row["summary"]


def test_doctor_separates_missing_articles_from_empty_sources():
    init_db()
    values = dict(
        platform="hackernews", author="writer", content="", timestamp="2026-10-16"
    )
    save_posts(
        [
            Post(
                **values,
                external_id="failed",
                content_markdown="Some comments",
                enrichment_method="failed",
            ),
            Post(**values, external_id="empty", content_status="source_empty"),
        ],
        "hackernews",
    )
    report = {}
    cli._doctor_read_db(report, db.DB_PATH, None, 5)
    row = report["recent_thin"][0]
    assert row["article_failed"] == 1
    assert row["source_empty"] == 1
    assert row["thin"] == 0
