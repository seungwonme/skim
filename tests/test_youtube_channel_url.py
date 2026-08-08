"""핸들(@name) 구독의 /videos URL 회귀 테스트.

핸들 canonical_id에 /channel/을 붙이면 YouTube가 404를 준다. 그 탓에
데스크톱에서 추가한 핸들 채널은 백필이 매번 0건이었고, CLI는 exit 0이라
앱에서는 조용히 빈 목록으로 보였다.
"""

import unittest
from unittest.mock import patch

from skim_core.feed_config import youtube_videos_url
from skim_core.youtube_history import backfill_channel_history, list_channel_videos


class FakeCompletedProcess:  # pylint: disable=too-few-public-methods
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class YouTubeChannelURLTests(unittest.TestCase):
    def test_handle_uses_bare_path_and_channel_id_uses_channel_path(self):
        self.assertEqual(
            youtube_videos_url("@aiDotEngineer"),
            "https://www.youtube.com/@aiDotEngineer/videos",
        )
        self.assertEqual(
            youtube_videos_url("UCC-lyoTfSrcJzA1ab3APAgw"),
            "https://www.youtube.com/channel/UCC-lyoTfSrcJzA1ab3APAgw/videos",
        )

    def test_enumerate_command_targets_handle_url(self):
        with patch(
            "skim_core.youtube_history.subprocess.run",
            return_value=FakeCompletedProcess(stdout=""),
        ) as run:
            list_channel_videos("@aiDotEngineer", "@aiDotEngineer", years=1)

        self.assertIn("https://www.youtube.com/@aiDotEngineer/videos", run.call_args.args[0])

    def test_enumerate_failure_raises_instead_of_returning_empty(self):
        with patch(
            "skim_core.youtube_history.subprocess.run",
            return_value=FakeCompletedProcess(returncode=1, stderr="HTTP Error 404"),
        ):
            with self.assertRaises(RuntimeError):
                list_channel_videos("@ghost", "@ghost", years=1)

    def test_backfill_raises_when_no_channel_matches(self):
        with patch("skim_core.youtube_history.get_connection") as get_connection:
            get_connection.return_value.execute.return_value.fetchall.return_value = []
            with self.assertRaises(RuntimeError):
                backfill_channel_history("없는채널", years=1)


if __name__ == "__main__":
    unittest.main()
