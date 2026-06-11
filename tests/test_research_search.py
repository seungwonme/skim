"""Phase 1 search_posts 회귀 테스트."""

import unittest
from pathlib import Path

from skim_core.db import get_connection, init_db
from skim_core.research.search import search_posts
from skim_core.research.types import SearchStats


def _insert(db: Path, **kw) -> None:
    conn = get_connection(db)
    defaults = {
        "platform": "hackernews",
        "source": None,
        "external_id": None,
        "author": "u",
        "title": None,
        "content": "",
        "url": None,
        "timestamp": "2026-04-15T00:00:00+00:00",
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


class SearchPostsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(self.id().replace(".", "_") + ".db")
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self) -> None:
        if self.db.exists():
            self.db.unlink()

    SINCE = "2026-04-01T00:00:00+00:00"

    def test_search_single_token_title(self):
        _insert(self.db, external_id="t1", title="NVIDIA earnings", content="body")
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "NVIDIA earnings")

    def test_search_single_token_content(self):
        _insert(self.db, external_id="c1", title=None, content="all about Nvidia chips")
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertIn("content", rows[0]["matched_fields"])

    def test_search_single_token_content_markdown(self):
        _insert(
            self.db,
            external_id="cm1",
            title=None,
            content="placeholder",
            content_markdown="enriched body with **nvidia** keyword",
        )
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertIn("content_markdown", rows[0]["matched_fields"])

    def test_search_single_token_summary(self):
        _insert(
            self.db,
            external_id="s1",
            title=None,
            content="x",
            summary="Quick Nvidia summary",
        )
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertIn("summary", rows[0]["matched_fields"])

    def test_search_multi_token_and(self):
        _insert(self.db, external_id="a", title="AI video model", content="x")
        _insert(self.db, external_id="b", title="AI only", content="text without v")
        rows, _, _ = search_posts("ai video", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "AI video model")

    def test_search_case_insensitive(self):
        _insert(self.db, external_id="case", title="NVIDIA chip", content="x")
        rows, _, _ = search_posts("Nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)

    def test_search_null_title_still_matches_via_content(self):
        _insert(self.db, external_id="nt", title=None, content="Nvidia in content")
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)

    def test_search_platform_filter(self):
        _insert(self.db, external_id="hn1", platform="hackernews", content="Nvidia hn")
        _insert(self.db, external_id="rd1", platform="reddit", content="Nvidia reddit")
        rows, _, _ = search_posts("nvidia", self.SINCE, ["reddit"], 10, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "reddit")

    def test_search_date_range_utc(self):
        _insert(self.db, external_id="new", content="Nvidia", timestamp="2026-04-15T00:00:00+00:00")
        _insert(self.db, external_id="old", content="Nvidia", timestamp="2025-01-01T00:00:00+00:00")
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_id"], "new")

    def test_search_rejects_non_utc_since_iso(self):
        with self.assertRaises(ValueError):
            search_posts("nvidia", "2026-04-01T00:00:00+09:00", None, 10, db_path=self.db)

    def test_search_rejects_naive_since_iso(self):
        with self.assertRaises(ValueError):
            search_posts("nvidia", "2026-04-01T00:00:00", None, 10, db_path=self.db)

    def test_search_timestamp_string_lexicographic_ordering(self):
        _insert(self.db, external_id="a", content="nvidia", timestamp="2026-04-15T00:00:00+00:00")
        _insert(self.db, external_id="b", content="nvidia", timestamp="2026-04-10T00:00:00+00:00")
        _insert(self.db, external_id="c", content="nvidia", timestamp="2026-04-20T00:00:00+00:00")
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        order = [r["external_id"] for r in rows]
        self.assertEqual(order, ["c", "a", "b"])

    def test_search_per_platform_limit(self):
        for i in range(5):
            _insert(
                self.db,
                external_id=f"hn{i}",
                platform="hackernews",
                content=f"nvidia {i}",
                timestamp=f"2026-04-{10+i:02d}T00:00:00+00:00",
            )
            _insert(
                self.db,
                external_id=f"rd{i}",
                platform="reddit",
                content=f"nvidia {i}",
                timestamp=f"2026-04-{10+i:02d}T00:00:00+00:00",
            )
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 2, db_path=self.db)
        per_platform: dict[str, int] = {}
        for r in rows:
            per_platform[r["platform"]] = per_platform.get(r["platform"], 0) + 1
        self.assertEqual(per_platform.get("hackernews", 0), 2)
        self.assertEqual(per_platform.get("reddit", 0), 2)

    def test_search_like_escape_percent(self):
        _insert(self.db, external_id="p1", content="literal 50% off promo")
        _insert(self.db, external_id="p2", content="zero 50 off promo")  # 50과 off 사이 % 없음
        rows, _, _ = search_posts("50%", self.SINCE, None, 10, db_path=self.db)
        ids = sorted(r["external_id"] for r in rows)
        self.assertEqual(ids, ["p1"])

    def test_search_like_escape_underscore(self):
        _insert(self.db, external_id="u1", content="snake_case keyword")
        _insert(self.db, external_id="u2", content="snake-case fallback")
        rows, _, _ = search_posts("snake_case", self.SINCE, None, 10, db_path=self.db)
        ids = [r["external_id"] for r in rows]
        self.assertEqual(ids, ["u1"])

    def test_search_attaches_matched_fields(self):
        _insert(
            self.db,
            external_id="mf",
            title="Nvidia card",
            content="something about Nvidia",
            summary="quick Nvidia summary",
        )
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)
        fields = set(rows[0]["matched_fields"])
        self.assertEqual(fields, {"title", "content", "summary"})

    def test_search_returns_search_stats(self):
        _insert(self.db, external_id="x", content="Nvidia")
        rows, stats, _ = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        self.assertIsInstance(stats, SearchStats)
        self.assertEqual(stats.rows_returned, len(rows))
        self.assertEqual(stats.rows_scanned, 1)
        self.assertGreaterEqual(stats.latency_ms, 0)
        self.assertEqual(stats.short_tokens, [])

    def test_search_warns_short_tokens(self):
        _insert(self.db, external_id="s", content="real ai")
        _, stats, _ = search_posts("ai", self.SINCE, None, 10, db_path=self.db)
        self.assertEqual(stats.short_tokens, ["ai"])

    def test_search_empty_topic_returns_all_within_window(self):
        # 토큰 0개여도 함수는 동작 (호출자가 warning 처리). 즉시 stopword fallback 금지는 CLI 책임.
        _insert(self.db, external_id="x", content="content")
        rows, stats, _ = search_posts("   ", self.SINCE, None, 10, db_path=self.db)
        # tokens 0개 → 검색 조건은 timestamp 만. 1건 반환.
        self.assertEqual(stats.rows_returned, 1)
        self.assertEqual(rows[0]["matched_fields"], [])

    def test_search_accepts_z_suffix(self):
        _insert(self.db, external_id="z", content="nvidia")
        # 'Z' suffix 도 받아서 +00:00 으로 정규화
        rows, _, _ = search_posts("nvidia", "2026-04-01T00:00:00Z", None, 10, db_path=self.db)
        self.assertEqual(len(rows), 1)

    def test_search_per_platform_limit_window_function(self):
        """codex Phase 1 review: ROW_NUMBER() PARTITION BY 가 starvation 막아야 함."""
        # reddit: 5개, hackernews: 2개. limit=2 면 둘 다 2개씩 반환되어야.
        for i in range(5):
            _insert(
                self.db,
                external_id=f"rd{i}",
                platform="reddit",
                content=f"nvidia {i}",
                timestamp=f"2026-04-{20-i:02d}T00:00:00+00:00",
            )
        for i in range(2):
            _insert(
                self.db,
                external_id=f"hn{i}",
                platform="hackernews",
                content=f"nvidia {i}",
                timestamp=f"2026-04-{10-i:02d}T00:00:00+00:00",
            )
        rows, _, _ = search_posts("nvidia", self.SINCE, None, 2, db_path=self.db)
        by_p: dict[str, int] = {}
        for r in rows:
            by_p[r["platform"]] = by_p.get(r["platform"], 0) + 1
        self.assertEqual(by_p, {"reddit": 2, "hackernews": 2})

    def test_search_emits_unparseable_timestamp_warning(self):
        _insert(self.db, external_id="bad", content="nvidia", timestamp="garbage")
        # WHERE 단계에서 'garbage' >= since 가 False 일 수 있어, 직접 timestamp 무효지만
        # 사전순 비교상 'garbage' > '2026-...' 가능 (g > 2). 어쨌든 warning 발행.
        rows, _, warnings = search_posts("nvidia", self.SINCE, None, 10, db_path=self.db)
        if rows:
            self.assertTrue(any("unparseable_timestamp" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
