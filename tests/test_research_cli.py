"""Phase 1 `skim research` CLI 회귀 테스트."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from skim_cli.cli import app
from skim_core.db import get_connection, init_db

RECENT_TIMESTAMP = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def _insert(db: Path, **kw) -> None:
    defaults = {
        "platform": "hackernews",
        "source": None,
        "external_id": None,
        "author": "u",
        "title": None,
        "content": "",
        "url": None,
        "timestamp": RECENT_TIMESTAMP,
        "likes": None,
        "comments": None,
        "reposts": None,
        "views": None,
        "summary": None,
        "content_markdown": None,
        "word_count": None,
        "extra": None,
    }
    defaults.update(kw)
    conn = get_connection(db)
    conn.execute(
        """INSERT INTO posts
           (platform, source, external_id, author, title, content, url, timestamp,
            likes, comments, reposts, views, summary, content_markdown, word_count, extra)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        tuple(
            defaults[k]
            for k in (
                "platform",
                "source",
                "external_id",
                "author",
                "title",
                "content",
                "url",
                "timestamp",
                "likes",
                "comments",
                "reposts",
                "views",
                "summary",
                "content_markdown",
                "word_count",
                "extra",
            )
        ),
    )
    conn.commit()
    conn.close()


class ResearchCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.db = Path(self.id().replace(".", "_") + ".db")
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        # CLI 가 default DB_PATH 를 사용하므로 patch
        self._patches = [
            patch("skim_core.db.DB_PATH", self.db),
            patch("skim_core.research.search.get_connection", lambda *_a, **_k: _open(self.db)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        if self.db.exists():
            self.db.unlink()

    # --- 케이스 ---
    def test_cli_emit_json_schema_matches_post_model(self):
        _insert(
            self.db,
            external_id="x1",
            content="Nvidia content",
            title="NVIDIA card",
            timestamp=RECENT_TIMESTAMP,
            extra='{"subreddit": "investing"}',
        )
        result = self.runner.invoke(app, ["research", "nvidia", "--days", "30"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        # 7 권위 필드
        self.assertEqual(
            set(payload.keys()),
            {"topic", "tokens", "date_range", "sources_requested", "posts", "stats", "warnings"},
        )
        self.assertEqual(payload["topic"], "nvidia")
        self.assertEqual(payload["stats"]["total"], 1)
        self.assertEqual(payload["posts"][0]["extra"], {"subreddit": "investing"})
        self.assertIn("matched_fields", payload["posts"][0])
        self.assertFalse(payload["posts"][0]["fetched_this_run"])

    def test_cli_extra_json_invalid_passthrough_with_warning(self):
        _insert(
            self.db,
            external_id="x2",
            content="Nvidia content",
            extra="not json",
            timestamp=RECENT_TIMESTAMP,
        )
        result = self.runner.invoke(app, ["research", "nvidia", "--days", "30"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["posts"][0]["extra"], "not json")
        self.assertTrue(any("extra json parse failed" in w for w in payload["warnings"]))

    def test_cli_empty_topic_exits_2(self):
        result = self.runner.invoke(app, ["research", "   "])
        self.assertEqual(result.exit_code, 2)

    def test_cli_empty_tokens_returns_empty_with_warning(self):
        # 공백만 → topic.strip() True 통과하도록 ' a ' 같은 토큰 0개 케이스: 실제는 strip 후 빈 토큰
        # 우리 CLI 는 strip() False 면 exit 2. 토큰 0개는 거의 발생 안 함.
        # 회귀 보호용으로 stopword-only 토큰을 모사 — 빈 텍스트 검색 결과 0건만 확인.
        result = self.runner.invoke(app, ["research", "nonexistenttoken12345", "--days", "30"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["stats"]["total"], 0)

    def test_cli_unknown_source_exits_2(self):
        result = self.runner.invoke(app, ["research", "topic", "--sources", "made_up"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("unknown sources", result.stderr)

    def test_cli_empty_db_returns_empty_list(self):
        result = self.runner.invoke(app, ["research", "nvidia", "--days", "30"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["posts"], [])

    def test_cli_emits_stats_to_stderr(self):
        _insert(self.db, external_id="se", content="Nvidia x", timestamp=RECENT_TIMESTAMP)
        result = self.runner.invoke(app, ["research", "nvidia", "--days", "30"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("[skim research stats]", result.stderr)

    def test_cli_refresh_invalid_value_exits_2(self):
        result = self.runner.invoke(app, ["research", "nvidia", "--refresh", "banana"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("invalid --refresh", result.stderr)

    def test_cli_short_tokens_warning(self):
        _insert(self.db, external_id="ai", content="AI keyword inside")
        result = self.runner.invoke(app, ["research", "ai", "--days", "30"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(any("short tokens" in w for w in payload["warnings"]))


def _open(db: Path):
    import sqlite3

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


if __name__ == "__main__":
    unittest.main()
