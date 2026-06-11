"""Phase 0 크롤러별 timestamp ISO 8601 회귀 테스트."""

import re
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import requests

from skim_core.crawlers.feed.geeknews import GeekNewsCrawler
from skim_core.crawlers.feed.hackernews import HackerNewsCrawler
from skim_core.feed_utils import fetch_feed

ISO_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


def _is_utc_iso(value: str) -> bool:
    return bool(value and ISO_UTC_PATTERN.match(value))


class HackerNewsTimestampTests(unittest.TestCase):
    def test_hackernews_firebase_mode_stores_iso8601(self):
        crawler = HackerNewsCrawler()
        fake_top = MagicMock()
        fake_top.json.return_value = [101]
        fake_top.raise_for_status = MagicMock()
        fake_item = MagicMock()
        fake_item.json.return_value = {
            "type": "story",
            "by": "tester",
            "title": "hello",
            "time": 1700000000,
            "url": "https://example.com",
            "score": 5,
            "descendants": 2,
        }
        fake_item.raise_for_status = MagicMock()

        with patch(
            "skim_core.crawlers.feed.hackernews.requests.get",
            side_effect=[fake_top, fake_item],
        ):
            posts = crawler._fetch_top_stories(1)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].timestamp, "2023-11-14T22:13:20+00:00")

    def test_hackernews_firebase_mode_empty_time_is_empty(self):
        crawler = HackerNewsCrawler()
        fake_top = MagicMock()
        fake_top.json.return_value = [101]
        fake_top.raise_for_status = MagicMock()
        fake_item = MagicMock()
        fake_item.json.return_value = {
            "type": "story",
            "by": "tester",
            "title": "no time",
        }
        fake_item.raise_for_status = MagicMock()

        with patch(
            "skim_core.crawlers.feed.hackernews.requests.get",
            side_effect=[fake_top, fake_item],
        ):
            posts = crawler._fetch_top_stories(1)

        self.assertEqual(posts[0].timestamp, "")


class GeekNewsTimestampTests(unittest.TestCase):
    HOMEPAGE_HTML = """
    <html><body>
      <div class="topic_row">
        <div class="topictitle"><a href="/topic?id=1">Hello topic</a></div>
        <div class="topicdesc"><a>- 요약 내용</a></div>
        <div class="topicinfo">
          <span>10</span>
          <a href="/user?id=u1">someone</a>
          3시간 전 작성
          <a href="/topic?id=1&go=comments">5 댓글</a>
        </div>
      </div>
    </body></html>
    """

    def test_geeknews_homepage_mode_stores_iso8601(self):
        crawler = GeekNewsCrawler()
        response = MagicMock()
        response.text = self.HOMEPAGE_HTML
        response.raise_for_status = MagicMock()

        with patch(
            "skim_core.crawlers.feed.geeknews.requests.get",
            return_value=response,
        ):
            posts = crawler._scrape_homepage(1)

        self.assertEqual(len(posts), 1)
        self.assertTrue(
            _is_utc_iso(posts[0].timestamp),
            f"expected UTC ISO 8601, got {posts[0].timestamp!r}",
        )

    def test_geeknews_homepage_mode_no_match_returns_empty(self):
        crawler = GeekNewsCrawler()
        html = """
        <html><body>
          <div class="topic_row">
            <div class="topictitle"><a href="/topic?id=2">No timing</a></div>
            <div class="topicinfo">
              <span>3</span>
              <a href="/user?id=u2">noone</a>
            </div>
          </div>
        </body></html>
        """
        response = MagicMock()
        response.text = html
        response.raise_for_status = MagicMock()

        with patch(
            "skim_core.crawlers.feed.geeknews.requests.get",
            return_value=response,
        ):
            posts = crawler._scrape_homepage(1)

        self.assertEqual(posts[0].timestamp, "")


class FeedUtilsTimestampTests(unittest.TestCase):
    def test_fetch_feed_emits_utc_offset(self):
        # entry.published_parsed 는 time.struct_time 호환 (year,mon,day,h,m,s,...)
        entry = {
            "title": "t",
            "link": "https://example.com",
            "summary": "<p>summary</p>",
            "published_parsed": (2026, 4, 19, 5, 0, 0, 0, 0, 0),
            "author": "a",
        }

        feed = MagicMock()
        feed.bozo = False
        feed.entries = [entry]
        response = MagicMock()
        response.content = b"<feed />"
        response.raise_for_status = MagicMock()
        # feed entries는 dict 호환되도록
        with (
            patch("skim_core.feed_utils.requests.get", return_value=response),
            patch("skim_core.feed_utils.feedparser.parse", return_value=feed),
        ):
            since = datetime(2026, 4, 1, tzinfo=timezone.utc)
            results = fetch_feed("https://feed", "src", since)

        self.assertEqual(len(results), 1)
        self.assertTrue(
            _is_utc_iso(results[0]["published"]),
            f"expected UTC ISO 8601, got {results[0]['published']!r}",
        )

    def test_fetch_feed_omits_old_entries(self):
        entry = {
            "title": "t",
            "link": "https://example.com",
            "summary": "",
            "published_parsed": (2026, 3, 1, 0, 0, 0, 0, 0, 0),
            "author": "a",
        }
        feed = MagicMock()
        feed.bozo = False
        feed.entries = [entry]
        response = MagicMock()
        response.content = b"<feed />"
        response.raise_for_status = MagicMock()
        with (
            patch("skim_core.feed_utils.requests.get", return_value=response),
            patch("skim_core.feed_utils.feedparser.parse", return_value=feed),
        ):
            since = datetime(2026, 4, 1, tzinfo=timezone.utc)
            results = fetch_feed("https://feed", "src", since)
        self.assertEqual(results, [])

    def test_fetch_feed_request_failure_returns_empty(self):
        with patch("skim_core.feed_utils.requests.get", side_effect=requests.Timeout("timeout")):
            since = datetime(2026, 4, 1, tzinfo=timezone.utc)
            results = fetch_feed("https://feed", "src", since)

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
