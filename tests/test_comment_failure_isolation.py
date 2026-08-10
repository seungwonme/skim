"""댓글 수집 실패가 게시글 저장을 막지 않는지 검증한다.

AGENTS.md의 계약이지만 실제로는 지켜지지 않았다. try가 HTTP 호출만 감싸고 있어서
상류 응답 구조가 바뀌면 파싱 예외가 크롤 루프까지 올라갔고, 그 회차의 게시글
50건이 통째로 저장 0건이 됐다.
"""

import unittest
from unittest.mock import MagicMock, patch

from skim_core.crawlers.api.linkedin import LinkedInAPICrawler
from skim_core.crawlers.api.reddit import RedditAPICrawler
from skim_core.crawlers.api.threads import ThreadsAPICrawler
from skim_core.crawlers.api.x import XAPICrawler
from skim_core.crawlers.feed import geeknews, hackernews, producthunt
from skim_core.models import Post


def _post(**overrides):
    data = {
        "platform": "reddit",
        "author": "tester",
        "content": "original body",
        "timestamp": "2026-08-10T00:00:00+09:00",
        "url": "https://example.com/a",
        "external_id": "7492224454731714560",
        "comments": 5,
    }
    data.update(overrides)
    return Post(**data)


class RedditIsolationTests(unittest.TestCase):
    def _crawler(self):
        with patch.object(RedditAPICrawler, "_load_session_cookies", return_value=None):
            return RedditAPICrawler()

    def test_parse_failure_keeps_the_body(self):
        crawler = self._crawler()
        posts = [_post(external_id="a"), _post(external_id="b")]
        with (
            patch.object(
                crawler, "fetch_comment_section", side_effect=ValueError("schema drift")
            ),
            patch("skim_core.crawlers.api.reddit.time.sleep"),
            patch("skim_core.crawlers.api.reddit.typer.echo"),
        ):
            crawler.attach_comments(posts)

        for post in posts:
            self.assertEqual(post.content, "original body")

    def test_zero_comment_posts_do_not_trip_the_circuit_breaker(self):
        # "댓글 0건"과 "HTTP 실패"가 둘 다 None이라, 조용한 서브레딧에서 0건 글이
        # 연속되면 남은 게시글 전체의 댓글 수집이 중단됐다.
        crawler = self._crawler()
        quiet = [_post(external_id=f"q{i}", comments=0) for i in range(5)]
        loud = _post(external_id="loud", comments=9)
        posts = quiet + [loud]

        calls = []

        def fake_fetch(url):
            calls.append(url)
            return "## Reddit Comments\n\n- **u/a**: hi"

        with (
            patch.object(crawler, "fetch_comment_section", side_effect=fake_fetch),
            patch("skim_core.crawlers.api.reddit.time.sleep"),
            patch("skim_core.crawlers.api.reddit.typer.echo"),
        ):
            crawler.attach_comments(posts)

        self.assertEqual(len(calls), 1, "0건 글은 조회하지 않는다")
        self.assertIn("Reddit Comments", loud.content_markdown or "")


class LinkedInIsolationTests(unittest.TestCase):
    def _crawler(self):
        with patch.object(
            LinkedInAPICrawler, "_load_session_cookies", return_value=None
        ):
            return LinkedInAPICrawler()

    def test_parse_failure_keeps_the_body(self):
        crawler = self._crawler()
        posts = [_post(platform="linkedin", external_id="1")]
        with (
            patch.object(
                crawler, "fetch_comment_section", side_effect=KeyError("included")
            ),
            patch("skim_core.crawlers.api.linkedin.time.sleep"),
            patch("skim_core.crawlers.api.linkedin.typer.echo"),
        ):
            crawler.attach_comments(posts)

        self.assertEqual(posts[0].content, "original body")

    def test_zero_comment_posts_are_skipped(self):
        crawler = self._crawler()
        posts = [_post(platform="linkedin", external_id="1", comments=0)]
        with (
            patch.object(crawler, "fetch_comment_section") as fetch,
            patch("skim_core.crawlers.api.linkedin.time.sleep"),
            patch("skim_core.crawlers.api.linkedin.typer.echo"),
        ):
            crawler.attach_comments(posts)

        fetch.assert_not_called()


class ThreadsIsolationTests(unittest.TestCase):
    def _crawler(self):
        with patch.object(ThreadsAPICrawler, "_setup_session", return_value=None):
            return ThreadsAPICrawler.__new__(ThreadsAPICrawler)

    def test_payload_parse_failure_keeps_the_body(self):
        crawler = self._crawler()
        posts = [_post(platform="threads", url="https://www.threads.net/@a/post/1")]
        response = MagicMock(status_code=200, text="<html></html>")
        response.raise_for_status.return_value = None
        with (
            patch("skim_core.crawlers.api.threads.requests.get", return_value=response),
            patch.object(
                crawler,
                "_extract_reply_threads",
                side_effect=TypeError("shape changed"),
            ),
            patch("skim_core.crawlers.api.threads.typer.echo"),
        ):
            crawler.attach_replies(posts)

        self.assertEqual(posts[0].content, "original body")


class XIsolationTests(unittest.TestCase):
    def test_reply_parse_failure_returns_none(self):
        # _reply_section은 _parse_tweets 루프 안에서 불린다. 여기서 예외가 새면
        # 그 회차의 트윗이 통째로 유실된다.
        crawler = XAPICrawler.__new__(XAPICrawler)
        with (
            patch.object(
                crawler, "_build_reply_section", side_effect=AttributeError("legacy")
            ),
            patch("skim_core.crawlers.api.x.typer.echo"),
        ):
            self.assertIsNone(crawler._reply_section("123", "456"))


class HackerNewsIsolationTests(unittest.TestCase):
    def test_comment_tree_parse_failure_returns_none(self):
        # Algolia가 children에 dict를 주면 walk()가 터진다. HTTP만 감싸던 시절엔
        # 이 예외가 crawl 루프까지 올라가 그 회차 HN이 통째로 저장 0건이 됐다.
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"children": {"not": "a list"}}
        with (
            patch.object(hackernews.requests, "get", return_value=response),
            patch("skim_core.crawlers.feed.hackernews.typer.echo"),
        ):
            self.assertIsNone(hackernews.fetch_hn_discussion("123"))

    def test_http_failure_still_returns_none(self):
        with (
            patch.object(
                hackernews.requests, "get", side_effect=OSError("connection reset")
            ),
            patch("skim_core.crawlers.feed.hackernews.typer.echo"),
        ):
            self.assertIsNone(hackernews.fetch_hn_discussion("123"))


class GeekNewsIsolationTests(unittest.TestCase):
    def test_markup_change_does_not_escape(self):
        # news.hada.io의 마크업이 바뀌어 셀렉터가 터져도 크롤은 계속돼야 한다.
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        response.text = "<html></html>"
        with (
            patch.object(geeknews.requests, "get", return_value=response),
            patch.object(
                geeknews, "_parse_comment_section", side_effect=AttributeError("dom")
            ),
            patch("skim_core.crawlers.feed.geeknews.typer.echo"),
        ):
            self.assertIsNone(geeknews.fetch_geeknews_metrics("42"))


class ProductHuntIsolationTests(unittest.TestCase):
    def test_dom_parse_failure_returns_none(self):
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        response.text = "<html></html>"
        with (
            patch.object(producthunt.requests, "get", return_value=response),
            patch.object(
                producthunt, "_parse_comment_section", side_effect=TypeError("dom")
            ),
            patch("skim_core.crawlers.feed.producthunt.typer.echo"),
        ):
            self.assertIsNone(
                producthunt.fetch_comment_section(
                    "https://www.producthunt.com/products/x"
                )
            )


if __name__ == "__main__":
    unittest.main()
