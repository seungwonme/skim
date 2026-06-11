"""Phase 2-C/D/E research/refresh.py 회귀 테스트."""

from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from skim_core.db import get_connection, init_db
from skim_core.research import store
from skim_core.research.refresh import (
    BACKOFF_WINDOW_MINUTES,
    DEFAULT_WINDOW_EXPANSION,
    STALE_RUNNING_TTL_MINUTES,
    ConcurrentResearchError,
    NoSessionError,
    _build_crawler_options,
    _canonical_key,
    _cleanup_stale_research_runs,
    _expansion_candidates,
    _filter_by_session,
    _has_running,
    research_lock,
    run_research,
    should_refresh_per_platform,
    within_backoff,
)

UTC = timezone.utc


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hours_ago_iso(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


class CanonicalKeyTests(unittest.TestCase):
    def test_canonical_key_order_insensitive(self):
        self.assertEqual(_canonical_key(["b", "a"]), _canonical_key(["a", "b"]))

    def test_canonical_key_dedup(self):
        self.assertEqual(_canonical_key(["x", "x", "y"]), _canonical_key(["x", "y"]))

    def test_canonical_key_empty(self):
        self.assertEqual(_canonical_key([]), "[]")


class ExpansionCandidatesTests(unittest.TestCase):
    def test_days_lt_7_expands_to_full_ladder(self):
        self.assertEqual(_expansion_candidates(7), list(DEFAULT_WINDOW_EXPANSION))

    def test_days_eq_30_returns_only_self(self):
        self.assertEqual(_expansion_candidates(30), [30])

    def test_days_gt_30_no_unboundlocal(self):
        """RISK-03: days > 30 도 안전하게 single-step return."""
        self.assertEqual(_expansion_candidates(60), [60])


class ShouldRefreshPerPlatformTests(unittest.TestCase):
    SOURCES = ["hackernews", "reddit"]

    def test_force_returns_all(self):
        self.assertEqual(should_refresh_per_platform([], "force", self.SOURCES), self.SOURCES)

    def test_never_returns_empty(self):
        self.assertEqual(should_refresh_per_platform([], "never", self.SOURCES), [])

    def test_auto_stale_only(self):
        results = [{"platform": "hackernews", "timestamp": _hours_ago_iso(1)}] * 5  # fresh + enough
        stale = should_refresh_per_platform(results, "auto", self.SOURCES)
        self.assertEqual(stale, ["reddit"])  # reddit no posts → stale

    def test_auto_invalid_timestamp_triggers_refresh(self):
        results = [{"platform": "hackernews", "timestamp": "garbage"}] * 10
        stale = should_refresh_per_platform(results, "auto", ["hackernews"])
        self.assertEqual(stale, ["hackernews"])

    def test_auto_stale_when_old(self):
        results = [{"platform": "hackernews", "timestamp": _hours_ago_iso(24)}] * 10
        stale = should_refresh_per_platform(results, "auto", ["hackernews"])
        self.assertEqual(stale, ["hackernews"])


class CrawlerOptionsTests(unittest.TestCase):
    def test_feed_uses_since(self):
        opts = _build_crawler_options("hackernews", 7)
        self.assertIn("since", opts)
        self.assertEqual(opts.get("no_content"), False)

    def test_sns_uses_count(self):
        opts = _build_crawler_options("threads", 7)
        self.assertEqual(opts.get("count"), 70)

    def test_reddit_homefeed_options(self):
        opts = _build_crawler_options("reddit", 7)
        self.assertEqual(opts.get("sort"), "hot")
        self.assertEqual(opts.get("count"), 70)

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            _build_crawler_options("nonexistent", 7)


class FilterBySessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        (self.workspace / "data" / "sessions").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_keeps_platforms_with_session(self):
        (self.workspace / "data" / "sessions" / "threads_session.json").write_text("{}")
        kept, skipped = _filter_by_session(["threads"], explicit=False, workspace=self.workspace)
        self.assertEqual(kept, ["threads"])
        self.assertEqual(skipped, [])

    def test_skips_missing_session(self):
        kept, skipped = _filter_by_session(["threads"], explicit=False, workspace=self.workspace)
        self.assertEqual(kept, [])
        self.assertEqual(skipped, ["threads"])

    def test_explicit_missing_session_raises(self):
        with self.assertRaises(NoSessionError):
            _filter_by_session(["threads"], explicit=True, workspace=self.workspace)

    def test_reddit_with_subreddit_no_session_required(self):
        kept, _ = _filter_by_session(
            ["reddit"],
            explicit=False,
            options_by_platform={"reddit": {"subreddit": "python"}},
            workspace=self.workspace,
        )
        self.assertEqual(kept, ["reddit"])

    def test_feed_platform_no_session_required(self):
        kept, _ = _filter_by_session(["hackernews"], explicit=False, workspace=self.workspace)
        self.assertEqual(kept, ["hackernews"])


class WithinBackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(self.id().replace(".", "_") + ".db")
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self) -> None:
        if self.db.exists():
            self.db.unlink()

    def _insert(self, *, tokens_key: str, sources_key: str, status: str, started_at: str) -> None:
        conn = sqlite3.connect(str(self.db))
        conn.execute(
            """INSERT INTO research_runs
               (topic, tokens_key, sources_key, refresh_mode, days_requested,
                started_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("t", tokens_key, sources_key, "auto", 7, started_at, status),
        )
        conn.commit()
        conn.close()

    def test_true_for_recent_completed_same_key(self):
        recent = _hours_ago_iso(0.1)
        self._insert(tokens_key='["a"]', sources_key='["b"]', status="completed", started_at=recent)
        conn = get_connection(self.db)
        try:
            self.assertTrue(within_backoff(conn, '["a"]', '["b"]'))
        finally:
            conn.close()

    def test_false_for_different_tokens(self):
        recent = _hours_ago_iso(0.1)
        self._insert(tokens_key='["a"]', sources_key='["b"]', status="completed", started_at=recent)
        conn = get_connection(self.db)
        try:
            self.assertFalse(within_backoff(conn, '["x"]', '["b"]'))
        finally:
            conn.close()

    def test_false_for_old_completed(self):
        old = (datetime.now(UTC) - timedelta(minutes=BACKOFF_WINDOW_MINUTES + 5)).isoformat()
        self._insert(tokens_key='["a"]', sources_key='["b"]', status="completed", started_at=old)
        conn = get_connection(self.db)
        try:
            self.assertFalse(within_backoff(conn, '["a"]', '["b"]'))
        finally:
            conn.close()

    def test_ignores_interrupted_rows(self):
        recent = _hours_ago_iso(0.1)
        self._insert(
            tokens_key='["a"]', sources_key='["b"]', status="interrupted", started_at=recent
        )
        conn = get_connection(self.db)
        try:
            self.assertFalse(within_backoff(conn, '["a"]', '["b"]'))
        finally:
            conn.close()

    def test_ignores_failed_rows(self):
        recent = _hours_ago_iso(0.1)
        self._insert(tokens_key='["a"]', sources_key='["b"]', status="failed", started_at=recent)
        conn = get_connection(self.db)
        try:
            self.assertFalse(within_backoff(conn, '["a"]', '["b"]'))
        finally:
            conn.close()


class CleanupStaleResearchRunsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(self.id().replace(".", "_") + ".db")
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self) -> None:
        if self.db.exists():
            self.db.unlink()

    def _insert_running(self, **kw) -> int:
        defaults = {
            "topic": "t",
            "tokens_key": "[]",
            "sources_key": "[]",
            "refresh_mode": "auto",
            "days_requested": 7,
            "started_at": _now_iso(),
            "status": "running",
            "runner_pid": os.getpid(),
            "runner_host": socket.gethostname(),
        }
        defaults.update(kw)
        conn = sqlite3.connect(str(self.db))
        cur = conn.execute(
            """INSERT INTO research_runs
               (topic, tokens_key, sources_key, refresh_mode, days_requested,
                started_at, status, runner_pid, runner_host)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(
                defaults[k]
                for k in (
                    "topic",
                    "tokens_key",
                    "sources_key",
                    "refresh_mode",
                    "days_requested",
                    "started_at",
                    "status",
                    "runner_pid",
                    "runner_host",
                )
            ),
        )
        rid = cur.lastrowid
        conn.commit()
        conn.close()
        return rid

    def test_cleanup_preserves_live_pid(self):
        rid = self._insert_running()  # 현재 process PID, host
        conn = get_connection(self.db)
        try:
            cleaned = _cleanup_stale_research_runs(conn)
        finally:
            conn.close()
        self.assertEqual(cleaned, 0)
        run = store.load_run(rid, db_path=self.db)
        self.assertEqual(run["status"], "running")

    def test_cleanup_dead_pid_transitions_to_interrupted(self):
        # PID 1 은 launchd. kill(0) PermissionError 가능 (alive 로 간주됨).
        # 정말 죽은 PID 가 필요하므로 매우 큰 PID 사용 (보장된 없음).
        rid = self._insert_running(runner_pid=2_147_483_640)
        conn = get_connection(self.db)
        try:
            cleaned = _cleanup_stale_research_runs(conn)
        finally:
            conn.close()
        self.assertEqual(cleaned, 1)
        run = store.load_run(rid, db_path=self.db)
        self.assertEqual(run["status"], "interrupted")

    def test_cleanup_ttl_exceeded(self):
        very_old = (
            datetime.now(UTC) - timedelta(minutes=STALE_RUNNING_TTL_MINUTES + 5)
        ).isoformat()
        rid = self._insert_running(started_at=very_old, runner_host="other-host")
        conn = get_connection(self.db)
        try:
            cleaned = _cleanup_stale_research_runs(conn)
        finally:
            conn.close()
        self.assertEqual(cleaned, 1)
        run = store.load_run(rid, db_path=self.db)
        self.assertEqual(run["status"], "interrupted")


class HasRunningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(self.id().replace(".", "_") + ".db")
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)

    def tearDown(self) -> None:
        if self.db.exists():
            self.db.unlink()

    def test_true_when_running_exists(self):
        store.record_started(
            topic="t",
            tokens_key='["a"]',
            sources_key='["b"]',
            refresh_mode="auto",
            days_requested=7,
            db_path=self.db,
        )
        conn = get_connection(self.db)
        try:
            self.assertTrue(_has_running(conn, '["a"]', '["b"]'))
        finally:
            conn.close()

    def test_false_after_completed(self):
        rid = store.record_started(
            topic="t",
            tokens_key='["a"]',
            sources_key='["b"]',
            refresh_mode="auto",
            days_requested=7,
            db_path=self.db,
        )
        store.record_completed(
            rid,
            result_count=0,
            newly_fetched=0,
            crawled_platforms=[],
            days_per_platform={},
            window_expanded=0,
            db_path=self.db,
        )
        conn = get_connection(self.db)
        try:
            self.assertFalse(_has_running(conn, '["a"]', '["b"]'))
        finally:
            conn.close()


class ResearchLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_acquire_when_free(self):
        with research_lock(self.workspace):
            pass  # no error

    def test_blocks_concurrent_acquire(self):
        with research_lock(self.workspace):
            with self.assertRaises(ConcurrentResearchError):
                with research_lock(self.workspace):
                    pass


class RunResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.db = self.workspace / "data" / "skim.db"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        init_db(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, **kw):
        return asyncio.run(
            run_research(
                topic=kw.get("topic", "nvidia"),
                sources=kw.get("sources", ["hackernews"]),
                days=kw.get("days", 7),
                limit=kw.get("limit", 10),
                refresh_mode=kw.get("refresh_mode", "never"),
                explicit=kw.get("explicit", True),
                db_path=self.db,
                workspace=self.workspace,
            )
        )

    def test_never_mode_returns_zero_exit_and_response(self):
        code, resp = self._run(refresh_mode="never")
        self.assertEqual(code, 0)
        self.assertEqual(resp["topic"], "nvidia")
        self.assertEqual(resp["stats"]["total"], 0)

    def test_force_mode_blocks_concurrent(self):
        # 첫 lock 잡고, 두 번째는 force 라 exit 4
        with research_lock(self.workspace):
            code, _ = self._run(refresh_mode="force", explicit=False)
        self.assertEqual(code, 4)

    def test_auto_mode_concurrent_returns_cached(self):
        with research_lock(self.workspace):
            code, resp = self._run(refresh_mode="auto", explicit=False)
        self.assertEqual(code, 0)
        self.assertTrue(any("concurrent" in w.lower() for w in resp["warnings"]))

    def test_no_session_error_maps_to_exit_1(self):
        # explicit threads + no session → NoSessionError → exit 1
        # refresh_mode=force 로 lock 까지 통과해 refresh_platforms 가 호출되게.
        # threads session 없음 (workspace 비었음).
        code, _ = self._run(
            refresh_mode="force",
            sources=["threads"],
            explicit=True,
        )
        self.assertEqual(code, 1)

    def test_auto_missing_session_is_structured_warning(self):
        code, resp = self._run(
            refresh_mode="auto",
            sources=["threads"],
            explicit=False,
        )
        self.assertEqual(code, 0)
        self.assertTrue(
            any("threads: no session file, skipped" in warning for warning in resp["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
