"""save_posts의 연결 수명과 정본 본문 판정 회귀 테스트.

`commit()`/`close()`가 try 밖에 있어서 sqlite3.Error가 아닌 예외(예: extra에 섞인
datetime의 json.dumps TypeError)가 나면 RESERVED 락이 남았다. 그러면 뒤따르는
finish_run이 60초를 기다리다 `database is locked`로 죽어 원래 오류를 덮는다.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from skim_core.db import (
    backup_db,
    canonical_body,
    canonical_url_for,
    check_integrity,
    init_db,
    save_posts,
    save_run,
)
from skim_core.models import Post


def _post(**overrides):
    data = {
        "platform": "reddit",
        "author": "tester",
        "content": "body text",
        "timestamp": "2026-08-10T00:00:00+09:00",
        "url": "https://example.com/a",
        "external_id": "abc",
    }
    data.update(overrides)
    return Post(**data)


class SavePostsConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "skim.db"
        init_db(self.db_path)

    def test_non_serializable_extra_does_not_lose_the_batch(self):
        # Post는 extra="allow"라 크롤러가 datetime을 실어 보낼 수 있다.
        posts = [
            _post(external_id="one", crawled_from=datetime.now(timezone.utc)),
            _post(external_id="two"),
        ]

        saved = save_posts(posts, "reddit", db_path=self.db_path)

        self.assertEqual(saved, 2, "직렬화 불가 값이 배치를 통째로 날리면 안 된다")

    def test_connection_is_released_for_the_next_writer(self):
        # 락이 남으면 이 호출이 60초 기다리다 `database is locked`로 죽는다.
        save_posts(
            [_post(nested={"dt": datetime.now(timezone.utc)})],
            "reddit",
            db_path=self.db_path,
        )

        run_id = save_run("running", self.db_path)

        self.assertIsInstance(run_id, int)

    def test_rows_survive_a_bad_neighbour(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=1000")
        rows = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        conn.close()
        self.assertEqual(rows, 0)

        save_posts([_post(external_id="keep")], "reddit", db_path=self.db_path)

        conn = sqlite3.connect(self.db_path)
        stored = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        conn.close()
        self.assertEqual(stored, 1)


class CanonicalBodyTests(unittest.TestCase):
    """크롤 요약의 결손 집계가 save_posts와 같은 판정을 쓰는지 본다."""

    def test_api_platform_body_arrives_in_content(self):
        # 승격 전 content_markdown만 보면 API형 4종이 전량 실패로 오탐된다.
        for platform in ("linkedin", "threads", "x", "reddit"):
            with self.subTest(platform=platform):
                post = _post(
                    platform=platform, content="post body", content_markdown=None
                )
                self.assertEqual(canonical_body(post, platform), "post body")

    def test_feed_platform_content_is_not_a_body(self):
        post = _post(
            platform="hackernews", content="title duplicate", content_markdown=None
        )
        self.assertEqual(canonical_body(post, "hackernews"), "")

    def test_existing_markdown_wins(self):
        post = _post(platform="reddit", content="raw", content_markdown="  rendered  ")
        self.assertEqual(canonical_body(post, "reddit"), "rendered")


class CanonicalUrlTests(unittest.TestCase):
    def test_strips_tracking_and_normalises_host(self):
        self.assertEqual(
            canonical_url_for("http://www.Example.com/post/?utm_source=rss&id=7#top"),
            "https://example.com/post?id=7",
        )

    def test_mobile_subdomain_matches_desktop(self):
        self.assertEqual(
            canonical_url_for("https://m.example.com/a"),
            canonical_url_for("https://example.com/a"),
        )

    def test_returns_none_for_unusable_input(self):
        for value in ("", None, "not a url", "mailto:a@b.c"):
            with self.subTest(value=value):
                self.assertIsNone(canonical_url_for(value))

    def test_saved_post_gets_a_cluster_key(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = Path(temp_dir.name) / "skim.db"
        init_db(db_path)

        save_posts(
            [_post(url="https://www.example.com/story/?utm_medium=email")],
            "reddit",
            db_path=db_path,
        )

        conn = sqlite3.connect(db_path)
        stored = conn.execute("SELECT canonical_url FROM posts").fetchone()[0]
        conn.close()
        self.assertEqual(stored, "https://example.com/story")


class BackupTests(unittest.TestCase):
    def test_backup_is_a_readable_copy(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = Path(temp_dir.name) / "skim.db"
        init_db(db_path)
        save_posts([_post()], "reddit", db_path=db_path)

        dest = backup_db(Path(temp_dir.name) / "backups" / "copy.db", db_path)

        self.assertTrue(dest.exists())
        conn = sqlite3.connect(dest)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)
        conn.close()
        self.assertEqual(check_integrity(dest), "ok")


if __name__ == "__main__":
    unittest.main()
