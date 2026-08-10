"""소비 상태(읽음/보관) 회귀 테스트.

`feedback` 테이블은 스키마와 `add_feedback()`만 있고 호출자가 0개, 행이 0개인 채로
남아 있었다. 하루 200건대가 들어오는데 어디까지 봤는지 표시할 데가 없었다.
새 컬럼 대신 이 테이블을 쓴다.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import typer

import skim_cli.cli as main
from skim_core.db import init_db, post_states, save_posts, set_post_state
from skim_core.models import Post


def _post(external_id, **overrides):
    data = {
        "platform": "blogs",
        "author": "a",
        "content": "",
        "content_markdown": "body",
        "title": f"T{external_id}",
        "timestamp": "2026-08-10T00:00:00+00:00",
        "url": f"https://example.com/{external_id}",
        "external_id": external_id,
    }
    data.update(overrides)
    return Post(**data)


class PostStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "skim.db"
        init_db(self.db_path)
        save_posts([_post("1"), _post("2"), _post("3")], "blogs", db_path=self.db_path)

    def _ids(self):
        conn = sqlite3.connect(self.db_path)
        ids = [row[0] for row in conn.execute("SELECT id FROM posts ORDER BY id")]
        conn.close()
        return ids

    def test_marking_read_is_visible(self):
        first = self._ids()[0]
        set_post_state(first, "read", self.db_path)

        self.assertEqual(post_states(self.db_path), {first: "read"})

    def test_state_is_single_valued(self):
        # read -> archived로 바꾸면 앞의 값이 남으면 안 된다.
        first = self._ids()[0]
        set_post_state(first, "read", self.db_path)
        set_post_state(first, "archived", self.db_path)

        self.assertEqual(post_states(self.db_path), {first: "archived"})
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        conn.close()
        self.assertEqual(rows, 1)

    def test_none_clears_the_state(self):
        first = self._ids()[0]
        set_post_state(first, "read", self.db_path)
        set_post_state(first, None, self.db_path)

        self.assertEqual(post_states(self.db_path), {})

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(ValueError):
            set_post_state(self._ids()[0], "starred", self.db_path)

    def test_unread_filter_excludes_marked_posts(self):
        ids = self._ids()
        set_post_state(ids[0], "read", self.db_path)
        set_post_state(ids[1], "archived", self.db_path)

        unread = main._recent_posts(self.db_path, 7, None, 10, unread_only=True)
        every = main._recent_posts(self.db_path, 7, None, 10)

        self.assertEqual(len(every), 3)
        self.assertEqual([row["external_id"] for row in unread], ["3"])

    def test_cli_rejects_an_unknown_state(self):
        with self.assertRaises(typer.Exit) as ctx:
            main.mark_posts(post_ids=[1], state="starred", db=self.db_path)
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_cli_marks_and_unmarks(self):
        ids = self._ids()
        main.mark_posts(post_ids=ids[:2], state="read", db=self.db_path)
        self.assertEqual(len(post_states(self.db_path)), 2)

        main.mark_posts(post_ids=ids[:2], state="unread", db=self.db_path)
        self.assertEqual(post_states(self.db_path), {})


if __name__ == "__main__":
    unittest.main()
