"""YouTube 조회수 회귀 테스트.

yt-dlp `--flat-playlist`는 요청을 늘리지 않고도 `view_count`를 준다.
그 값을 버리고 있어서 youtube 행은 views가 통째로 비어 있었다.
"""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from skim_core.crawlers.feed.youtube import _fetch_via_ytdlp, _item_to_post

SINCE = datetime(2026, 8, 1, tzinfo=timezone.utc)


class FakeCompletedProcess:  # pylint: disable=too-few-public-methods
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class YouTubeViewCountTests(unittest.TestCase):
    def _run(self, payload):
        stdout = "\n".join(json.dumps(item) for item in payload)
        with patch(
            "skim_core.crawlers.feed.youtube.subprocess.run",
            return_value=FakeCompletedProcess(stdout=stdout),
        ):
            return _fetch_via_ytdlp("LangChain", "LangChain", SINCE)

    def test_flat_playlist_view_count_reaches_post(self):
        """flat-playlist가 이미 주는 값이라 추가 요청 없이 조회수가 붙는다."""
        items = self._run(
            [
                {
                    "id": "abc123XYZ09",
                    "title": "T",
                    "timestamp": 1754611200,
                    "view_count": 3200,
                }
            ]
        )

        self.assertEqual(items[0]["views"], 3200)
        self.assertEqual(_item_to_post(items[0]).views, 3200)

    def test_missing_view_count_stays_none(self):
        """0과 미수집은 다른 값이다. 없으면 None으로 남긴다."""
        items = self._run([{"id": "abc123XYZ09", "title": "T", "timestamp": 1754611200}])

        self.assertIsNone(items[0]["views"])
        self.assertIsNone(_item_to_post(items[0]).views)


if __name__ == "__main__":
    unittest.main()
