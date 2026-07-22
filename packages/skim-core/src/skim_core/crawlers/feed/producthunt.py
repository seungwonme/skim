"""
@file producthunt.py
@description Product Hunt 크롤러 (RSS)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, List

from ...enrichment import enrich_with_content
from ...feed_config import PRODUCTHUNT_RSS
from ...feed_utils import fetch_feed
from ...models import Post

# PH 피드 content에는 제품 외부 사이트로 가는 리다이렉트(/r/p/{id})가 들어있다.
# /products/{slug} 페이지는 JS SPA + 403이라 본문 추출이 불가하므로, 이 링크를 enrich 대상으로 쓴다.
_RP_HREF = re.compile(r'href="(https://www\.producthunt\.com/r/p/[^"]+)"')
# 피드 content_html 첫 <p>는 제품 태그라인이다 (그 뒤 <p>는 Discussion/Link 링크).
_TAGLINE = re.compile(r"<p>\s*(.*?)\s*</p>", re.DOTALL)


def _redirect_url(item: dict) -> str:
    """피드 항목에서 제품 외부 사이트 리다이렉트 URL을 추출 (없으면 원 URL)."""
    match = _RP_HREF.search(item.get("content_html", ""))
    return match.group(1) if match else item.get("url", "")


def _tagline(item: dict) -> str:
    """피드 content_html 첫 <p>의 제품 태그라인.

    외부 사이트 본문 추출이 실패해도 최소한 이 태그라인은 본문으로 남긴다.
    """
    match = _TAGLINE.search(item.get("content_html", "") or "")
    if not match:
        return ""
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def _item_to_post(item: dict) -> Post:
    """피드 항목을 Post 객체로 변환"""
    extras = {
        key: value
        for key, value in item.items()
        if key in ("enrichment_method", "enrichment_error") and value is not None
    }
    return Post(
        platform="producthunt",
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

        for item in items:
            item["enrich_url"] = _redirect_url(item)
            item["tagline"] = _tagline(item)

        if not no_content:
            enrich_with_content(items)

        return [_item_to_post(item) for item in items]
