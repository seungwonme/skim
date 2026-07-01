"""Content enrichment quality gates."""

import asyncio
import unittest
from unittest.mock import patch

from skim_core.enrichment import _fetch_rendered_html, _is_content_usable, enrich_with_content


class EnrichmentQualityTests(unittest.TestCase):
    def test_enrich_with_content_rejects_geeknews_loading_placeholder(self):
        item = {
            "platform": "geeknews",
            "title": "Placeholder page",
            "url": "https://news.hada.io/topic?id=1",
        }

        with (
            patch(
                "skim_core.enrichment.resolve_geeknews_original_url",
                return_value="https://example.com/app",
            ),
            patch(
                "skim_core.enrichment.defuddle",
                return_value={"content_markdown": "Loading", "word_count": 1},
            ),
            patch(
                "skim_core.enrichment.extract_article_content",
                return_value=(None, "failed", "test fallback failed"),
            ),
        ):
            enrich_with_content([item])

        self.assertEqual(item["content_markdown"], "")
        self.assertEqual(item["word_count"], 0)

    def test_enrich_with_content_keeps_useful_geeknews_content(self):
        item = {
            "platform": "geeknews",
            "title": "Useful article",
            "url": "https://news.hada.io/topic?id=2",
        }
        body = "Useful article body with actual details"

        with (
            patch(
                "skim_core.enrichment.resolve_geeknews_original_url",
                return_value="https://example.com/article",
            ),
            patch(
                "skim_core.enrichment.defuddle",
                return_value={"content_markdown": body, "word_count": len(body.split())},
            ),
        ):
            enrich_with_content([item])

        self.assertEqual(item["content_markdown"], body)
        self.assertEqual(item["word_count"], 6)

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

    def test_enrich_with_content_falls_back_for_geeknews_placeholder(self):
        item = {
            "platform": "geeknews",
            "title": "Dynamic page",
            "url": "https://news.hada.io/topic?id=3",
        }
        body = "Recovered dynamic page content with enough detail"

        with (
            patch(
                "skim_core.enrichment.resolve_geeknews_original_url",
                return_value="https://example.com/dynamic",
            ),
            patch(
                "skim_core.enrichment.defuddle",
                return_value={"content_markdown": "Loading", "word_count": 1},
            ),
            patch(
                "skim_core.enrichment.extract_article_content",
                return_value=(
                    {"content_markdown": body, "word_count": len(body.split())},
                    "playwright+trafilatura",
                    None,
                ),
            ),
        ):
            enrich_with_content([item])

        self.assertEqual(item["content_markdown"], body)
        self.assertEqual(item["word_count"], 7)
        self.assertEqual(item["original_url"], "https://example.com/dynamic")


if __name__ == "__main__":
    unittest.main()
