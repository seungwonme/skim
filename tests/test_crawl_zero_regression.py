"""0건 수집 회귀 감지와 소스별 기본 조회 기간 회귀 테스트."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import skim_cli.cli as main
from skim_core.db import init_db, platforms_with_recent_posts


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

    def test_returns_only_platforms_inside_window(self):
        self._insert("threads", "2026-08-07 00:00:00")
        self._insert("everyto", "2020-01-01 00:00:00")

        recent = platforms_with_recent_posts(14, self.db_path)

        self.assertIn("threads", recent)
        self.assertNotIn("everyto", recent)


class ZeroResultRegressionTests(unittest.TestCase):
    """빈 리스트는 예외가 아니라서 크롤러가 깨져도 정상 종료처럼 보인다."""

    def _run_crawl(self, recent_platforms):
        with (
            patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
            patch(
                "skim_cli.cli.platforms_with_recent_posts",
                return_value=recent_platforms,
            ),
            patch("skim_cli.cli.save_run", return_value=7),
            patch("skim_cli.cli.init_db"),
            patch("skim_cli.cli.update_run_progress"),
            patch("skim_cli.cli.finish_run") as finish_run,
            patch("skim_cli.cli.typer.echo") as echo,
        ):
            crawler.return_value = []
            main.crawl(
                platforms=["threads"],
                count=None,
                days=None,
                output=None,
                debug=False,
                no_content=True,
                user_id=None,
            )
        messages = " ".join(str(call.args[0]) for call in echo.call_args_list if call.args)
        return finish_run, messages

    def test_warns_when_previously_active_platform_returns_nothing(self):
        finish_run, messages = self._run_crawl({"threads", "reddit"})

        self.assertIn("0건", messages)
        self.assertIn("threads", messages)
        summary = finish_run.call_args.args[3]
        self.assertIn("0건 회귀: threads", summary)

    def test_stays_quiet_for_platform_without_recent_history(self):
        # 원래 저빈도인 소스까지 회귀로 잡으면 경고가 무의미해진다.
        finish_run, messages = self._run_crawl({"reddit"})

        self.assertNotIn("0건 회귀", messages)
        self.assertEqual(finish_run.call_args.args[3], "전체 플랫폼 처리 완료")


class DefaultLookbackTests(unittest.TestCase):
    def _forwarded_since_days(self, platform):
        with (
            patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
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
                days=None,
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

    def test_other_feeds_keep_the_one_day_default(self):
        self.assertEqual(self._forwarded_since_days("geeknews"), 1)


if __name__ == "__main__":
    unittest.main()
