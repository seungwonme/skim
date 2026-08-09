"""
@file blogs.py
@description 개인 블로그 구독 크롤러 (RSS, 멀티 피드)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, List, Tuple

from ...db import list_tracked_sources
from ...enrichment import enrich_with_content
from ...feed_config import PERSONAL_BLOGS
from ...feed_utils import fetch_feed
from ...models import Post


def tracked_blog_feeds() -> List[Tuple[str, str]]:
    """수집 대상 블로그 목록 (display_name, feed_url).

    정본은 tracked_sources다. `skim source add`로 등록한 소스가 데일리 수집에
    들어오지 않으면 등록 자체가 무의미해진다. DB가 비었거나 못 읽을 때만
    feed_config로 폴백한다 (youtube 크롤러와 같은 규칙).
    """
    rows = list_tracked_sources("blogs")
    feeds = [(r["display_name"], r["feed_url"]) for r in rows if r.get("feed_url")]

    # 데스크톱 앱의 소스 UI는 youtube 채널용이라 feed_url을 채우지 않는다.
    # 조용히 빠지면 "등록했는데 왜 안 들어오지"가 되므로 이름을 찍어둔다.
    missing = [r["display_name"] for r in rows if not r.get("feed_url")]
    if missing:
        print(
            f"  [!] feed_url 없는 등록 소스 {len(missing)}개 건너뜀: {', '.join(missing)}"
        )
        print("      `skim source add <url>` 로 다시 등록하면 피드 주소가 채워진다.")

    return feeds or list(PERSONAL_BLOGS.items())


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

        feeds = tracked_blog_feeds()

        if debug:
            print(f"[Blogs] 블로그 {len(feeds)}개 피드 수집 중...")

        all_items: List[dict] = []

        for name, feed_url in feeds:
            results = fetch_feed(feed_url, f"blogs/{name}", since)
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
