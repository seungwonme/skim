"""`crawl` 저장 경로 회귀 테스트."""

import inspect
import unittest
from unittest.mock import AsyncMock, patch

import skim_cli.cli as main
from skim_core.models import Post


class CrawlPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.sample_posts = [
            Post(
                platform="threads",
                author="tester",
                content="stored content",
                timestamp="2026-04-08T00:00:00+09:00",
            )
        ]

    def test_crawl_signature_does_not_expose_dry_run(self):
        self.assertNotIn("dry_run", inspect.signature(main.crawl).parameters)

    def test_crawl_signature_exposes_reddit_subreddit_and_sort(self):
        params = inspect.signature(main.crawl).parameters
        self.assertIn("subreddit", params)
        self.assertIn("sort", params)

    @patch("skim_cli.cli.typer.echo")
    @patch("skim_cli.cli.SheetsExporter")
    @patch("skim_cli.cli.save_posts_to_file")
    @patch("skim_cli.cli.finish_run")
    @patch("skim_cli.cli.update_run_progress")
    @patch("skim_cli.cli.save_posts")
    @patch("skim_cli.cli.save_run", return_value=99)
    @patch("skim_cli.cli.init_db")
    @patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock)
    def test_crawl_always_persists_and_does_not_forward_removed_dry_run_option(
        self,
        run_single_crawler,
        init_db_mock,
        save_run_mock,
        save_posts,
        update_run_progress_mock,
        finish_run_mock,
        save_posts_to_file_mock,
        sheets_exporter,
        typer_echo,
    ):
        run_single_crawler.return_value = self.sample_posts
        save_posts.return_value = 1

        main.crawl(
            platforms=["threads"],
            count=1,
            days=None,
            output=None,
            debug=False,
            sheets=True,
            no_content=True,
            user_id=None,
        )

        run_single_crawler.assert_awaited_once()
        forwarded_options = run_single_crawler.await_args.args[1]
        self.assertNotIn("dry_run", forwarded_options)

        init_db_mock.assert_called_once()
        save_run_mock.assert_called_once_with()
        save_posts.assert_called_once_with(self.sample_posts, "threads")
        update_run_progress_mock.assert_any_call(99, "threads", "threads 크롤링 시작")
        update_run_progress_mock.assert_any_call(99, "threads", "threads 처리 완료: 1개 DB 반영")
        finish_run_mock.assert_called_once_with(99, "success", 1, "전체 플랫폼 처리 완료")
        save_posts_to_file_mock.assert_called_once()
        sheets_exporter.assert_called_once()
        self.assertTrue(
            any(
                "완료: 총 1개 저장 (run #99)" in call.args[0]
                for call in typer_echo.call_args_list
                if call.args
            ),
        )

    @patch("skim_cli.cli.typer.echo")
    @patch("skim_cli.cli.save_posts_to_file")
    @patch("skim_cli.cli.finish_run")
    @patch("skim_cli.cli.update_run_progress")
    @patch("skim_cli.cli.save_posts")
    @patch("skim_cli.cli.save_run", return_value=100)
    @patch("skim_cli.cli.init_db")
    @patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock)
    def test_crawl_forwards_reddit_subreddit_and_sort_options(
        self,
        run_single_crawler,
        init_db_mock,
        save_run_mock,
        save_posts,
        update_run_progress_mock,
        finish_run_mock,
        save_posts_to_file_mock,
        typer_echo_mock,
    ):
        run_single_crawler.return_value = self.sample_posts
        save_posts.return_value = 1

        main.crawl(
            platforms=["reddit"],
            count=3,
            days=None,
            output=None,
            debug=False,
            sheets=False,
            no_content=True,
            user_id=None,
            subreddit="python",
            sort="new",
        )

        forwarded_options = run_single_crawler.await_args.args[1]
        self.assertEqual(forwarded_options["subreddit"], "python")
        self.assertEqual(forwarded_options["sort"], "new")
        init_db_mock.assert_called_once()
        save_run_mock.assert_called_once_with()
        save_posts.assert_called_once_with(self.sample_posts, "reddit")
        update_run_progress_mock.assert_any_call(100, "reddit", "reddit 크롤링 시작")
        update_run_progress_mock.assert_any_call(100, "reddit", "reddit 처리 완료: 1개 DB 반영")
        finish_run_mock.assert_called_once_with(100, "success", 1, "전체 플랫폼 처리 완료")
        save_posts_to_file_mock.assert_called_once()
        self.assertTrue(
            any(
                "완료: 총 1개 저장 (run #100)" in call.args[0]
                for call in typer_echo_mock.call_args_list
                if call.args
            ),
        )


if __name__ == "__main__":
    unittest.main()
