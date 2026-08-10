"""비어 있는 author가 복구되는지 검증한다.

실측 456행(ailabs/OpenAI News 424, blogs 32)이 영구 공백이었다. upsert에
author 복구 분기가 없었고, WHERE 가드에도 author 조건이 없어 문장 자체가
걸러졌다. 대부분은 크롤 창보다 오래된 행이라 재크롤로도 안 채워진다.
"""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from skim_core.db import backfill_blank_authors, init_db, save_posts
from skim_core.models import Post


def _post(**overrides):
    data = {
        "platform": "blogs",
        "author": "Addy Osmani",
        "title": "Agent Harness Engineering",
        "content": "body",
        "timestamp": "2026-08-10T00:00:00+09:00",
        "url": "https://addyosmani.com/a",
        "external_id": "a1",
    }
    data.update(overrides)
    return Post(**data)


class BlankAuthorRepairTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "skim.db"
        init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _authors(self):
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute("SELECT author FROM posts ORDER BY id")]
        finally:
            conn.close()

    def test_recrawl_fills_a_blank_author(self):
        save_posts([_post(author="")], "blogs", "blogs/Addy Osmani", self.db)
        self.assertEqual(self._authors(), [""])

        save_posts([_post(author="Addy Osmani")], "blogs", "blogs/Addy Osmani", self.db)
        self.assertEqual(self._authors(), ["Addy Osmani"])

    def test_recrawl_does_not_overwrite_an_existing_author(self):
        save_posts([_post(author="Real Name")], "blogs", "blogs/Addy Osmani", self.db)
        save_posts([_post(author="Wrong Name")], "blogs", "blogs/Addy Osmani", self.db)
        self.assertEqual(self._authors(), ["Real Name"])

    def test_backfill_uses_the_source_tail(self):
        # 재크롤이 없을 옛 행은 백필로만 채울 수 있다.
        save_posts([_post(author="")], "blogs", "blogs/Addy Osmani", self.db)
        save_posts(
            [_post(author="", external_id="b2", url="https://openai.com/x")],
            "ailabs",
            "ailabs/OpenAI News",
            self.db,
        )

        filled = backfill_blank_authors(self.db)

        self.assertEqual(filled, 2)
        self.assertEqual(sorted(self._authors()), ["Addy Osmani", "OpenAI News"])

    def test_backfill_is_idempotent(self):
        save_posts([_post(author="")], "blogs", "blogs/Addy Osmani", self.db)
        backfill_blank_authors(self.db)
        self.assertEqual(backfill_blank_authors(self.db), 0)


if __name__ == "__main__":
    unittest.main()
