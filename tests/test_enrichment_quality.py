"""Content enrichment quality gates.

GeekNews 본문 경로는 crawlers/feed/geeknews.py로 옮겼다 (#29). 그 테스트는
test_geeknews_body.py에 있다.
"""

import asyncio
import unittest
from unittest.mock import patch

from skim_core.enrichment import (
    _article_html,
    _fetch_rendered_html,
    _is_content_usable,
    enrich_with_content,
    extract_article_content,
)


class EnrichmentQualityTests(unittest.TestCase):
    def test_short_product_description_is_kept_without_browser_retry(self):
        body = " ".join(f"productword{i}" for i in range(47))
        item = {
            "platform": "producthunt",
            "title": "Product",
            "url": "https://example.com/product",
        }
        with (
            patch(
                "skim_core.enrichment._http_fetch_html",
                return_value=f"<main><p>{body}</p></main>",
            ),
            patch("skim_core.enrichment._fetch_rendered_html") as rendered,
        ):
            enrich_with_content([item])
        self.assertEqual(item["content_markdown"], body)
        self.assertEqual(item["enrichment_method"], "trafilatura")
        self.assertNotIn("enrichment_error", item)
        rendered.assert_not_called()

    def test_long_menu_does_not_make_a_javascript_shell_usable(self):
        shell = (
            "<nav>"
            + "Menu link " * 100
            + "</nav><article><h1>App</h1><noscript>App needs JavaScript to run.</noscript></article>"
        )
        body = "Rendered usage instructions explain the actual app. " * 12
        rendered = f"<nav>Unrelated global menu</nav><article><p>{body}</p></article>"
        with (
            patch("skim_core.enrichment._http_fetch_html", return_value=shell),
            patch(
                "skim_core.enrichment._fetch_rendered_html", return_value=rendered
            ) as browser,
            patch("skim_core.enrichment.defuddle") as fallback,
        ):
            data, method, error = extract_article_content(
                "https://example.com/app", "App"
            )
        self.assertEqual(method, "playwright+trafilatura")
        self.assertIn("Rendered usage instructions", data["content_markdown"])
        self.assertNotIn("Menu link", data["content_markdown"])
        self.assertNotIn("Unrelated", data["content_markdown"])
        self.assertIsNone(error)
        browser.assert_called_once()
        fallback.assert_not_called()

    def test_failed_render_does_not_fall_back_to_the_same_app_shell(self):
        shell = '<div role="main"><noscript>Enable JavaScript to use this app.</noscript></div>'
        with (
            patch("skim_core.enrichment._http_fetch_html", return_value=shell),
            patch("skim_core.enrichment._fetch_rendered_html", return_value=None),
            patch("skim_core.enrichment.defuddle") as fallback,
        ):
            data, _, error = extract_article_content("https://example.com/app", "App")
        self.assertIsNone(data)
        self.assertIn("javascript shell", error)
        fallback.assert_not_called()

    def test_javascript_discussion_in_an_article_is_not_an_app_shell(self):
        html = "<article><p>A tutorial says enable JavaScript to run your example.</p><p>Actual article content.</p></article>"
        prepared, needs_render = _article_html(html)
        self.assertFalse(needs_render)
        self.assertIn("Actual article content.", prepared)

    def test_tagline_fallback_remains_explicitly_partial(self):
        item = {
            "platform": "producthunt",
            "title": "Product",
            "url": "https://example.com/product",
            "tagline": "A short product introduction",
        }
        with patch(
            "skim_core.enrichment._extract_article_or_feed_content",
            return_value=(None, "failed", "http fetch failed"),
        ):
            enrich_with_content([item])
        self.assertEqual(item["content_markdown"], item["tagline"])
        self.assertEqual(item["content_status"], "partial")
        self.assertEqual(item["enrichment_method"], "failed")

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
