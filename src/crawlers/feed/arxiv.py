"""
@file arxiv.py
@description arXiv cs.AI 논문 크롤러 (Atom API)
"""

import re
from datetime import datetime, timedelta
from typing import Any, List

import feedparser

from feed_config import ARXIV_API_URL

from ...enrichment import enrich_papers_with_content
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

        feed = feedparser.parse(ARXIV_API_URL)

        items: List[dict] = []
        for entry in feed.entries:
            pub = entry.get("published", "")
            try:
                entry_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if not is_within_range(entry_dt, since):
                continue

            authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
            items.append(
                {
                    "platform": "arxiv",
                    "title": re.sub(r"\s+", " ", entry.get("title", "")).strip(),
                    "url": entry.get("link", ""),
                    "author": authors,
                    "published": entry_dt.astimezone(KST).isoformat(),
                    "summary": re.sub(r"\s+", " ", entry.get("summary", "")).strip()[:500],
                }
            )

        items.sort(key=lambda x: x.get("published", ""), reverse=True)
        items = items[:count]

        if not no_content and items:
            enrich_papers_with_content(items)

        return [self._item_to_post(item) for item in items]

    def _item_to_post(self, item: dict) -> Post:
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content=item.get("title", ""),
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
        )
