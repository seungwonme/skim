"""Phase 2-B research_runs store CRUD 회귀 테스트."""

import json
import unittest
from pathlib import Path

from skim_core.db import init_db
from skim_core.research import store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(self.id().replace(".", "_") + ".db")
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self) -> None:
        if self.db.exists():
            self.db.unlink()

    def test_record_started_returns_id_with_running_status(self):
        run_id = store.record_started(
            topic="nvidia",
            tokens_key='["nvidia"]',
            sources_key='["all"]',
            refresh_mode="auto",
            days_requested=7,
            db_path=self.db,
        )
        run = store.load_run(run_id, db_path=self.db)
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["topic"], "nvidia")
        self.assertEqual(run["refresh_mode"], "auto")
        self.assertEqual(run["days_per_platform"], {})
        self.assertEqual(run["crawled_platforms"], [])

    def test_record_completed_updates_metrics(self):
        run_id = store.record_started(
            topic="nvidia",
            tokens_key='["nvidia"]',
            sources_key='["reddit"]',
            refresh_mode="auto",
            days_requested=7,
            db_path=self.db,
        )
        store.record_completed(
            run_id,
            result_count=10,
            newly_fetched=3,
            crawled_platforms=["reddit"],
            days_per_platform={"reddit": 7},
            window_expanded=1,
            db_path=self.db,
        )
        run = store.load_run(run_id, db_path=self.db)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["result_count"], 10)
        self.assertEqual(run["newly_fetched"], 3)
        self.assertEqual(run["crawled_platforms"], ["reddit"])
        self.assertEqual(run["days_per_platform"], {"reddit": 7})
        self.assertEqual(run["window_expanded"], 1)
        self.assertIsNotNone(run["finished_at"])

    def test_record_failed_truncates_error(self):
        run_id = store.record_started(
            topic="t",
            tokens_key="[]",
            sources_key="[]",
            refresh_mode="force",
            days_requested=1,
            db_path=self.db,
        )
        store.record_failed(run_id, "x" * 1000, db_path=self.db)
        run = store.load_run(run_id, db_path=self.db)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(len(run["error_message"]), 500)

    def test_load_days_per_platform_round_trip(self):
        run_id = store.record_started(
            topic="t",
            tokens_key="[]",
            sources_key="[]",
            refresh_mode="auto",
            days_requested=7,
            db_path=self.db,
        )
        store.record_completed(
            run_id,
            result_count=0,
            newly_fetched=0,
            crawled_platforms=[],
            days_per_platform={"reddit": 14, "hackernews": 7},
            window_expanded=1,
            db_path=self.db,
        )
        self.assertEqual(
            store.load_days_per_platform(run_id, db_path=self.db),
            {"reddit": 14, "hackernews": 7},
        )

    def test_load_run_missing_returns_none(self):
        self.assertIsNone(store.load_run(9999, db_path=self.db))

    def test_record_started_stores_runner_pid_and_host(self):
        run_id = store.record_started(
            topic="t",
            tokens_key="[]",
            sources_key="[]",
            refresh_mode="auto",
            days_requested=7,
            db_path=self.db,
        )
        run = store.load_run(run_id, db_path=self.db)
        self.assertIsNotNone(run["runner_pid"])
        self.assertIsNotNone(run["runner_host"])

    def test_started_row_json_defaults_are_parseable(self):
        run_id = store.record_started(
            topic="t",
            tokens_key="[]",
            sources_key="[]",
            refresh_mode="auto",
            days_requested=7,
            db_path=self.db,
        )
        run = store.load_run(run_id, db_path=self.db)
        # row 가 json 으로 직렬화 가능해야 (CLI JSON 응답에 노출 가능)
        json.dumps(run, default=str)


if __name__ == "__main__":
    unittest.main()
