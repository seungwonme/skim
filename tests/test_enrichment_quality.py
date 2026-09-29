"""Content enrichment quality gates.

GeekNews 본문 경로는 crawlers/feed/geeknews.py로 옮겼다 (#29). 그 테스트는
test_geeknews_body.py에 있다.
"""

import asyncio
import unittest
from unittest.mock import patch

from skim_core.enrichment import (
    _fetch_rendered_html,
    _is_content_usable,
    enrich_with_content,
)


class EnrichmentQualityTests(unittest.TestCase):
    def test_is_content_usable_accepts_short_real_articles(self):
        body = " ".join(f"word{i}" for i in range(70))

        self.assertTrue(
            _is_content_usable(
                {"content_markdown": body, "word_count": 70},
                "Short article",
            )
        )

    def test_fetch_rendered_html_runs_sync_playwright_from_async_loop(self):
        async def _run():
            return _fetch_rendered_html("https://example.com")

        with patch(
            "skim_core.enrichment._fetch_rendered_html_sync",
            return_value="<html>ok</html>",
        ) as fetch:
            html = asyncio.run(_run())

        self.assertEqual(html, "<html>ok</html>")
        fetch.assert_called_once_with("https://example.com", 30000)

    def test_is_content_usable_rejects_stage_placeholder_prefix(self):
        self.assertFalse(
            _is_content_usable(
                {
                    "content_markdown": "STAGE 1 App Store Google Play App Store Google Play RETRY",
                    "word_count": 9,
                },
                "Playable app",
                min_words=3,
            )
        )

    def test_enrich_with_content_uses_feed_html_when_article_fetch_fails(self):
        body = "full feed body " * 70
        item = {
            "platform": "blogs",
            "title": "Feed-backed article",
            "url": "https://discuss.example/t/feed-backed/1",
            "content_html": f"<article><p>{body}</p></article>",
        }

        with (
            patch(
                "skim_core.enrichment.extract_article_content",
                return_value=(None, "failed", "http fetch failed"),
            ),
            patch(
                "skim_core.enrichment._extract_feed_content_html",
                return_value={
                    "content_markdown": body,
                    "word_count": len(body.split()),
                },
            ),
        ):
            enrich_with_content([item])

        self.assertEqual(item["content_markdown"], body)
        self.assertEqual(item["word_count"], 210)
        self.assertEqual(item["enrichment_method"], "feed-content")
        self.assertNotIn("enrichment_error", item)


if __name__ == "__main__":
    unittest.main()
