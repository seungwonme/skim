"""데일리 youtube 크롤이 tracked_sources를 구독 정본으로 쓰는지 회귀 테스트.

크롤러가 feed_config 하드코딩 목록만 보면 데스크톱에서 추가한 채널은
데일리 수집에서 영영 빠진다.
"""

import sqlite3
import unittest
from unittest.mock import patch

from skim_core.crawlers.feed.youtube import tracked_youtube_channels
from skim_core.feed_config import YOUTUBE_CHANNELS


def fake_connection(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (display_name TEXT, canonical_id TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", rows)
    conn.commit()

    class Wrapper:  # pylint: disable=too-few-public-methods
        """execute를 고정 쿼리로 갈아끼우는 최소 래퍼."""

        def execute(self, _sql):
            return conn.execute("SELECT display_name, canonical_id FROM t")

        def close(self):
            conn.close()

    return Wrapper()


class TrackedYouTubeChannelsTests(unittest.TestCase):
    def test_uses_tracked_sources_including_handles(self):
        rows = [("@aiDotEngineer", "@aiDotEngineer"), ("LangChain", "UC-langchain")]
        with patch(
            "skim_core.crawlers.feed.youtube.get_connection",
            return_value=fake_connection(rows),
        ):
            channels = tracked_youtube_channels()

        self.assertEqual(channels, rows)
        self.assertIn(("@aiDotEngineer", "@aiDotEngineer"), channels)

    def test_falls_back_to_feed_config_when_table_is_empty(self):
        with patch(
            "skim_core.crawlers.feed.youtube.get_connection",
            return_value=fake_connection([]),
        ):
            self.assertEqual(tracked_youtube_channels(), list(YOUTUBE_CHANNELS.items()))

    def test_falls_back_to_feed_config_when_db_is_unreadable(self):
        with patch(
            "skim_core.crawlers.feed.youtube.get_connection",
            side_effect=sqlite3.Error("no such table"),
        ):
            self.assertEqual(tracked_youtube_channels(), list(YOUTUBE_CHANNELS.items()))


if __name__ == "__main__":
    unittest.main()
