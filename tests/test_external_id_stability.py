"""id 체계가 흔들려 생기는 누락과 중복을 검증한다.

producthunt는 피드가 주는 런치별 id를 버려서, 같은 제품 페이지의 두 번째
이후 런치가 DB에 저장되지 않았다. ailabs는 피드 guid와 URL 기반 id가 공존해
같은 글이 두 행으로 갈라졌다(실측 182행).
"""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from skim_core.crawlers.feed.producthunt import _item_to_post
from skim_core.db import dedupe_by_url, init_db, save_posts
from skim_core.models import Post


class ProductHuntRelaunchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "skim.db"
        init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _rows(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT external_id, title FROM posts ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    def _item(self, external_id, title):
        return {
            "platform": "producthunt",
            "title": title,
            "url": "https://www.producthunt.com/products/grok",
            "external_id": external_id,
            "published": "2026-08-10T00:00:00+09:00",
            "author": "Product Hunt",
        }

    def test_feed_id_reaches_the_post(self):
        post = _item_to_post(
            self._item("tag:www.producthunt.com,2005:Post/1219088", "Grok 4.5")
        )
        self.assertEqual(post.external_id, "tag:www.producthunt.com,2005:Post/1219088")

    def test_relaunch_of_the_same_product_is_kept(self):
        # 같은 제품 페이지(url)의 서로 다른 런치. id가 없으면 db가 URL로 병합해
        # 두 번째 런치가 사라진다.
        first = _item_to_post(self._item("tag:pt,2005:Post/1", "Grok 4.4"))
        second = _item_to_post(self._item("tag:pt,2005:Post/2", "Grok 4.5"))
        save_posts([first], "producthunt", "producthunt", self.db)
        save_posts([second], "producthunt", "producthunt", self.db)

        rows = self._rows()
        self.assertEqual(len(rows), 2, "재런치가 별도 행으로 남아야 한다")
        self.assertEqual(sorted(r[1] for r in rows), ["Grok 4.4", "Grok 4.5"])

    def test_same_launch_twice_is_still_one_row(self):
        item = self._item("tag:pt,2005:Post/1", "Grok 4.5")
        save_posts([_item_to_post(item)], "producthunt", "producthunt", self.db)
        save_posts([_item_to_post(item)], "producthunt", "producthunt", self.db)
        self.assertEqual(len(self._rows()), 1)


class DedupeByUrlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "skim.db"
        init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _post(self, external_id, body):
        return Post(
            platform="ailabs",
            author="LangChain",
            title="Agent harness",
            content=body,
            content_markdown=body,
            timestamp="2026-08-10T00:00:00+09:00",
            url="https://www.langchain.com/blog/agent-harness",
            external_id=external_id,
        )

    def _bodies(self):
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute("SELECT content_markdown FROM posts")]
        finally:
            conn.close()

    def test_split_rows_are_folded_keeping_the_longest_body(self):
        # 해시 id 시절 행과 URL 기반 id 행이 공존하던 상황
        save_posts(
            [self._post("ef284bdf89ac38a4", "short")], "ailabs", "ailabs", self.db
        )
        save_posts(
            [self._post("www.langchain.com/blog/agent-harness", "much longer body")],
            "ailabs",
            "ailabs",
            self.db,
        )
        self.assertEqual(len(self._bodies()), 2)

        result = dedupe_by_url("ailabs", self.db)

        self.assertEqual(result["removed"], 1)
        self.assertEqual(self._bodies(), ["much longer body"])

    def test_dry_run_does_not_delete(self):
        save_posts([self._post("a", "short")], "ailabs", "ailabs", self.db)
        save_posts([self._post("b", "longer body here")], "ailabs", "ailabs", self.db)

        result = dedupe_by_url("ailabs", self.db, dry_run=True)

        self.assertEqual(result["removed"], 1)
        self.assertEqual(len(self._bodies()), 2, "미리보기는 지우지 않는다")

    def test_is_idempotent(self):
        save_posts([self._post("a", "only one")], "ailabs", "ailabs", self.db)
        self.assertEqual(dedupe_by_url("ailabs", self.db)["removed"], 0)


if __name__ == "__main__":
    unittest.main()
