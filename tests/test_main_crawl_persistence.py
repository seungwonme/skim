"""`crawl` 저장 경로 회귀 테스트."""

import inspect
import unittest
from unittest.mock import AsyncMock, patch

import main
from src.models import Post


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

    @patch("main.typer.echo")
    @patch("main.SheetsExporter")
    @patch("main.save_posts_to_file")
    @patch("main.finish_run")
    @patch("main.save_posts")
    @patch("main.save_run", return_value=99)
    @patch("main.init_db")
    @patch("main.run_single_crawler", new_callable=AsyncMock)
    def test_crawl_always_persists_and_does_not_forward_removed_dry_run_option(
        self,
        run_single_crawler,
        init_db_mock,
        save_run_mock,
        save_posts,
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
        finish_run_mock.assert_called_once_with(99, "success", 1)
        save_posts_to_file_mock.assert_called_once()
        sheets_exporter.assert_called_once()
        self.assertTrue(
            any(
                "완료: 총 1개 저장 (run #99)" in call.args[0]
                for call in typer_echo.call_args_list
                if call.args
            ),
        )

    @patch("main.typer.echo")
    @patch("main.save_posts_to_file")
    @patch("main.finish_run")
    @patch("main.save_posts")
    @patch("main.save_run", return_value=100)
    @patch("main.init_db")
    @patch("main.run_single_crawler", new_callable=AsyncMock)
    def test_crawl_forwards_reddit_subreddit_and_sort_options(
        self,
        run_single_crawler,
        init_db_mock,
        save_run_mock,
        save_posts,
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
        finish_run_mock.assert_called_once_with(100, "success", 1)
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
