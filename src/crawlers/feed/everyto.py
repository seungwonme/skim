"""
@file everyto.py
@description Every.to 크롤러 (RSS, 멀티 피드)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, List

from feed_config import EVERY_TO_FEEDS

from ...enrichment import enrich_with_content
from ...feed_utils import fetch_feed
from ...models import Post


def _item_to_post(item: dict) -> Post:
    """피드 항목을 Post 객체로 변환"""
    return Post(
        platform="every.to",
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


class EveryToCrawler:
    """Every.to 멀티 칼럼 RSS 피드 크롤러"""

    platform: str = "every.to"

    async def crawl(self, **options: Any) -> List[Post]:
        """
        Every.to 칼럼 피드를 수집합니다.

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

        if debug:
            print(f"[Every] Every.to {len(EVERY_TO_FEEDS)}개 칼럼 피드 수집 중...")

        all_items: List[dict] = []

        for name, feed_url in EVERY_TO_FEEDS.items():
            results = fetch_feed(feed_url, f"every.to/{name}", since)
            if results and debug:
                print(f"  -> {name}: {len(results)}개")
            all_items.extend(results)

        if debug:
            print(f"  -> 총 {len(all_items)}개 글")

        if not all_items:
            return []

        all_items.sort(key=lambda x: x.get("published", ""), reverse=True)

        if not no_content:
            enrich_with_content(all_items)

        return [_item_to_post(item) for item in all_items]
