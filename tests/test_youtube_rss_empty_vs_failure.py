"""YouTube RSS "실패"와 "새 영상 없음" 구분 회귀 테스트 (#54).

fetch_feed()는 요청 실패, 파싱 실패, 창 안 항목 없음을 모두 []로 돌려줘서
YouTube 크롤러가 조용한 채널마다 yt-dlp를 다시 불렀고 로그의 "RSS 실패"도 부풀었다.
HTTP 계층만 가짜로 바꾸고 네트워크와 data/는 건드리지 않는다.
"""

import asyncio
import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import requests

from skim_core.crawlers.feed import youtube
from skim_core.feed_utils import fetch_feed, fetch_feed_or_none

SINCE = datetime(2026, 10, 6, tzinfo=timezone.utc)

ATOM_OLD_ENTRY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ch</title>
  <entry>
    <id>yt:video:abc123XYZ09</id>
    <title>old</title>
    <link href="https://www.youtube.com/watch?v=abc123XYZ09"/>
    <published>2026-09-01T00:00:00+00:00</published>
    <updated>2026-09-01T00:00:00+00:00</updated>
  </entry>
</feed>"""

ATOM_NEW_ENTRY = ATOM_OLD_ENTRY.replace(b"2026-09-01", b"2026-10-06")


def _response(content: bytes) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status.return_value = None
    return resp


class FetchFeedOrNoneTests(unittest.TestCase):
    def _call(self, session_get):
        with patch("skim_core.feed_utils._FEED_SESSION") as session:
            session.get = session_get
            return fetch_feed_or_none("https://feed", "src", SINCE, quiet=True)

    def test_request_failure_is_none(self):
        self.assertIsNone(self._call(MagicMock(side_effect=requests.ConnectionError())))

    def test_parse_failure_is_none(self):
        self.assertIsNone(self._call(MagicMock(return_value=_response(b"<<not xml"))))

    def test_healthy_feed_without_entries_in_window_is_empty_list(self):
        result = self._call(MagicMock(return_value=_response(ATOM_OLD_ENTRY)))
        self.assertEqual(result, [])

    def test_healthy_feed_with_new_entry_returns_it(self):
        result = self._call(MagicMock(return_value=_response(ATOM_NEW_ENTRY)))
        self.assertEqual(len(result), 1)

    def test_fetch_feed_keeps_returning_empty_list_for_failures(self):
        """기존 호출자 계약: 실패도 [] 그대로다."""
        with patch("skim_core.feed_utils._FEED_SESSION") as session:
            session.get = MagicMock(side_effect=requests.ConnectionError())
            self.assertEqual(fetch_feed("https://feed", "src", SINCE, quiet=True), [])


class YouTubeCrawlerFallbackTests(unittest.TestCase):
    def _crawl(self, channels, feed_result):
        """채널 목록과 RSS 결과를 고정해 크롤하고 (yt-dlp 호출 인자들, 출력)을 돌려준다."""
        ytdlp = MagicMock(return_value=[])
        buf = io.StringIO()
        with (
            patch.object(youtube, "normalize_tracked_channels"),
            patch.object(youtube, "tracked_youtube_channels", return_value=channels),
            patch.object(youtube, "fetch_feed_or_none", return_value=feed_result),
            patch.object(youtube, "_fetch_via_ytdlp", ytdlp),
            patch.object(youtube, "drop_known_items", side_effect=lambda _p, i: i),
            patch.object(youtube.time, "sleep"),
            redirect_stdout(buf),
        ):
            asyncio.run(youtube.YouTubeCrawler().crawl(since=SINCE, no_content=True))
        return ytdlp.call_args_list, buf.getvalue()

    def test_rss_failure_falls_back_to_ytdlp(self):
        calls, out = self._crawl([("A", "UCaaaaaaaaaaaaaaaaaaaaaa")], None)

        self.assertEqual(len(calls), 1)
        self.assertIn("RSS 실패 1, 핸들 구독 0", out)
        self.assertIn("새 영상 없음 0", out)

    def test_healthy_empty_feed_does_not_call_ytdlp(self):
        calls, out = self._crawl([("A", "UCaaaaaaaaaaaaaaaaaaaaaa")], [])

        self.assertEqual(calls, [])
        self.assertIn("RSS 실패 0, 핸들 구독 0", out)
        self.assertIn("새 영상 없음 1", out)

    def test_handle_only_subscription_uses_ytdlp(self):
        feed = MagicMock(return_value=[])
        ytdlp = MagicMock(return_value=[])
        buf = io.StringIO()
        with (
            patch.object(youtube, "normalize_tracked_channels"),
            patch.object(
                youtube, "tracked_youtube_channels", return_value=[("H", "@handle")]
            ),
            patch.object(youtube, "fetch_feed_or_none", feed),
            patch.object(youtube, "_fetch_via_ytdlp", ytdlp),
            patch.object(youtube, "drop_known_items", side_effect=lambda _p, i: i),
            patch.object(youtube.time, "sleep"),
            redirect_stdout(buf),
        ):
            asyncio.run(youtube.YouTubeCrawler().crawl(since=SINCE, no_content=True))

        feed.assert_not_called()
        self.assertEqual(ytdlp.call_count, 1)
        self.assertIn("RSS 실패 0, 핸들 구독 1", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
