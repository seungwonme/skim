"""Operational CLI commands for agent-facing Skim workflows."""

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from skim_cli.cli import app
from skim_core.crawlers.feed import geeknews
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
        "extra": None,
    }
    defaults.update(kw)
    conn = get_connection(db)
    conn.execute(
        """INSERT INTO posts
           (platform, external_id, author, title, content, url, timestamp, summary,
            content_markdown, extra)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            defaults["extra"],
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
        # doctor는 news.hada.io 토픽 경로로 UA 차단을 확인한다. 테스트마다 그 경로를
        # 두드리면 로컬 (IP, UA)의 요청 예산을 깎는다 (#29). probe 자체는
        # test_geeknews_user_agent.py가 본다.
        probe = patch("skim_cli.cli.probe_user_agent", return_value=None)
        probe.start()
        self.addCleanup(probe.stop)

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

    def _insert_geeknews(self, count, start=0, **kw):
        for i in range(start, start + count):
            _insert(
                self.db,
                platform="geeknews",
                external_id=f"topic?id={i}",
                url=f"https://news.hada.io/topic?id={i}",
                summary="RSS 요약 조각이다...",
                **kw,
            )

    def test_doctor_warns_when_bodies_are_only_feed_fragments(self):
        # 조각뿐인 본문은 비어 있지 않아서 missing body에 안 잡힌다. GeekNews 9월
        # 저장분 89%가 이 상태였는데 doctor에는 2/268로만 보였다 (#29).
        self._insert_geeknews(
            6,
            content_markdown="RSS 요약 조각이다...",
            extra=json.dumps({"enrichment_method": "failed"}),
        )
        self._insert_geeknews(
            2,
            start=6,
            content_markdown="RSS 요약 조각이다...\n\n---\n\n## Original Article\n\n원문",
            extra=json.dumps({"content_status": "partial"}),
        )
        self._insert_geeknews(
            4,
            start=8,
            content_markdown="- GN 요약\n\n---\n\n## Original Article\n\n원문",
            extra=json.dumps({"enrichment_method": "defuddle", "geeknews_topic": "ok"}),
        )

        result = self.runner.invoke(
            app, ["doctor", "--db", str(self.db), "--emit", "json"]
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        payload = json.loads(result.stdout)
        row = next(r for r in payload["recent_thin"] if r["platform"] == "geeknews")
        self.assertEqual(
            (row["fragment"], row["partial"], row["thin"], row["total"]), (6, 2, 0, 12)
        )
        # 경고는 조각뿐인 본문만 센다. 원문이 붙은 partial은 보여만 준다.
        self.assertTrue(
            any(w.startswith("geeknews:") and "6/12" in w for w in payload["warnings"]),
            payload["warnings"],
        )

    def test_doctor_does_not_warn_on_partial_bodies_with_originals(self):
        # 토픽 요청 한도가 하루 글 수보다 작아 GN 요약이 빠진 partial은 매일 생긴다.
        # 원문은 있으므로 경고하면 매일 울리는 잡음이 된다.
        self._insert_geeknews(
            12,
            content_markdown="RSS 요약 조각이다...\n\n---\n\n## Original Article\n\n원문",
            extra=json.dumps({"content_status": "partial", "enrichment_method": "defuddle"}),
        )

        result = self.runner.invoke(
            app, ["doctor", "--db", str(self.db), "--emit", "json"]
        )

        payload = json.loads(result.stdout)
        row = next(r for r in payload["recent_thin"] if r["platform"] == "geeknews")
        self.assertEqual((row["fragment"], row["partial"]), (0, 12))
        self.assertFalse([w for w in payload["warnings"] if w.startswith("geeknews:")])

    def test_doctor_tolerates_a_few_fragment_rows(self):
        # 정상 주에도 GeekNews partial은 0~5% 나온다. 그 수준에서는 경고하지 않는다.
        self._insert_geeknews(
            1,
            content_markdown="RSS 요약 조각이다...",
            extra=json.dumps({"enrichment_method": "failed"}),
        )
        self._insert_geeknews(
            19,
            start=1,
            content_markdown="- GN 요약",
            extra="",
        )

        result = self.runner.invoke(
            app, ["doctor", "--db", str(self.db), "--emit", "json"]
        )

        payload = json.loads(result.stdout)
        row = next(r for r in payload["recent_thin"] if r["platform"] == "geeknews")
        self.assertEqual((row["fragment"], row["total"]), (1, 20))
        self.assertFalse([w for w in payload["warnings"] if w.startswith("geeknews:")])

    def test_doctor_reports_a_recorded_topic_block_without_probing(self):
        # 차단된 날은 원문만 붙은 partial이라 본문 경고에 안 잡힌다. 크롤이 적어 둔
        # 차단을 보고하고, 막힌 뒤의 요청은 차단을 연장하므로 probe는 보내지 않는다.
        geeknews.TOPIC_BUDGET_FILE.write_text(
            json.dumps({"requests": [], "blocked_at": time.time() - 60}),
            encoding="utf-8",
        )
        with patch("skim_cli.cli.probe_user_agent") as probe:
            result = self.runner.invoke(
                app, ["doctor", "--db", str(self.db), "--emit", "json"]
            )

        probe.assert_not_called()
        payload = json.loads(result.stdout)
        self.assertTrue(
            any(w.startswith("geeknews: 토픽 페이지가") for w in payload["warnings"]),
            payload["warnings"],
        )

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
