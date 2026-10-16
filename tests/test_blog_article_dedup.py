"""새 RSS 소스를 추가해도 이미 보관한 본문을 중복 저장하지 않는지 검증한다."""

import sqlite3

import pytest

from skim_core.db import init_db, save_posts
from skim_core.models import Post


@pytest.fixture()
def article_db(tmp_path):
    db_path = tmp_path / "skim.db"
    init_db(db_path)
    return db_path


def article(platform, external_id, body, **extra):
    return Post(
        platform=platform,
        external_id=external_id,
        author="Writer",
        title="An article",
        content="",
        timestamp="2026-10-16T00:00:00+00:00",
        content_markdown=body,
        url=extra.pop("url", "https://example.com/article"),
        **extra,
    )


def stored_rows(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT platform, external_id, content_markdown, author FROM posts ORDER BY id"
        ).fetchall()


def test_skips_blog_body_already_preserved_with_discussion(article_db):
    existing = article(
        "hackernews",
        "hn-1",
        "Article body.\n\n---\n\n## Hacker News Comments\nA reply.",
    )
    save_posts([existing], "hackernews", db_path=article_db)
    before = stored_rows(article_db)

    saved = save_posts(
        [
            article(
                "blogs",
                "feed-1",
                "Article   body.",
                url="http://www.example.com/article/?utm_source=rss#top",
            )
        ],
        "blogs",
        db_path=article_db,
    )

    assert saved == 0
    assert stored_rows(article_db) == before


@pytest.mark.parametrize(
    "existing_body",
    [None, "", "## Hacker News Comments\nOnly discussion.", "A short summary."],
)
def test_incomplete_existing_article_does_not_block_full_body(
    article_db, existing_body
):
    save_posts(
        [article("hackernews", "hn-1", existing_body)], "hackernews", db_path=article_db
    )
    incoming = article("blogs", "feed-1", "The complete original article body.")

    assert save_posts([incoming], "blogs", db_path=article_db) == 1
    assert stored_rows(article_db)[1][2] == incoming.content_markdown


def test_new_information_at_the_same_url_is_preserved(article_db):
    save_posts([article("blogs", "old", "Original body.")], "blogs", db_path=article_db)
    updated = article("blogs", "new", "Original body. New research findings.")

    assert save_posts([updated], "blogs", db_path=article_db) == 1
    assert len(stored_rows(article_db)) == 2


def test_blog_upsert_still_repairs_an_existing_empty_body(article_db):
    save_posts([article("blogs", "feed-1", "")], "blogs", db_path=article_db)
    save_posts(
        [article("hackernews", "hn-1", "Complete body.")],
        "hackernews",
        db_path=article_db,
    )

    assert (
        save_posts(
            [article("blogs", "feed-1", "Complete body.")], "blogs", db_path=article_db
        )
        == 1
    )
    assert stored_rows(article_db)[0][2] == "Complete body."


def test_same_body_at_different_urls_and_community_posts_are_preserved(article_db):
    posts = [
        article("blogs", "a", "Shared body."),
        article("blogs", "b", "Shared body.", url="https://example.com/another"),
        article("hackernews", "hn-1", "Shared body."),
        article("hackernews", "hn-2", "Shared body."),
    ]

    assert save_posts(posts, "blogs", db_path=article_db) == 4
    assert len(stored_rows(article_db)) == 4


def test_repeated_feed_items_in_one_batch_are_skipped(article_db):
    posts = [
        article("blogs", "a", "Shared body."),
        article("blogs", "b", "Shared body."),
    ]

    assert save_posts(posts, "blogs", db_path=article_db) == 1
    assert len(stored_rows(article_db)) == 1
