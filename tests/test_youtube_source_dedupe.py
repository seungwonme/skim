"""유튜브 구독 중복 방지 회귀 테스트.

핸들(@name)과 채널 ID(UC...)는 같은 채널이라도 문자열이 달라
UNIQUE(platform, canonical_id)가 막지 못한다. 실제로 Andrej Karpathy와
Lex Fridman이 양쪽으로 등록돼 매일 두 번씩 크롤되고 있었다.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skim_core.db import get_connection, init_db
from skim_core.youtube_history import normalize_tracked_channels

KARPATHY_ID = "UCXUPKJO5MZQN11PqgIvyuvQ"


class NormalizeTrackedChannelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "skim.db"
        init_db(self.db)
        self._patcher = patch(
            "skim_core.youtube_history.get_connection",
            side_effect=lambda *a, **kw: get_connection(self.db),
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self.tmp.cleanup()

    def _add_source(self, display_name: str, canonical_id: str) -> None:
        conn = get_connection(self.db)
        conn.execute(
            "INSERT INTO tracked_sources (platform, source_type, display_name, canonical_id) "
            "VALUES ('youtube', 'channel', ?, ?)",
            (display_name, canonical_id),
        )
        conn.commit()
        conn.close()

    def _add_post(self, source: str, external_id: str) -> None:
        conn = get_connection(self.db)
        conn.execute(
            "INSERT INTO posts (platform, source, external_id, author, title, content) "
            "VALUES ('youtube', ?, ?, 'a', 't', '')",
            (source, external_id),
        )
        conn.commit()
        conn.close()

    def _sources(self):
        conn = get_connection(self.db)
        rows = conn.execute(
            "SELECT display_name, canonical_id FROM tracked_sources ORDER BY display_name"
        ).fetchall()
        conn.close()
        return [(r["display_name"], r["canonical_id"]) for r in rows]

    def test_duplicate_handle_is_merged_into_the_channel_id_subscription(self):
        self._add_source("Andrej Karpathy", KARPATHY_ID)
        self._add_source("@AndrejKarpathy", "@AndrejKarpathy")
        self._add_post("youtube/@AndrejKarpathy", "vid-1")

        with patch("skim_core.youtube_history.resolve_channel_id", return_value=KARPATHY_ID):
            stats = normalize_tracked_channels()

        self.assertEqual(stats["merged"], 1)
        self.assertEqual(self._sources(), [("Andrej Karpathy", KARPATHY_ID)])

        # 핸들 이름으로 저장돼 있던 글은 남는 구독 쪽으로 옮겨야 사라지지 않는다.
        conn = get_connection(self.db)
        source = conn.execute("SELECT source FROM posts WHERE external_id='vid-1'").fetchone()[0]
        conn.close()
        self.assertEqual(source, "youtube/Andrej Karpathy")

    def test_unique_handle_is_promoted_to_channel_id(self):
        self._add_source("@aiDotEngineer", "@aiDotEngineer")

        with patch("skim_core.youtube_history.resolve_channel_id", return_value="UCnewchannel"):
            stats = normalize_tracked_channels()

        self.assertEqual(stats["promoted"], 1)
        self.assertEqual(self._sources(), [("@aiDotEngineer", "UCnewchannel")])

    def test_promotion_makes_the_unique_constraint_reject_a_later_duplicate(self):
        self._add_source("@aiDotEngineer", "@aiDotEngineer")
        with patch("skim_core.youtube_history.resolve_channel_id", return_value="UCnewchannel"):
            normalize_tracked_channels()

        # 승격 후에는 같은 채널을 다시 등록하려 해도 DB가 막는다.
        with self.assertRaises(sqlite3.IntegrityError):
            self._add_source("AI Engineer", "UCnewchannel")

    def test_is_idempotent_and_skips_yt_dlp_when_no_handles_remain(self):
        self._add_source("Andrej Karpathy", KARPATHY_ID)

        with patch("skim_core.youtube_history.resolve_channel_id") as resolve:
            stats = normalize_tracked_channels()

        resolve.assert_not_called()
        self.assertEqual(stats, {"promoted": 0, "merged": 0, "unresolved": 0})

    def test_unresolvable_handle_is_left_alone(self):
        self._add_source("@gone", "@gone")

        with patch("skim_core.youtube_history.resolve_channel_id", return_value=None):
            stats = normalize_tracked_channels()

        self.assertEqual(stats["unresolved"], 1)
        self.assertEqual(self._sources(), [("@gone", "@gone")])


if __name__ == "__main__":
    unittest.main()
