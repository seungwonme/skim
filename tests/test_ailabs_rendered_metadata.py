"""ailabs 발행일 폴백의 마지막 단계(Playwright 렌더링)를 확인한다.

HTTP로 받은 메타와 사이트맵에 발행일이 없을 때만 렌더링한다. 렌더러는
enrichment의 것을 쓴다(사본을 두던 때는 두 곳을 따로 고쳐야 했다).
"""

import unittest
from unittest.mock import patch

from skim_core.crawlers.feed import ailabs

RENDERED = (
    "<html><head>"
    '<meta property="article:published_time" content="2026-09-01T10:00:00Z">'
    '<meta property="og:title" content="Rendered title">'
    "</head></html>"
)


class RenderedMetadataFallbackTests(unittest.TestCase):
    def setUp(self):
        ailabs._fetch_article_metadata.cache_clear()
        self.addCleanup(ailabs._fetch_article_metadata.cache_clear)

    def test_renders_when_http_and_sitemap_have_no_date(self):
        with (
            patch.object(ailabs, "_fetch_html", return_value=None),
            patch.object(ailabs, "_sitemap_published", return_value=None),
            patch.object(
                ailabs, "_fetch_rendered_html", return_value=RENDERED
            ) as render,
        ):
            meta = ailabs._fetch_article_metadata("https://example.com/a")

        render.assert_called_once_with("https://example.com/a")
        self.assertEqual(meta["published"], "2026-09-01T10:00:00Z")
        self.assertEqual(meta["title"], "Rendered title")

    def test_does_not_render_when_sitemap_has_date(self):
        with (
            patch.object(ailabs, "_fetch_html", return_value=None),
            patch.object(ailabs, "_sitemap_published", return_value="2026-09-02"),
            patch.object(ailabs, "_fetch_rendered_html") as render,
        ):
            meta = ailabs._fetch_article_metadata("https://example.com/b")

        render.assert_not_called()
        self.assertEqual(meta["published"], "2026-09-02")


if __name__ == "__main__":
    unittest.main()
