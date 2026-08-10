"""
@file blogs.py
@description 개인 블로그 구독 크롤러 (RSS, 멀티 피드)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, List

from ...enrichment import enrich_with_content
from ...feed_config import PERSONAL_BLOGS
from ...feed_utils import fetch_feed
from ...models import Post
from ...source_registry import resolve_feed_sources


def _item_to_post(item: dict) -> Post:
    """피드 항목을 Post 객체로 변환"""
    extras = {
        key: value
        for key, value in item.items()
        if key
        in (
            "enrichment_method",
            "enrichment_error",
            "description",
            "image",
            "original_url",
        )
        and value is not None
    }
    return Post(
        platform="blogs",
        author=item.get("author", ""),
        title=item.get("title", ""),
        content="",
        timestamp=item.get("published", ""),
        url=item.get("url", ""),
        summary=item.get("summary", ""),
        source=item.get("platform", ""),
        content_markdown=item.get("content_markdown", ""),
        word_count=item.get("word_count"),
        **extras,
    )


class BlogsCrawler:
    """개인 블로그 멀티 RSS 피드 크롤러"""

    platform: str = "blogs"

    async def crawl(self, **options: Any) -> List[Post]:
        """
        구독 중인 개인 블로그의 RSS 피드를 수집합니다.

        Options:
            since (datetime): 이 시점 이후의 글만 수집
            no_content (bool): 콘텐츠 enrichment 스킵
            debug (bool): 디버그 모드
        """
        since: datetime = options.get(
            "since",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        no_content: bool = options.get("no_content", False)
        debug: bool = options.get("debug", False)

        feeds = resolve_feed_sources("blogs", PERSONAL_BLOGS)

        if debug:
            print(f"[Blogs] 블로그 {len(feeds)}개 피드 수집 중...")

        all_items: List[dict] = []

        for source in feeds:
            name = source["name"]
            results = fetch_feed(source["feed_url"], f"blogs/{name}", since)
            if results and debug:
                print(f"  -> {name}: {len(results)}개")
            all_items.extend(results)

        if debug:
            print(f"  -> 총 {len(all_items)}개 글")

        if not all_items:
            return []

        all_items.sort(key=lambda x: x.get("published", ""), reverse=True)
        # CLI가 마지막에 posts[:count]로 자르므로, 버려질 항목을 enrichment하지 않는다.
        if options.get("count") is not None:
            all_items = all_items[: options["count"]]

        if not no_content:
            enrich_with_content(all_items)

        return [_item_to_post(item) for item in all_items]
