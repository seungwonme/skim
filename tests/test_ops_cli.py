"""Operational CLI commands for agent-facing Skim workflows."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from skim_cli.cli import app
from skim_core.db import get_connection, init_db

RECENT_TIMESTAMP = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
BASELINE_TIMESTAMP = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()


def _insert(db: Path, **kw) -> None:
    defaults = {
        "platform": "hackernews",
        "external_id": "hn-1",
        "author": "pg",
        "title": "Nvidia results",
        "content": "Nvidia earnings and GPU demand",
        "url": "https://example.com/nvidia",
        "timestamp": RECENT_TIMESTAMP,
        "summary": "",
        "content_markdown": "",
    }
    defaults.update(kw)
    conn = get_connection(db)
    conn.execute(
        """INSERT INTO posts
           (platform, external_id, author, title, content, url, timestamp, summary, content_markdown)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            defaults["platform"],
            defaults["external_id"],
            defaults["author"],
            defaults["title"],
            defaults["content"],
            defaults["url"],
            defaults["timestamp"],
            defaults["summary"],
            defaults["content_markdown"],
        ),
    )
    conn.commit()
    conn.close()


class OpsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "data" / "skim.db"
        init_db(self.db)
        _insert(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_doctor_json_reports_db_platform_and_sessions(self):
        sessions = self.db.parent / "sessions"
        sessions.mkdir()
        (sessions / "reddit_session.json").write_text("{}", encoding="utf-8")

        result = self.runner.invoke(
            app, ["doctor", "--db", str(self.db), "--emit", "json"]
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["db_exists"])
        self.assertEqual(payload["platforms"][0]["platform"], "hackernews")
        reddit = next(s for s in payload["sessions"] if s["platform"] == "reddit")
        self.assertTrue(reddit["exists"])
        self.assertTrue(reddit["path"].endswith("data/sessions/reddit_session.json"))

    def test_doctor_reports_recent_rows_missing_canonical_body(self):
        # summary가 있어도 content_markdown이 비면 데이터 계약 위반이다.
        # 기존 missing_text는 summary 폴백까지 세느라 이걸 못 잡았다.
        _insert(self.db, external_id="hn-2", summary="요약만 있음", content_markdown="")
        _insert(self.db, external_id="hn-3", content_markdown="# 본문 있음")

        result = self.runner.invoke(
            app, ["doctor", "--db", str(self.db), "--emit", "json"]
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        thin = next(r for r in payload["recent_thin"] if r["platform"] == "hackernews")
        self.assertEqual(thin["thin"], 2)
        self.assertEqual(thin["total"], 3)

    def test_doctor_reports_extractor_availability(self):
        result = self.runner.invoke(
            app, ["doctor", "--db", str(self.db), "--emit", "json"]
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("ok", payload["extractor"])
        self.assertTrue(payload["extractor"]["detail"])
        if not payload["extractor"]["ok"]:
            self.assertTrue(
                any(w.startswith("playwright unavailable") for w in payload["warnings"])
            )

    def test_doctor_missing_db_does_not_create_file(self):
        missing = self.root / "data" / "missing.db"

        result = self.runner.invoke(
            app, ["doctor", "--db", str(missing), "--emit", "json"]
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["db_exists"])
        self.assertFalse(missing.exists())

    def test_coverage_json_reports_text_counts(self):
        result = self.runner.invoke(
            app, ["coverage", "--db", str(self.db), "--emit", "json"]
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["coverage"][0]["platform"], "hackernews")
        self.assertEqual(payload["coverage"][0]["with_text"], 1)

    def test_bundle_writes_handoff_files(self):
        out = self.root / "bundle"

        result = self.runner.invoke(
            app,
            [
                "bundle",
                "nvidia",
                "--db",
                str(self.db),
                "--days",
                "30",
                "--output-dir",
                str(out),
            ],
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn(f"bundle: {out}", result.stdout)
        self.assertTrue((out / "source-inventory.tsv").exists())
        self.assertTrue((out / "results.json").exists())
        self.assertTrue((out / "summary.md").exists())
        self.assertTrue((out / "proof.txt").exists())
        payload = json.loads((out / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["stats"]["total"], 1)

    def test_refresh_plan_reports_missing_session_before_crawl(self):
        result = self.runner.invoke(
            app,
            [
                "refresh-plan",
                "--db",
                str(self.db),
                "--platform",
                "reddit",
                "--emit",
                "json",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["stale_platforms"], ["reddit"])
        self.assertEqual(payload["missing_sessions"], ["reddit"])
        self.assertEqual(payload["commands"], ["uv run skim login reddit"])

    def _seed_extraction_regression(self) -> None:
        """평소 본문이 차던 소스가 최근 전부 비기 시작한 상태."""
        for i in range(20):
            _insert(
                self.db,
                external_id=f"base-{i}",
                timestamp=BASELINE_TIMESTAMP,
                content_markdown="# 본문",
            )
        for i in range(10):
            _insert(self.db, external_id=f"new-{i}", content_markdown="")

    def test_doctor_reports_per_source_extraction_regression(self):
        # 소스별 회귀는 플랫폼 합계에 묻힌다. producthunt가 3개월간 그랬다.
        self._seed_extraction_regression()

        result = self.runner.invoke(
            app, ["doctor", "--db", str(self.db), "--emit", "json"]
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [h["kind"] for h in payload["source_health"]], ["body_rate_drop"]
        )
        self.assertTrue(any("본문 보유율" in w for w in payload["warnings"]))

    def test_doctor_platform_filter_narrows_source_health(self):
        self._seed_extraction_regression()

        result = self.runner.invoke(
            app,
            ["doctor", "--db", str(self.db), "--platform", "reddit", "--emit", "json"],
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["source_health"], [])


if __name__ == "__main__":
    unittest.main()
