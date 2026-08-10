"""
@file arxiv.py
@description arXiv cs.AI 논문 크롤러 (Atom API)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, List

import feedparser

from ...enrichment import enrich_papers_with_content
from ...feed_config import ARXIV_CATEGORIES, arxiv_api_url
from ...feed_utils import KST, is_within_range
from ...models import Post


class ArxivCrawler:
    platform = "arxiv"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 50)
        since = options.get("since")
        no_content = options.get("no_content", False)

        if not since:
            since = (datetime.now(KST) - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        items: List[dict] = []
        seen_urls: set[str] = set()
        for category in ARXIV_CATEGORIES:
            feed = feedparser.parse(arxiv_api_url(category))
            for entry in feed.entries:
                pub = entry.get("published", "")
                try:
                    entry_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
                if not is_within_range(entry_dt, since):
                    continue

                url = entry.get("link", "")
                # 논문은 여러 카테고리에 교차 등록된다. 같은 abs 링크가 두 번 들어오면
                # enrichment도 두 번 돌고 정렬 뒤 상한만 잡아먹는다.
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
                items.append(
                    {
                        "platform": "arxiv",
                        "title": re.sub(r"\s+", " ", entry.get("title", "")).strip(),
                        "url": url,
                        "author": authors,
                        "published": entry_dt.astimezone(timezone.utc).isoformat(),
                        "summary": re.sub(
                            r"\s+", " ", entry.get("summary", "")
                        ).strip()[:500],
                        "abstract": re.sub(
                            r"\s+", " ", entry.get("summary", "")
                        ).strip(),
                        "arxiv_category": category,
                    }
                )

        items.sort(key=lambda x: x.get("published", ""), reverse=True)
        items = items[:count]

        if not no_content and items:
            enrich_papers_with_content(items)

        return [self._item_to_post(item) for item in items]

    def _item_to_post(self, item: dict) -> Post:
        extras = {
            key: value
            for key, value in item.items()
            if key in ("enrichment_method", "enrichment_error", "arxiv_category")
            and value is not None
        }
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content="",
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
            **extras,
        )
