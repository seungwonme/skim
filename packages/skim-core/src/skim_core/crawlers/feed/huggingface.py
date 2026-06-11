"""
@file huggingface.py
@description HuggingFace Daily Papers 크롤러 (JSON API)
"""

import re
from datetime import datetime, timezone
from typing import Any, List

import requests

from ...enrichment import enrich_papers_with_content
from ...feed_config import HUGGINGFACE_PAPERS_URL
from ...models import Post


class HuggingFaceCrawler:
    platform = "huggingface"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 50)
        no_content = options.get("no_content", False)

        resp = requests.get(HUGGINGFACE_PAPERS_URL, timeout=15)
        resp.raise_for_status()
        papers = resp.json()

        items: List[dict] = []
        now = datetime.now(timezone.utc)

        for p in papers:
            paper_id = p.get("paper", {}).get("id", "")
            authors = ", ".join(
                a.get("name", "") for a in p.get("paper", {}).get("authors", [])[:5]
            )
            if len(p.get("paper", {}).get("authors", [])) > 5:
                authors += " et al."

            pub = p.get("publishedAt", "")
            try:
                entry_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                published = entry_dt.astimezone(timezone.utc).isoformat()
            except (ValueError, AttributeError):
                published = now.isoformat()

            items.append(
                {
                    "platform": "huggingface",
                    "title": p.get("title", ""),
                    "url": f"https://huggingface.co/papers/{paper_id}" if paper_id else "",
                    "author": authors,
                    "published": published,
                    "summary": re.sub(r"\s+", " ", p.get("summary", "")).strip()[:500],
                    "thumbnail": p.get("thumbnail", ""),
                    "num_comments": p.get("numComments", 0),
                }
            )

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
            thumbnail=item.get("thumbnail", ""),
            num_comments=item.get("num_comments", 0),
        )
