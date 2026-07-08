"""Content enrichment quality gates."""

import asyncio
import unittest
from unittest.mock import patch

from skim_core.enrichment import (
    _fetch_rendered_html,
    _geeknews_topic_body_from_html,
    _is_content_usable,
    enrich_with_content,
)


class EnrichmentQualityTests(unittest.TestCase):
    def test_enrich_with_content_rejects_geeknews_loading_placeholder(self):
        item = {
            "platform": "geeknews",
            "title": "Placeholder page",
            "url": "https://news.hada.io/topic?id=1",
        }

        with (
            patch("skim_core.enrichment.fetch_geeknews_topic_body", return_value=None),
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
            patch("skim_core.enrichment.fetch_geeknews_topic_body", return_value=None),
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
            patch("skim_core.enrichment.fetch_geeknews_topic_body", return_value=None),
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
                return_value={"content_markdown": body, "word_count": len(body.split())},
            ),
        ):
            enrich_with_content([item])

        self.assertEqual(item["content_markdown"], body)
        self.assertEqual(item["word_count"], 210)
        self.assertEqual(item["enrichment_method"], "feed-content")
        self.assertNotIn("enrichment_error", item)

    def test_geeknews_topic_body_from_html_extracts_bullets(self):
        html = (
            '<div class="topic_contents">'
            "<ul><li>첫 <strong>번째</strong> 요점</li><li>두 번째 요점</li></ul>"
            "<p>마무리 문단</p></div>"
        )

        body = _geeknews_topic_body_from_html(html)

        self.assertEqual(body, "- 첫 번째 요점\n- 두 번째 요점\n마무리 문단")

    def test_geeknews_topic_body_from_html_returns_none_without_container(self):
        self.assertIsNone(_geeknews_topic_body_from_html("<div>no topic here</div>"))

    def test_enrich_with_content_keeps_topic_body_when_original_is_junk(self):
        item = {
            "platform": "geeknews",
            "title": "Directory landing",
            "url": "https://news.hada.io/topic?id=4",
        }
        topic_body = "- 노동자 소유 기업 디렉터리 요약\n- 22,000개 이상 제품"

        with (
            patch("skim_core.enrichment.fetch_geeknews_topic_body", return_value=topic_body),
            patch(
                "skim_core.enrichment.resolve_geeknews_original_url",
                return_value="https://example.com/landing",
            ),
            patch(
                "skim_core.enrichment.defuddle",
                return_value={"content_markdown": "Loading", "word_count": 1},
            ),
            patch(
                "skim_core.enrichment.extract_article_content",
                return_value=(None, "failed", "landing junk"),
            ),
        ):
            enrich_with_content([item])

        self.assertEqual(item["content_markdown"], topic_body)

    def test_enrich_with_content_appends_original_after_topic_body(self):
        item = {
            "platform": "geeknews",
            "title": "Useful article",
            "url": "https://news.hada.io/topic?id=5",
        }
        topic_body = "- 한국어 요약"
        original = "Original article body with actual details"

        with (
            patch("skim_core.enrichment.fetch_geeknews_topic_body", return_value=topic_body),
            patch(
                "skim_core.enrichment.resolve_geeknews_original_url",
                return_value="https://example.com/article",
            ),
            patch(
                "skim_core.enrichment.defuddle",
                return_value={"content_markdown": original, "word_count": len(original.split())},
            ),
        ):
            enrich_with_content([item])

        self.assertEqual(
            item["content_markdown"],
            f"{topic_body}\n\n---\n\n## Original Article\n\n{original}",
        )


if __name__ == "__main__":
    unittest.main()
