"""수집 행의 메타데이터 공백 회귀 테스트.

- yt-dlp fallback 경로가 published를 빈 문자열로 남기던 문제
- author를 싣지 않는 피드가 빈 작성자로 저장되던 문제
"""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from skim_core import feed_utils
from skim_core.crawlers.feed.youtube import _fetch_via_ytdlp


class FakeCompletedProcess:  # pylint: disable=too-few-public-methods
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class YouTubeFallbackTimestampTests(unittest.TestCase):
    def _run(self, payload):
        stdout = "\n".join(json.dumps(item) for item in payload)
        with patch(
            "skim_core.crawlers.feed.youtube.subprocess.run",
            return_value=FakeCompletedProcess(stdout=stdout),
        ) as run:
            items = _fetch_via_ytdlp(
                "@aiDotEngineer",
                "@aiDotEngineer",
                datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
        return items, run

    def test_requests_approximate_date_so_flat_playlist_carries_timestamps(self):
        _, run = self._run([{"id": "abc123XYZ09", "title": "T", "timestamp": 1754611200}])
        argv = run.call_args.args[0]
        self.assertIn("youtubetab:approximate_date", argv)

    def test_published_is_filled_from_timestamp(self):
        items, _ = self._run([{"id": "abc123XYZ09", "title": "T", "timestamp": 1754611200}])
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["published"])
        self.assertEqual(
            items[0]["published"],
            datetime.fromtimestamp(1754611200, tz=timezone.utc).isoformat(),
        )

    def test_missing_timestamp_still_yields_the_row(self):
        # 시각을 못 얻어도 영상 자체는 버리지 않는다 (읽기 쪽이 crawled_at으로 폴백)
        items, _ = self._run([{"id": "abc123XYZ09", "title": "T"}])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published"], "")


class FakeResponse:  # pylint: disable=too-few-public-methods
    content = b"<rss/>"

    def raise_for_status(self):
        return None


class FeedAuthorFallbackTests(unittest.TestCase):
    def _fetch(self, entry, source_name):
        parsed = type("Parsed", (), {"bozo": False, "entries": [entry]})()
        with (
            patch.object(feed_utils.requests, "get", return_value=FakeResponse()),
            patch.object(feed_utils.feedparser, "parse", return_value=parsed),
        ):
            return feed_utils.fetch_feed(
                "https://example.com/rss",
                source_name,
                datetime(2026, 8, 1, tzinfo=timezone.utc),
                quiet=True,
            )

    def test_author_falls_back_to_source_display_name(self):
        # OpenAI News RSS는 author를 싣지 않아 422건이 빈 작성자로 저장돼 있었다.
        entry = {
            "title": "Introducing something",
            "link": "https://openai.com/index/x",
            "summary": "body",
            "id": "x",
            "published_parsed": (2026, 8, 8, 1, 0, 0, 0, 0, 0),
        }
        results = self._fetch(entry, "ailabs/OpenAI News")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["author"], "OpenAI News")

    def test_real_author_is_not_overwritten(self):
        entry = {
            "title": "Post",
            "link": "https://example.com/x",
            "summary": "body",
            "id": "x",
            "author": "Jane Doe",
            "published_parsed": (2026, 8, 8, 1, 0, 0, 0, 0, 0),
        }
        results = self._fetch(entry, "ailabs/OpenAI News")

        self.assertEqual(results[0]["author"], "Jane Doe")


if __name__ == "__main__":
    unittest.main()
