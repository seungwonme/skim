"""
@file hackernews.py
@description Hacker News 크롤러 (Firebase API + RSS)
"""

from typing import Any, List

import requests
import typer

from feed_config import HACKERNEWS_RSS

from ...enrichment import enrich_with_content
from ...feed_utils import fetch_feed
from ...models import Post

HN_API_BASE = "https://hacker-news.firebaseio.com/v0"


class HackerNewsCrawler:
    platform = "hackernews"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 30)
        since = options.get("since")
        no_content = options.get("no_content", False)

        if since:
            # RSS mode
            items = fetch_feed(HACKERNEWS_RSS, "hackernews", since)
            items.sort(key=lambda x: x.get("published", ""), reverse=True)
            if not no_content:
                enrich_with_content(items)
            return [self._item_to_post(item) for item in items]
        else:
            # Firebase API mode (top stories)
            return self._fetch_top_stories(count)

    def _fetch_top_stories(self, count: int) -> List[Post]:
        """Firebase API를 사용하여 Hacker News Top Stories를 가져옵니다."""
        typer.echo(f"Hacker News에서 상위 {count}개 스토리를 가져옵니다...")

        resp = requests.get(f"{HN_API_BASE}/topstories.json", timeout=10)
        resp.raise_for_status()
        story_ids = resp.json()[:count]

        posts: List[Post] = []
        for i, story_id in enumerate(story_ids):
            try:
                item_resp = requests.get(f"{HN_API_BASE}/item/{story_id}.json", timeout=10)
                item_resp.raise_for_status()
                item = item_resp.json()

                if not item or item.get("type") != "story":
                    continue

                post = Post(
                    platform="hackernews",
                    author=item.get("by", "unknown"),
                    content=item.get("title", ""),
                    timestamp=str(item.get("time", "")),
                    url=item.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                    likes=item.get("score", 0),
                    comments=item.get("descendants", 0),
                )
                posts.append(post)
                typer.echo(f"   [{i + 1}/{count}] {post.content[:60]}")
            except Exception as e:
                typer.echo(f"   [{i + 1}/{count}] 스킵 (에러: {e})")

        return posts

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
