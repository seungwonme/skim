"""이미 저장된 항목을 enrichment 전에 걸러내는 `drop_known_items` 검증.

producthunt는 7일 창으로 받으므로 매 회차 30건 넘게 겹친다. 저장은 UNIQUE가
거르지만 원문 추출과 댓글 조회는 그 전에 돌아서, 여기서 안 자르면 매일 다시 낸다.
"""

import tempfile
import unittest
from pathlib import Path

from skim_core.db import drop_known_items, get_connection, init_db


class DropKnownItemsTests(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp()) / "skim.db"
        init_db(self.db)
        conn = get_connection(self.db)
        conn.execute(
            "INSERT INTO posts(platform, external_id, url, title, author, content) "
            "VALUES ('producthunt', 'a', 'https://ph/x', 'A', 'u', 'c')"
        )
        conn.commit()
        conn.close()
        self.items = [
            {"external_id": "a", "url": "https://ph/x"},
            {"external_id": "b", "url": "https://ph/x"},
            {"external_id": "c", "url": "https://ph/y"},
        ]

    def test_filters_by_the_requested_key(self):
        # 같은 URL로 재런칭한 글은 id가 다르므로 id 기준이면 남고 URL 기준이면 빠진다.
        by_id = drop_known_items("producthunt", self.items, "external_id", self.db)
        self.assertEqual([i["external_id"] for i in by_id], ["b", "c"])
        by_url = drop_known_items("producthunt", self.items, "url", self.db)
        self.assertEqual([i["external_id"] for i in by_url], ["c"])

    def test_other_platforms_and_db_errors_leave_items_alone(self):
        self.assertEqual(drop_known_items("youtube", self.items, "url", self.db), self.items)
        no_tables = Path(tempfile.mkdtemp()) / "empty.db"
        self.assertEqual(drop_known_items("producthunt", self.items, "url", no_tables), self.items)


if __name__ == "__main__":
    unittest.main()
