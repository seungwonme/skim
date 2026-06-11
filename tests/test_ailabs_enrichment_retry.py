"""
@file test_ailabs_enrichment_retry.py
@description `enrichment_method=failed` 마커가 붙은 post를 재크롤링 시
              DB upsert가 실제 본문으로 덮어쓰는지 회귀 테스트.
"""

from __future__ import annotations

import json
from pathlib import Path

from skim_core.db import get_connection, init_db, save_posts
from skim_core.models import Post


def _make_post(
    *,
    external_id: str = "www.anthropic.com/engineering/foo",
    content_markdown: str = "",
    word_count: int = 0,
    enrichment_method: str = "failed",
    enrichment_error: str | None = "content not usable",
) -> Post:
    extra: dict = {"enrichment_method": enrichment_method}
    if enrichment_error is not None:
        extra["enrichment_error"] = enrichment_error
    return Post(
        platform="ailabs",
        author="Anthropic",
        title="Example title",
        content="Example title",
        timestamp="2026-04-20T09:00:00+09:00",
        url="https://www.anthropic.com/engineering/foo",
        summary="",
        source="ailabs/Anthropic Engineering",
        external_id=external_id,
        content_markdown=content_markdown,
        word_count=word_count,
        **extra,
    )


def _fetch_row(db_path: Path) -> dict:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM posts").fetchone()
    conn.close()
    assert row is not None
    return dict(row)


def test_failed_enrichment_gets_overwritten_by_successful_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "skim.db"
    init_db(db_path)

    # 1차: failed 상태로 저장
    failed_post = _make_post(
        content_markdown="",
        word_count=0,
        enrichment_method="failed",
        enrichment_error="content not usable",
    )
    save_posts([failed_post], "ailabs", db_path=db_path)

    row = _fetch_row(db_path)
    assert row["content_markdown"] in (None, "")
    assert json.loads(row["extra"]).get("enrichment_method") == "failed"

    # 2차: 동일 external_id로 실제 본문 도착
    good_post = _make_post(
        content_markdown="This is a genuine article body with plenty of words " * 30,
        word_count=300,
        enrichment_method="defuddle",
        enrichment_error=None,
    )
    save_posts([good_post], "ailabs", db_path=db_path)

    row = _fetch_row(db_path)
    assert row["content_markdown"].startswith("This is a genuine article body")
    assert row["word_count"] == 300
    extra = json.loads(row["extra"])
    assert extra.get("enrichment_method") == "defuddle"
    assert "enrichment_error" not in extra


def test_successful_enrichment_is_not_overwritten_by_later_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "skim.db"
    init_db(db_path)

    good_post = _make_post(
        content_markdown="Genuine article body " * 30,
        word_count=150,
        enrichment_method="defuddle",
        enrichment_error=None,
    )
    save_posts([good_post], "ailabs", db_path=db_path)

    # 재크롤링 시 defuddle이 일시적으로 실패했다고 가정
    transient_failure = _make_post(
        content_markdown="",
        word_count=0,
        enrichment_method="failed",
        enrichment_error="transient fetch failure",
    )
    save_posts([transient_failure], "ailabs", db_path=db_path)

    row = _fetch_row(db_path)
    # 기존 좋은 본문이 그대로 남아있어야 한다
    assert row["content_markdown"].startswith("Genuine article body")
    assert row["word_count"] == 150
    extra = json.loads(row["extra"])
    assert extra.get("enrichment_method") == "defuddle"
