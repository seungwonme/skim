"""
@file lobsters.py
@description Lobsters 크롤러 (RSS + 게시물별 JSON)

Hacker News와 같은 링크 애그리게이터지만 태그가 붙고 노이즈가 적다. RSS로 목록을
받고, 게시물 JSON이 `comment_plain`/`score`/`depth`를 그대로 줘서 Comment에 1:1로
매핑된다 (HN처럼 Algolia를 따로 부를 필요가 없다).
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import typer

from ...comments import Comment, append_comment_section, render_comment_section
from ...enrichment import enrich_with_content
from ...feed_config import LOBSTERS_ITEM_JSON, LOBSTERS_RSS
from ...feed_utils import fetch_feed, make_retrying_session
from ...models import Post

MAX_COMMENTS = 15
# 게시물당 요청 1건이 늘어난다. 공개 API에 예의를 지키는 간격.
COMMENT_REQUEST_INTERVAL_SECONDS = 1.0

_SESSION = make_retrying_session({"Accept": "application/json"})


def _short_id(item: dict) -> Optional[str]:
    """RSS guid(https://lobste.rs/s/rsztog)에서 short_id를 뽑는다.

    fetch_feed는 guid를 `external_id` 키로 넘긴다. `guid`/`id`로 읽으면 항상 None이라
    댓글이 한 건도 안 붙는다.
    """
    guid = (item.get("external_id") or "").rstrip("/")
    if "/s/" not in guid:
        return None
    short = guid.rsplit("/", 1)[-1].strip()
    return short or None


def fetch_item(short_id: str) -> Optional[dict]:
    """게시물 JSON. 댓글과 본문 폴백을 한 번의 요청으로 함께 받는다."""
    resp = _SESSION.get(LOBSTERS_ITEM_JSON.format(short_id=short_id), timeout=20)
    if resp.status_code != 200:
        return None
    return resp.json()


def comment_section_from(payload: dict) -> Optional[str]:
    """게시물 JSON의 댓글 트리를 본문용 마크다운 섹션으로 만든다."""
    collected: List[Comment] = []
    for raw in payload.get("comments") or []:
        if raw.get("is_deleted") or raw.get("is_moderated"):
            continue
        text = (raw.get("comment_plain") or "").strip()
        if not text:
            continue
        created = raw.get("created_at") or ""
        collected.append(
            Comment(
                author=raw.get("commenting_user") or "unknown",
                text=text,
                score=raw.get("score"),
                created=created[:16].replace("T", " ") or None,
                depth=int(raw.get("depth") or 0),
            )
        )

    return render_comment_section(
        "Lobsters Comments", collected, max_comments=MAX_COMMENTS
    )


class LobstersCrawler:
    """Lobsters RSS 크롤러."""

    platform = "lobsters"

    async def crawl(self, **options: Any) -> List[Post]:
        since: datetime = options.get(
            "since", datetime.now(timezone.utc) - timedelta(days=1)
        )
        count = options.get("count")
        no_content: bool = options.get("no_content", False)

        items = fetch_feed(LOBSTERS_RSS, "lobsters", since)
        items.sort(key=lambda x: x.get("published", ""), reverse=True)
        if count is not None:
            items = items[:count]
        if not items:
            return []

        if not no_content:
            # 링크 원문을 본문으로 채운다. 자체 텍스트 글(url이 lobste.rs)은 원문이
            # 없으므로 JSON의 description_plain이 본문 자리를 대신한다.
            external = [
                item for item in items if "lobste.rs" not in (item.get("url") or "")
            ]
            if external:
                enrich_with_content(external)

        posts = [self._item_to_post(item) for item in items]
        if not no_content:
            self.attach_details(posts, items)
        return posts

    def attach_details(self, posts: List[Post], items: List[dict]) -> None:
        """게시물 JSON 1건으로 댓글 섹션과 본문 폴백을 함께 채운다.

        댓글만 받고 끝내면 원문 추출이 실패한 글이 본문 0자로 저장된다. 같은 응답에
        `description_plain`이 들어 있으므로 추가 요청 없이 최저선을 채울 수 있다.
        """
        failures = 0
        for index, (post, item) in enumerate(zip(posts, items)):
            short_id = _short_id(item)
            if not short_id:
                continue
            if index:
                time.sleep(COMMENT_REQUEST_INTERVAL_SECONDS)
            try:
                payload = fetch_item(short_id)
                section = comment_section_from(payload) if payload else None
            except Exception as exc:  # noqa: BLE001 - 댓글 실패가 게시글 저장을 막지 않는다
                failures += 1
                typer.echo(f"   [!] Lobsters 게시물 조회 실패({short_id}): {exc}")
                continue

            body = (post.content_markdown or "").strip()
            if not body and payload:
                body = (payload.get("description_plain") or "").strip()
                if body:
                    post.content_markdown = body
            if section:
                post.content_markdown = append_comment_section(
                    post.content_markdown or post.content, section
                )

        if failures:
            typer.echo(f"   [!] Lobsters 게시물 조회 실패 {failures}건 (본문만 저장)")

    def _item_to_post(self, item: dict) -> Post:
        extras = {
            key: value
            for key, value in item.items()
            if key in ("enrichment_method", "enrichment_error") and value is not None
        }
        short_id = _short_id(item)
        return Post(
            platform=self.platform,
            author=item.get("author", ""),
            title=item.get("title", ""),
            content="",
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
            external_id=short_id,
            **extras,
        )
