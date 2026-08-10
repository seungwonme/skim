"""링크 게시물의 원문이 본문에 들어오는지 검증한다.

reddit 링크 게시물은 제목 한 줄만 남고 원문 URL조차 버려졌다(562행 실측).
hackernews/lobsters/everyto는 defuddle 단발만 타서 3단 사다리를 못 받았다.
"""

import unittest
from unittest.mock import patch

from skim_core import enrichment
from skim_core.crawlers.api.reddit import RedditAPICrawler


def _listing_item(**overrides):
    data = {
        "id": "abc123",
        "title": "Rails is done",
        "selftext": "",
        "permalink": "/r/programming/comments/abc123/rails/",
        "created_utc": 1760000000,
        "is_self": False,
        "url_overridden_by_dest": "https://example.com/rails-is-done",
        "author": "tester",
        "score": 42,
        "num_comments": 7,
        "subreddit": "programming",
        "subreddit_name_prefixed": "r/programming",
    }
    data.update(overrides)
    return data


class RedditExternalLinkTests(unittest.TestCase):
    def _crawler(self):
        with patch.object(RedditAPICrawler, "_load_session_cookies", return_value=None):
            return RedditAPICrawler()

    def test_article_link_is_preserved(self):
        post = self._crawler().parse_post(_listing_item())
        self.assertEqual(
            getattr(post, "original_url", None), "https://example.com/rails-is-done"
        )

    def test_self_post_has_no_external_link(self):
        item = _listing_item(is_self=True, selftext="my own words")
        self.assertIsNone(self._crawler()._external_link(item))

    def test_image_and_reddit_hosts_are_not_articles(self):
        crawler = self._crawler()
        for dest in (
            "https://i.redd.it/abc.jpg",
            "https://v.redd.it/xyz",
            "https://www.reddit.com/r/x/comments/y/",
            "https://imgur.com/a/abc",
            "https://example.com/chart.png",
        ):
            self.assertIsNone(
                crawler._external_link(_listing_item(url_overridden_by_dest=dest)),
                f"{dest}는 원문 기사가 아니다",
            )

    def test_extracted_article_is_appended(self):
        crawler = self._crawler()
        post = crawler.parse_post(_listing_item())

        def fake_enrich(items):
            for item in items:
                item["content_markdown"] = "full article body"
            return items

        with patch(
            "skim_core.crawlers.api.reddit.enrich_with_content", side_effect=fake_enrich
        ):
            crawler.attach_articles([post])

        self.assertIn("## Original Article", post.content_markdown)
        self.assertIn("full article body", post.content_markdown)

    def test_enrichment_failure_keeps_the_post(self):
        crawler = self._crawler()
        post = crawler.parse_post(_listing_item())
        before = post.content_markdown

        with (
            patch(
                "skim_core.crawlers.api.reddit.enrich_with_content",
                side_effect=RuntimeError("boom"),
            ),
            patch("skim_core.crawlers.api.reddit.typer.echo"),
        ):
            crawler.attach_articles([post])

        self.assertEqual(post.content_markdown, before)


class RedditRssFallbackTests(unittest.TestCase):
    """JSON listing이 403이면 Atom 폴백을 타는데, 그 경로가 원문을 버렸다.

    strip_html이 <a href="원문">[link]</a>를 텍스트로 만들면서 href가 사라지고
    본문에는 "submitted by /u/x [link] [comments]" 껍데기만 남았다.
    """

    def _crawler(self):
        with patch.object(RedditAPICrawler, "_load_session_cookies", return_value=None):
            return RedditAPICrawler()

    def _entry(self, summary, title="Assembly Hall of Shame"):
        return {
            "id": "t3_1vjketg",
            "title": title,
            "summary": summary,
            "link": "https://www.reddit.com/r/programming/comments/1vjketg/x/",
            "author": "/u/f311a",
            "published_parsed": (2026, 8, 10, 0, 0, 0, 0, 0, 0),
        }

    def test_link_href_is_preserved(self):
        summary = (
            '<!-- SC_OFF --><div class="md"></div><!-- SC_ON --> submitted by '
            '<a href="https://www.reddit.com/user/f311a"> /u/f311a </a> '
            '<a href="https://example.com/assembly">[link]</a> '
            '<a href="https://www.reddit.com/r/programming/comments/1vjketg/x/">'
            "[comments]</a>"
        )
        post = self._crawler().parse_rss_entry(self._entry(summary), "programming")
        self.assertEqual(
            getattr(post, "original_url", None), "https://example.com/assembly"
        )

    def test_shell_body_becomes_the_title(self):
        summary = (
            "submitted by /u/f311a "
            '<a href="https://example.com/assembly">[link]</a> '
            '<a href="https://www.reddit.com/r/programming/comments/1vjketg/x/">'
            "[comments]</a>"
        )
        post = self._crawler().parse_rss_entry(self._entry(summary), "programming")
        self.assertEqual(post.content, "Assembly Hall of Shame")
        self.assertNotIn("[link]", post.content)

    def test_self_post_body_is_kept(self):
        summary = '<!-- SC_OFF --><div class="md"><p>my own words here</p></div>'
        post = self._crawler().parse_rss_entry(self._entry(summary), "programming")
        self.assertIn("my own words here", post.content)
        self.assertIsNone(getattr(post, "original_url", None))


class AggregatorLadderTests(unittest.TestCase):
    def test_aggregators_use_the_three_step_ladder(self):
        # defuddle 단발은 파이프라인에서 가장 약한 경로다.
        for platform in ("hackernews", "lobsters", "everyto", "reddit"):
            item = {"platform": platform, "title": "t"}
            with patch.object(
                enrichment,
                "_enrich_article_item",
                return_value={"content_markdown": "x"},
            ) as ladder:
                enrichment._extract_for_platform(item, "https://example.com/a")
            self.assertTrue(ladder.called, f"{platform}이 사다리를 안 탄다")
            self.assertEqual(
                ladder.call_args.kwargs.get("min_words"),
                enrichment._AGGREGATOR_MIN_WORDS,
                "링크 애그리게이터는 짧은 릴리스 노트도 정당한 본문이다",
            )

    def test_unknown_platform_still_uses_defuddle(self):
        item = {"platform": "somethingelse", "title": "t"}
        with patch.object(enrichment, "defuddle", return_value=None) as duf:
            enrichment._extract_for_platform(item, "https://example.com/a")
        self.assertTrue(duf.called)

    def test_pdf_link_falls_back_to_pdf_extraction(self):
        with patch.object(
            enrichment,
            "extract_pdf_text",
            return_value={"content_markdown": "pdf body"},
        ) as pdf:
            data = enrichment._pdf_fallback("https://example.com/paper.pdf")
        self.assertTrue(pdf.called)
        self.assertEqual(data["content_markdown"], "pdf body")

    def test_non_pdf_link_is_not_sent_to_pdf_extraction(self):
        with patch.object(enrichment, "extract_pdf_text") as pdf:
            self.assertIsNone(enrichment._pdf_fallback("https://example.com/a"))
        pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
