"""Phase 0 timestamp migration 스크립트 회귀 테스트."""

import importlib.util
import sqlite3
import sys
import unittest
from datetime import (  # noqa: F401 — datetime API used in DetectAndNormalizeTests
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = ROOT / "scripts" / "normalize_existing_timestamps.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("normalize_existing_timestamps", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _seed(db_path: Path, rows: list[tuple[str, str]]) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT
        )""")
    for platform, ts in rows:
        conn.execute(
            "INSERT INTO posts(platform, author, content, timestamp) VALUES (?,?,?,?)",
            (platform, "a", "c", ts),
        )
    conn.commit()
    conn.close()


def _read(db_path: Path) -> list[tuple[int, str]]:
    conn = sqlite3.connect(str(db_path))
    rows = [(r[0], r[1]) for r in conn.execute("SELECT id, timestamp FROM posts").fetchall()]
    conn.close()
    return rows


class DetectAndNormalizeTests(unittest.TestCase):
    def test_epoch_branch(self):
        new_value, reason = migration.detect_and_normalize("1700000000", "hackernews")
        self.assertEqual(reason, "epoch")
        self.assertEqual(new_value, "2023-11-14T22:13:20+00:00")

    def test_relative_ko_skipped_to_avoid_reanchor(self):
        """codex review HIGH: 마이그레이션이 NOW 기준 재anchor 하면 wall-clock 손실."""
        new_value, reason = migration.detect_and_normalize("3시간 전", "geeknews")
        self.assertIsNone(new_value)
        self.assertEqual(reason, "relative_ko_skipped")

    def test_relative_ko_skipped_preserves_noisy_text(self):
        new_value, reason = migration.detect_and_normalize("3시간 전 2분", "geeknews")
        self.assertIsNone(new_value)
        self.assertEqual(reason, "relative_ko_skipped")

    def test_iso_branch_kst_offset_converted(self):
        new_value, reason = migration.detect_and_normalize("2026-04-19T14:00:00+09:00", "youtube")
        self.assertEqual(reason, "iso")
        self.assertEqual(new_value, "2026-04-19T05:00:00+00:00")

    def test_iso_branch_already_utc_kept(self):
        new_value, reason = migration.detect_and_normalize("2026-04-19T05:00:00+00:00", "youtube")
        self.assertEqual(reason, "iso")
        self.assertEqual(new_value, "2026-04-19T05:00:00+00:00")

    def test_empty_returns_empty_reason(self):
        new_value, reason = migration.detect_and_normalize("", "x")
        self.assertIsNone(new_value)
        self.assertEqual(reason, "empty")

    def test_unparseable_falls_through_to_unknown(self):
        new_value, reason = migration.detect_and_normalize("yesterday", "x")
        self.assertIsNone(new_value)
        self.assertEqual(reason, "unknown")

    def test_unparseable_relative_still_skipped(self):
        new_value, reason = migration.detect_and_normalize("어제 전", "geeknews")
        self.assertIsNone(new_value)
        self.assertEqual(reason, "relative_ko_skipped")


class NormalizeDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.id().replace(".", "_") + ".db")
        if self.tmp.exists():
            self.tmp.unlink()

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()

    def test_migration_script_dry_run_no_writes(self):
        _seed(self.tmp, [("hackernews", "1700000000")])
        stats = migration.normalize_db(self.tmp, commit=False)
        self.assertEqual(stats["updated"], 1)
        rows = _read(self.tmp)
        # commit=False 이므로 원본 유지
        self.assertEqual(rows[0][1], "1700000000")

    def test_migration_script_commit_updates_epoch_rows(self):
        _seed(self.tmp, [("hackernews", "1700000000"), ("hackernews", "1712345678901")])
        stats = migration.normalize_db(self.tmp, commit=True)
        self.assertEqual(stats["updated"], 2)
        rows = dict(_read(self.tmp))
        self.assertEqual(rows[1], "2023-11-14T22:13:20+00:00")
        self.assertEqual(rows[2], "2024-04-05T19:34:38+00:00")

    def test_migration_script_logs_unparseable_rows(self):
        _seed(self.tmp, [("x", "garbage-value")])
        stats = migration.normalize_db(self.tmp, commit=True)
        self.assertEqual(stats["failed"], 1)
        # 원본 그대로 유지
        rows = _read(self.tmp)
        self.assertEqual(rows[0][1], "garbage-value")

    def test_migration_script_skips_already_canonical(self):
        _seed(self.tmp, [("youtube", "2026-04-19T05:00:00+00:00")])
        stats = migration.normalize_db(self.tmp, commit=True)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["updated"], 0)


if __name__ == "__main__":
    unittest.main()
