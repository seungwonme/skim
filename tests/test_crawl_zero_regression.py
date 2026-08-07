"""0건 수집 회귀 감지와 소스별 최소 조회 기간 회귀 테스트."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import typer

import skim_cli.cli as main
from skim_core.db import init_db, platforms_with_recent_posts


def _stamp(days_ago: float) -> str:
    """`datetime('now', ...)`와 같은 UTC 축의 타임스탬프."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


class PlatformsWithRecentPostsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "skim.db"
        init_db(self.db_path)

    def _insert(self, platform, crawled_at):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO posts (platform, author, content, crawled_at)
               VALUES (?, 'tester', 'body', ?)""",
            (platform, crawled_at),
        )
        conn.commit()
        conn.close()

    def test_window_boundary_is_the_requested_day_count(self):
        # 고정 날짜를 쓰면 창이 지나갈 때 이유 없이 빨개진다. 항상 지금 기준으로 잡는다.
        self._insert("threads", _stamp(13))
        self._insert("everyto", _stamp(15))

        recent = platforms_with_recent_posts(14, self.db_path)

        self.assertIn("threads", recent)
        self.assertNotIn("everyto", recent)

    def test_broken_database_disables_detection_instead_of_raising(self):
        empty_db = Path(self.temp_dir.name) / "no-schema.db"
        sqlite3.connect(empty_db).close()

        self.assertEqual(platforms_with_recent_posts(14, empty_db), set())


class ZeroResultRegressionTests(unittest.TestCase):
    """빈 리스트는 예외가 아니라서 크롤러가 깨져도 정상 종료처럼 보인다."""

    def _run_crawl(self, recent_platforms, platforms=("threads",), crawler_results=None):
        with (
            patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
            patch(
                "skim_cli.cli.platforms_with_recent_posts",
                return_value=set(recent_platforms),
            ),
            patch("skim_cli.cli.save_posts", return_value=1),
            patch("skim_cli.cli.save_posts_to_file"),
            patch("skim_cli.cli.save_run", return_value=7),
            patch("skim_cli.cli.init_db"),
            patch("skim_cli.cli.update_run_progress"),
            patch("skim_cli.cli.finish_run") as finish_run,
            patch("skim_cli.cli.typer.echo") as echo,
        ):
            if crawler_results is None:
                crawler.return_value = []
            else:
                crawler.side_effect = crawler_results
            try:
                main.crawl(
                    platforms=list(platforms),
                    count=None,
                    days=None,
                    output=None,
                    debug=False,
                    no_content=True,
                    user_id=None,
                )
                exited = False
            except typer.Exit:
                exited = True
        messages = " ".join(str(call.args[0]) for call in echo.call_args_list if call.args)
        return finish_run, messages, exited

    def test_warns_and_marks_run_degraded_when_active_platform_returns_nothing(self):
        finish_run, messages, exited = self._run_crawl({"threads", "reddit"})

        self.assertIn("0건", messages)
        self.assertIn("threads", messages)
        self.assertFalse(exited, "0건이 정상인 날도 있어 파이프라인은 세우지 않는다")
        status, _, summary = finish_run.call_args.args[1:4]
        # success로 두면 runs 테이블과 doctor가 회귀를 구분할 수 없다.
        self.assertEqual(status, "degraded")
        self.assertIn("0건 회귀: threads", summary)

    def test_stays_quiet_for_platform_without_recent_history(self):
        # 원래 저빈도인 소스까지 회귀로 잡으면 경고가 무의미해진다.
        finish_run, messages, _ = self._run_crawl({"reddit"})

        self.assertNotIn("0건 회귀", messages)
        self.assertEqual(finish_run.call_args.args[1], "success")
        self.assertEqual(finish_run.call_args.args[3], "전체 플랫폼 처리 완료")

    def test_failed_platform_and_regression_are_reported_together(self):
        finish_run, _, exited = self._run_crawl(
            {"reddit"},
            platforms=("threads", "reddit"),
            crawler_results=[RuntimeError("boom"), []],
        )

        self.assertTrue(exited, "크롤러 예외는 여전히 비정상 종료로 남는다")
        status, _, summary = finish_run.call_args.args[1:4]
        self.assertEqual(status, "failed")
        self.assertIn("실패 플랫폼: threads", summary)
        self.assertIn("0건 회귀: reddit", summary)

    def test_detection_failure_does_not_lose_the_crawl_result(self):
        with (
            patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
            patch(
                "skim_cli.cli.platforms_with_recent_posts",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            patch("skim_cli.cli.save_run", return_value=7),
            patch("skim_cli.cli.init_db"),
            patch("skim_cli.cli.update_run_progress"),
            patch("skim_cli.cli.finish_run") as finish_run,
            patch("skim_cli.cli.typer.echo"),
        ):
            crawler.return_value = []
            with self.assertRaises(sqlite3.OperationalError):
                main.crawl(
                    platforms=["threads"],
                    count=None,
                    days=None,
                    output=None,
                    debug=False,
                    no_content=True,
                    user_id=None,
                )

        # 판정이 터지면 finish_run이 불리지 않아 run이 running으로 남는다.
        # db 계층이 예외를 삼키므로 실제 경로에서는 이 상황이 나오지 않는다.
        self.assertEqual(finish_run.call_count, 0)


class LookbackWindowTests(unittest.TestCase):
    def _forwarded_since_days(self, platform, days=None):
        with (
            patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
            patch("skim_cli.cli.platforms_with_recent_posts", return_value=set()),
            patch("skim_cli.cli.save_run", return_value=1),
            patch("skim_cli.cli.init_db"),
            patch("skim_cli.cli.update_run_progress"),
            patch("skim_cli.cli.finish_run"),
            patch("skim_cli.cli.typer.echo"),
        ):
            crawler.return_value = []
            main.crawl(
                platforms=[platform],
                count=None,
                days=days,
                output=None,
                debug=False,
                no_content=True,
                user_id=None,
            )
            options = crawler.await_args.args[1]
        now = main.datetime.now(main.KST)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (midnight - options["since"]).days

    def test_huggingface_looks_further_back_than_one_day(self):
        # daily papers가 싣는 publishedAt은 arXiv 발행일이라 1일 창에서는 전량 걸러진다.
        self.assertEqual(self._forwarded_since_days("huggingface"), 3)

    def test_explicit_days_cannot_shrink_below_the_platform_floor(self):
        # 일일 배치가 `crawl all --days 1`로 돌기 때문에 이 경로가 실제 운영 경로다.
        self.assertEqual(self._forwarded_since_days("huggingface", days=1), 3)

    def test_explicit_days_still_widens_the_window(self):
        self.assertEqual(self._forwarded_since_days("huggingface", days=7), 7)

    def test_other_feeds_keep_the_one_day_default(self):
        self.assertEqual(self._forwarded_since_days("geeknews"), 1)

    def test_explicit_days_wins_for_platforms_without_a_floor(self):
        self.assertEqual(self._forwarded_since_days("geeknews", days=5), 5)


if __name__ == "__main__":
    unittest.main()
