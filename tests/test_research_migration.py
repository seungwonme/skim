"""Phase 2-A research_runs migration 회귀 테스트."""

import sqlite3
import unittest
from pathlib import Path

from skim_core.db import RESEARCH_RUNS_CREATE_SQL, _ensure_column, _migrate_research_runs, init_db


class EnsureColumnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(self.id().replace(".", "_") + ".db")
        if self.db.exists():
            self.db.unlink()

    def tearDown(self) -> None:
        if self.db.exists():
            self.db.unlink()

    def test_ensure_column_idempotent(self):
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE t (id INTEGER)")
        _ensure_column(conn, "t", "name", "TEXT")
        _ensure_column(conn, "t", "name", "TEXT")  # idempotent
        cols = {row[1] for row in conn.execute("PRAGMA table_info(t)")}
        self.assertEqual(cols, {"id", "name"})
        conn.close()


class MigrateResearchRunsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(self.id().replace(".", "_") + ".db")
        if self.db.exists():
            self.db.unlink()

    def tearDown(self) -> None:
        if self.db.exists():
            self.db.unlink()

    def test_migrate_on_fresh_db(self):
        conn = sqlite3.connect(str(self.db))
        _migrate_research_runs(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
        for required in (
            "id",
            "topic",
            "tokens_key",
            "sources_key",
            "refresh_mode",
            "days_requested",
            "days_per_platform",
            "window_expanded",
            "result_count",
            "newly_fetched",
            "crawled_platforms",
            "started_at",
            "finished_at",
            "status",
            "runner_pid",
            "runner_host",
            "error_message",
        ):
            self.assertIn(required, cols, f"missing column: {required}")
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertGreaterEqual(ver, 1)
        conn.close()

    def test_migrate_on_existing_v0_db_missing_columns(self):
        """기존 minimal research_runs 테이블이 있어도 누락 컬럼이 채워져야."""
        conn = sqlite3.connect(str(self.db))
        conn.execute("""CREATE TABLE research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                tokens_key TEXT NOT NULL,
                sources_key TEXT NOT NULL,
                refresh_mode TEXT NOT NULL,
                days_requested INTEGER NOT NULL,
                result_count INTEGER NOT NULL,
                crawled_platforms TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                error_message TEXT
            )""")
        _migrate_research_runs(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
        for required in (
            "days_per_platform",
            "window_expanded",
            "newly_fetched",
            "runner_pid",
            "runner_host",
        ):
            self.assertIn(required, cols, f"missing migration column: {required}")
        conn.close()

    def test_migrate_idempotent_on_v1_db(self):
        conn = sqlite3.connect(str(self.db))
        _migrate_research_runs(conn)
        _migrate_research_runs(conn)  # 두 번 호출해도 안전
        conn.close()

    def test_init_db_invokes_research_migration(self):
        init_db(self.db)
        conn = sqlite3.connect(str(self.db))
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertIn("research_runs", tables)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertGreaterEqual(ver, 1)
        conn.close()

    def test_research_runs_indexes_exist(self):
        init_db(self.db)
        conn = sqlite3.connect(str(self.db))
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_runs'"
            )
        }
        for expected in (
            "idx_research_runs_topic",
            "idx_research_runs_started_at",
            "idx_research_runs_backoff",
            "idx_research_runs_status",
        ):
            self.assertIn(expected, indexes)
        conn.close()


class CreateSqlConstantTests(unittest.TestCase):
    def test_create_sql_contains_all_columns(self):
        for col in (
            "topic",
            "tokens_key",
            "sources_key",
            "refresh_mode",
            "days_requested",
            "days_per_platform",
            "window_expanded",
            "result_count",
            "newly_fetched",
            "crawled_platforms",
            "started_at",
            "finished_at",
            "status",
            "runner_pid",
            "runner_host",
            "error_message",
        ):
            self.assertIn(col, RESEARCH_RUNS_CREATE_SQL)


if __name__ == "__main__":
    unittest.main()
