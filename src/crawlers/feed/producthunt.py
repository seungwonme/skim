"""
@file producthunt.py
@description Product Hunt 크롤러 (RSS)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, List

from feed_config import PRODUCTHUNT_RSS

from ...enrichment import enrich_with_content
from ...feed_utils import fetch_feed
from ...models import Post


def _item_to_post(item: dict) -> Post:
    """피드 항목을 Post 객체로 변환"""
    return Post(
        platform="producthunt",
        author=item.get("author", ""),
        title=item.get("title", ""),
        content=item.get("title", ""),
        timestamp=item.get("published", ""),
        url=item.get("url", ""),
        summary=item.get("summary", ""),
        source=item.get("platform", ""),
        content_markdown=item.get("content_markdown", ""),
        word_count=item.get("word_count"),
    )


class ProductHuntCrawler:
    """Product Hunt RSS 피드 크롤러"""

    platform: str = "producthunt"

    async def crawl(self, **options: Any) -> List[Post]:
        """
        Product Hunt 피드를 수집합니다.

        Options:
            since (datetime): 이 시점 이후의 항목만 수집
            no_content (bool): 콘텐츠 enrichment 스킵
            debug (bool): 디버그 모드
        """
        since: datetime = options.get(
            "since",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        no_content: bool = options.get("no_content", False)
        debug: bool = options.get("debug", False)

        if debug:
            print("[PH] Product Hunt 피드 수집 중...")

        items = fetch_feed(PRODUCTHUNT_RSS, "producthunt", since)

        if debug:
            print(f"  -> {len(items)}개 항목")

        if not items:
            return []

        items.sort(key=lambda x: x.get("published", ""), reverse=True)

        if not no_content:
            enrich_with_content(items)

        return [_item_to_post(item) for item in items]
