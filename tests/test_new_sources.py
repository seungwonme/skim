"""소스 확장 회귀 테스트: arxiv 다중 카테고리, HN 다중 피드, lobsters."""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from skim_core.crawlers import REGISTRY
from skim_core.crawlers.feed import arxiv
from skim_core.crawlers.feed.arxiv import ArxivCrawler
from skim_core.crawlers.feed.hackernews import HackerNewsCrawler
from skim_core.crawlers.feed.lobsters import (
    LobstersCrawler,
    _short_id,
    comment_section_from,
)
from skim_core.feed_config import (
    ARXIV_CATEGORIES,
    HACKERNEWS_FEEDS,
    PERSONAL_BLOGS,
    arxiv_api_url,
)


class FeedConfigTests(unittest.TestCase):
    def test_arxiv_urls_use_https(self):
        # export.arxiv.org는 http를 리다이렉트하고, feedparser가 그 과정에서
        # 조용히 빈 피드를 돌려주는 경우가 있다.
        for category in ARXIV_CATEGORIES:
            self.assertTrue(arxiv_api_url(category).startswith("https://"))
            self.assertIn(f"cat:{category}", arxiv_api_url(category))

    def test_show_and_ask_feeds_have_no_score_gate(self):
        # Ask/Show HN은 링크가 아니라 본문이 알맹이라 30점 문턱에 걸리면 사라진다.
        for name in ("hackernews/show", "hackernews/ask"):
            self.assertNotIn("points=", HACKERNEWS_FEEDS[name])

    def test_undated_feed_is_not_registered(self):
        # 요즘IT는 피드에 발행일이 없어 fetch_feed가 전량 스킵한다. 등록하면 매번 0건.
        self.assertNotIn("yozm.wishket.com", " ".join(PERSONAL_BLOGS.values()))

    def test_new_platforms_are_in_the_registry(self):
        self.assertIn("lobsters", REGISTRY)


def _arxiv_entry(title, link, published):
    return {
        "title": title,
        "link": link,
        "published": published,
        "summary": "abstract",
        "authors": [{"name": "A"}],
    }


class ArxivMultiCategoryTests(unittest.TestCase):
    def test_cross_listed_paper_is_collected_once(self):
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        shared = _arxiv_entry("Shared", "https://arxiv.org/abs/1", stamp)
        unique = _arxiv_entry("Unique", "https://arxiv.org/abs/2", stamp)

        def fake_fetch(category, start=0):
            feed = MagicMock()
            # cs.AI와 cs.LG 양쪽에 같은 논문이 올라온 상황
            if start:
                feed.entries = []
                return feed
            feed.entries = (
                [shared] if category in ("cs.CL", "cs.CV") else [shared, unique]
            )
            return feed

        with (
            patch.object(arxiv, "_fetch_category", fake_fetch),
            patch.object(arxiv.time, "sleep"),
        ):
            posts = asyncio.run(
                ArxivCrawler().crawl(
                    since=now - timedelta(days=2), no_content=True, count=50
                )
            )

        urls = [p.url for p in posts]
        self.assertEqual(
            len(urls), len(set(urls)), "교차 등록 논문이 중복 저장되면 안 된다"
        )
        self.assertEqual(
            sorted(urls), ["https://arxiv.org/abs/1", "https://arxiv.org/abs/2"]
        )

    def test_category_is_recorded_on_the_post(self):
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        def fake_fetch(category, start=0):
            feed = MagicMock()
            if start:
                feed.entries = []
                return feed
            feed.entries = (
                [_arxiv_entry("Only CV", "https://arxiv.org/abs/9", stamp)]
                if category == "cs.CV"
                else []
            )
            return feed

        with (
            patch.object(arxiv, "_fetch_category", fake_fetch),
            patch.object(arxiv.time, "sleep"),
        ):
            posts = asyncio.run(
                ArxivCrawler().crawl(
                    since=now - timedelta(days=2), no_content=True, count=50
                )
            )

        self.assertEqual(getattr(posts[0], "arxiv_category"), "cs.CV")


class HackerNewsMultiFeedTests(unittest.TestCase):
    def test_same_story_from_two_feeds_is_kept_once(self):
        now = datetime.now(timezone.utc)

        def fake_fetch(url, name, since):  # pylint: disable=unused-argument
            common = {
                "url": "https://example.com/a",
                "title": "Show HN: thing",
                "published": now.isoformat(),
                "external_id": "x",
                "author": "a",
                "content_html": "",
                "summary": "",
            }
            # 점수가 오른 Show HN은 newest에도 올라온다.
            return [common] if "show" in url or "newest" in url else []

        # 0건인 피드는 Algolia 폴백을 부른다. 여기서 보려는 건 중복 제거뿐이라
        # 폴백까지 막지 않으면 테스트가 실제 네트워크를 탄다.
        with (
            patch("skim_core.crawlers.feed.hackernews.fetch_feed", fake_fetch),
            patch(
                "skim_core.crawlers.feed.hackernews.fetch_algolia_fallback",
                return_value=[],
            ),
        ):
            posts = asyncio.run(
                HackerNewsCrawler().crawl(
                    since=now - timedelta(days=1), no_content=True
                )
            )

        self.assertEqual(len(posts), 1)

    def test_every_configured_feed_is_queried(self):
        now = datetime.now(timezone.utc)
        seen = []

        def fake_fetch(url, name, since):  # pylint: disable=unused-argument
            seen.append(url)
            return []

        with (
            patch("skim_core.crawlers.feed.hackernews.fetch_feed", fake_fetch),
            patch(
                "skim_core.crawlers.feed.hackernews.fetch_algolia_fallback",
                return_value=[],
            ),
        ):
            asyncio.run(
                HackerNewsCrawler().crawl(
                    since=now - timedelta(days=1), no_content=True
                )
            )

        self.assertEqual(set(seen), set(HACKERNEWS_FEEDS.values()))


LOBSTERS_PAYLOAD = {
    "short_id": "abc123",
    "comment_count": 2,
    "description_plain": "self post body",
    "comments": [
        {
            "comment_plain": "top level",
            "commenting_user": "alice",
            "score": 4,
            "depth": 0,
            "created_at": "2026-08-09T08:00:00.000-05:00",
        },
        {
            "comment_plain": "a reply",
            "commenting_user": "bob",
            "score": 1,
            "depth": 1,
            "created_at": "2026-08-09T09:00:00.000-05:00",
        },
        {
            "comment_plain": "gone",
            "commenting_user": "x",
            "is_deleted": True,
            "depth": 0,
            "created_at": "2026-08-09T09:00:00.000-05:00",
        },
    ],
}


class LobstersTests(unittest.TestCase):
    def test_short_id_comes_from_the_feed_external_id(self):
        # fetch_feed는 guid를 external_id 키로 넘긴다. guid/id로 읽으면 항상 None이라
        # 댓글이 한 건도 안 붙는다.
        self.assertEqual(
            _short_id({"external_id": "https://lobste.rs/s/rsztog"}), "rsztog"
        )
        self.assertIsNone(_short_id({"external_id": "https://lobste.rs/t/zig"}))
        self.assertIsNone(_short_id({}))

    def test_renders_depth_and_skips_deleted(self):
        section = comment_section_from(LOBSTERS_PAYLOAD)

        self.assertIn("## Lobsters Comments", section)
        self.assertIn("- **alice** (4 points,", section)
        self.assertIn("  - **bob** (1 point,", section)
        self.assertNotIn("gone", section)

    def test_description_fills_the_body_when_extraction_failed(self):
        crawler = LobstersCrawler()
        post = MagicMock(content_markdown="", content="")
        items = [{"external_id": "https://lobste.rs/s/abc123"}]

        with (
            patch(
                "skim_core.crawlers.feed.lobsters.fetch_item",
                return_value=LOBSTERS_PAYLOAD,
            ),
            patch("skim_core.crawlers.feed.lobsters.time.sleep"),
            patch("skim_core.crawlers.feed.lobsters.typer.echo"),
        ):
            crawler.attach_details([post], items)

        self.assertIn("self post body", post.content_markdown)
        self.assertIn("## Lobsters Comments", post.content_markdown)

    def test_item_fetch_failure_keeps_the_post(self):
        crawler = LobstersCrawler()
        post = MagicMock(content_markdown="original", content="original")
        items = [{"external_id": "https://lobste.rs/s/abc123"}]

        with (
            patch(
                "skim_core.crawlers.feed.lobsters.fetch_item",
                side_effect=ValueError("bad json"),
            ),
            patch("skim_core.crawlers.feed.lobsters.time.sleep"),
            patch("skim_core.crawlers.feed.lobsters.typer.echo"),
        ):
            crawler.attach_details([post], items)

        self.assertEqual(post.content_markdown, "original")


if __name__ == "__main__":
    unittest.main()
