"""0건 수집 회귀 감지와 소스별 최소 조회 기간 회귀 테스트."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import typer

import skim_cli.cli as main
from skim_core import db
from skim_core.db import (
    PostCadence,
    init_db,
    platform_post_cadence,
    platforms_with_recent_posts,
)

NOW = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    """posts.timestamp 형식(ISO 8601, UTC 오프셋)."""
    return (NOW - timedelta(days=days_ago)).isoformat()


def _stamp(days_ago: float) -> str:
    """`datetime('now', ...)`와 같은 UTC 축의 타임스탬프."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


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


class PlatformPostCadenceTests(unittest.TestCase):
    """0건 판정의 근거가 되는 게시 간격. 단위는 일이고 게시 시각 기준이다."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "skim.db"
        init_db(self.db_path)
        self.serial = 0

    def _insert(self, platform, *days_ago):
        conn = sqlite3.connect(self.db_path)
        for ago in days_ago:
            self.serial += 1
            conn.execute(
                """INSERT INTO posts (platform, external_id, author, content, timestamp)
                   VALUES (?, ?, 'tester', 'body', ?)""",
                (platform, f"id-{self.serial}", _iso(ago)),
            )
        conn.commit()
        conn.close()

    def _cadence(self):
        return platform_post_cadence(60, now=NOW, db_path=self.db_path)

    def test_gaps_and_silence_come_from_publish_times(self):
        self._insert("everyto", 10, 7, 2, 1.5)

        stats = self._cadence()["everyto"]

        self.assertAlmostEqual(stats.silent_days, 1.5)
        self.assertAlmostEqual(stats.max_gap_days, 5)
        # 하루 이하인 간격(0.5일)은 빼고, 창 길이와 비교하도록 값을 긴 것부터 준다.
        self.assertEqual([round(gap, 6) for gap in stats.long_gaps], [5, 3])

    def test_rows_outside_the_window_and_future_rows_are_ignored(self):
        # 시간대가 어긋나 미래로 찍힌 행이 마지막 글이 되면 침묵을 못 잰다.
        self._insert("blogs", 90, 3, 1, -0.5)

        stats = self._cadence()["blogs"]

        self.assertAlmostEqual(stats.silent_days, 1)
        self.assertAlmostEqual(stats.max_gap_days, 2)

    def test_platform_without_a_gap_is_left_out(self):
        self._insert("ailabs", 3)

        self.assertNotIn("ailabs", self._cadence())

    def test_broken_database_disables_detection_instead_of_raising(self):
        empty_db = Path(self.temp_dir.name) / "no-schema.db"
        sqlite3.connect(empty_db).close()

        self.assertEqual(platform_post_cadence(60, db_path=empty_db), {})


class ZeroResultRegressionTests(unittest.TestCase):
    """빈 리스트는 예외가 아니라서 크롤러가 깨져도 정상 종료처럼 보인다."""

    ACTIVE = PostCadence(silent_days=1.0, max_gap_days=0.5, long_gaps=())
    # 운영 DB의 everyto 공백(2026-10-01). 데일리 창은 실행 시각에 따라 1~2일이라
    # 이 테스트들의 공백은 전부 2일을 넘겨 잡는다.
    EVERYTO_GAPS = (5.13, 3.8, 3.7, 3.4, 3.3, 3.3, 3.2, 2.8)

    def _run_crawl(
        self, cadence, platforms=("threads",), crawler_results=None, days=None
    ):
        with (
            patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
            patch("skim_cli.cli.platform_post_cadence", return_value=dict(cadence)),
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
                    days=days,
                    output=None,
                    debug=False,
                    no_content=True,
                    user_id=None,
                )
                exited = False
            except typer.Exit:
                exited = True
        messages = " ".join(
            str(call.args[0]) for call in echo.call_args_list if call.args
        )
        return finish_run, messages, exited

    def test_warns_and_marks_run_degraded_when_active_platform_returns_nothing(self):
        finish_run, messages, exited = self._run_crawl(
            {"threads": self.ACTIVE, "reddit": self.ACTIVE}
        )

        self.assertIn("0건", messages)
        self.assertIn("threads", messages)
        self.assertFalse(exited, "0건이 정상인 날도 있어 파이프라인은 세우지 않는다")
        status, _, summary = finish_run.call_args.args[1:4]
        # success로 두면 runs 테이블과 doctor가 회귀를 구분할 수 없다.
        self.assertEqual(status, "degraded")
        self.assertIn("0건 회귀: threads", summary)

    def test_stays_quiet_for_platform_without_recent_history(self):
        # 게시 이력이 모자라 간격을 못 재는 플랫폼은 판정하지 않는다.
        finish_run, messages, _ = self._run_crawl({"reddit": self.ACTIVE})

        self.assertNotIn("0건 회귀", messages)
        self.assertEqual(finish_run.call_args.args[1], "success")
        self.assertEqual(finish_run.call_args.args[3], "전체 플랫폼 처리 완료")

    def test_sparse_source_on_a_quiet_day_is_not_a_regression(self):
        # everyto는 2~4일에 한 편이라 하루 0건이 정상이다. 이걸 회귀로 잡아 9/26~30
        # 회차가 전부 degraded였고, 그 속에 arxiv 장애가 묻혔다 (#47).
        quiet = PostCadence(
            silent_days=2.1, max_gap_days=5.13, long_gaps=self.EVERYTO_GAPS
        )

        finish_run, messages, _ = self._run_crawl(
            {"everyto": quiet}, platforms=("everyto",)
        )

        self.assertEqual(finish_run.call_args.args[1], "success")
        self.assertIn("정상 공백", messages)
        self.assertIn("everyto", messages)

    def test_sparse_source_silent_longer_than_ever_is_a_regression(self):
        silent = PostCadence(
            silent_days=6.0, max_gap_days=5.13, long_gaps=self.EVERYTO_GAPS
        )

        finish_run, _, _ = self._run_crawl({"everyto": silent}, platforms=("everyto",))

        self.assertEqual(finish_run.call_args.args[1], "degraded")
        self.assertIn("0건 회귀: everyto", finish_run.call_args.args[3])

    def test_daily_source_is_a_regression_despite_missed_run_gaps(self):
        # 매일 들어오는 소스도 회차를 놓친 날의 1일짜리 공백이 남는다. 침묵이 그보다
        # 짧아도 0건이면 고장이다.
        daily = PostCadence(silent_days=0.9, max_gap_days=1.01, long_gaps=(1.01, 1.01))

        finish_run, _, _ = self._run_crawl(
            {"hackernews": daily}, platforms=("hackernews",)
        )

        self.assertEqual(finish_run.call_args.args[1], "degraded")

    def test_more_long_gaps_than_missed_runs_explain_is_a_sparse_source(self):
        sparse = PostCadence(
            silent_days=0.9,
            max_gap_days=2.5,
            long_gaps=(2.5,) * (main.MISSED_RUN_ALLOWANCE + 1),
        )

        finish_run, _, _ = self._run_crawl({"ailabs": sparse}, platforms=("ailabs",))

        self.assertEqual(finish_run.call_args.args[1], "success")

    def test_gaps_shorter_than_the_window_do_not_make_a_source_sparse(self):
        # producthunt는 7일 창으로 받는다. 하루 넘는 공백은 잦아도 7일 창이 빈 적은
        # 없었다(운영 DB 2026-10-01). 하루 기준으로 세면 뜸한 소스로 분류돼 고장 나도
        # 이틀 뒤에야 잡혔다.
        weekly = PostCadence(
            silent_days=1.3,
            max_gap_days=2.8,
            long_gaps=(2.8, 2.5, 2.3, 1.9, 1.5, 1.2),
        )

        finish_run, messages, _ = self._run_crawl(
            {"producthunt": weekly}, platforms=("producthunt",)
        )

        self.assertEqual(finish_run.call_args.args[1], "degraded")
        self.assertIn("0건 회귀: producthunt", finish_run.call_args.args[3])
        self.assertIn("창보다 긴 공백 0번", messages)

    def test_a_wider_window_counts_fewer_gaps(self):
        # 같은 이력이라도 창이 넓으면(수동 `--days 7`, `--catch-up`) 그 창보다 긴
        # 공백이 줄어든다. 7일이 빈 적 없는 소스가 7일 창에서 0건이면 고장이다.
        finish_run, _, _ = self._run_crawl(
            {"everyto": PostCadence(2.1, 5.13, self.EVERYTO_GAPS)},
            platforms=("everyto",),
            days=7,
        )

        self.assertEqual(finish_run.call_args.args[1], "degraded")

    def test_failed_platform_and_regression_are_reported_together(self):
        finish_run, _, exited = self._run_crawl(
            {"reddit": self.ACTIVE},
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
                "skim_cli.cli.platform_post_cadence",
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


class ZeroResultReplayTests(unittest.TestCase):
    """실제 스키마의 DB에 운영과 같은 모양의 이력을 넣고 판정을 끝까지 돌린다."""

    def _insert(self, platform, days_ago):
        db.init_db()
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(db.DB_PATH)
        for i, ago in enumerate(days_ago):
            conn.execute(
                """INSERT INTO posts (platform, external_id, author, content, timestamp)
                   VALUES (?, ?, 'a', 'c', ?)""",
                (platform, f"{platform}-{i}", (now - timedelta(days=ago)).isoformat()),
            )
        conn.commit()
        conn.close()

    def test_daily_source_is_flagged_and_sparse_source_is_not(self):
        # hackernews: 1시간마다 들어오되 회차를 한 번 놓쳐 하루가 비었다. 마지막 글이
        # 하루 전이라 최장 공백(약 1일)만 보면 침묵이 그보다 짧아 고장을 놓친다.
        hourly = [hour / 24 for hour in range(24, 40 * 24)]
        self._insert("hackernews", [ago for ago in hourly if not 10 <= ago < 11])
        # everyto: 3일에 한 편.
        self._insert("everyto", [1 + 3 * k for k in range(13)])

        with (
            patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
            patch("skim_cli.cli.save_run", return_value=7),
            patch("skim_cli.cli.update_run_progress"),
            patch("skim_cli.cli.finish_run") as finish_run,
            patch("skim_cli.cli.typer.echo"),
        ):
            crawler.return_value = []
            main.crawl(
                platforms=["hackernews", "everyto"],
                count=None,
                days=None,
                output=None,
                debug=False,
                no_content=True,
                user_id=None,
            )

        status, _, summary = finish_run.call_args.args[1:4]
        self.assertEqual(status, "degraded")
        self.assertIn("0건 회귀: hackernews", summary)
        self.assertNotIn("everyto", summary)


class LookbackWindowTests(unittest.TestCase):
    def _forwarded_since_days(self, platform, days=None, now=None):
        frozen = now or main.datetime.now(main.KST)
        with (
            patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
            patch("skim_cli.cli.platform_post_cadence", return_value={}),
            patch("skim_cli.cli.save_run", return_value=1),
            patch("skim_cli.cli.init_db"),
            patch("skim_cli.cli.update_run_progress"),
            patch("skim_cli.cli.finish_run"),
            patch("skim_cli.cli.typer.echo"),
            patch("skim_cli.cli.datetime") as mock_datetime,
        ):
            mock_datetime.now.return_value = frozen
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
        midnight = frozen.replace(hour=0, minute=0, second=0, microsecond=0)
        return (midnight - options["since"]).days

    def test_huggingface_looks_further_back_than_one_day(self):
        # HF는 주말에 daily papers를 큐레이션하지 않는다. 월요일 배치가 금요일 목록을
        # 놓치지 않으려면 3일이 필요하다.
        self.assertEqual(self._forwarded_since_days("huggingface"), 3)

    def test_producthunt_window_covers_late_feed_arrivals(self):
        # PH 피드는 갱신순 50건이라 런칭 며칠 뒤에야 올라오는 항목이 많다. 1일 창은
        # 최근 7일 런칭 37건 중 3건만 받았다 (2026-09-22 실측).
        self.assertEqual(self._forwarded_since_days("producthunt", days=1), 7)

    def test_arxiv_tuesday_morning_reaches_friday_mailing(self):
        # 데일리는 00:02 KST다. 화 00:02는 월요일 메일링(화 09:00 KST) 전이고,
        # 마지막 메일링은 금요일분이다. 2일 창은 그 발행일을 잘라 0건이 된다.
        # 2026-09-01 00:06 회차가 그렇게 비었다.
        tuesday = datetime(2026, 9, 1, 0, 30, tzinfo=main.KST)
        self.assertEqual(tuesday.weekday(), 1)
        self.assertEqual(main.min_lookback_days("arxiv", tuesday), 4)

    def test_arxiv_wednesday_keeps_the_two_day_floor(self):
        wednesday = datetime(2026, 9, 2, 0, 30, tzinfo=main.KST)
        self.assertEqual(wednesday.weekday(), 2)
        self.assertEqual(main.min_lookback_days("arxiv", wednesday), 2)

    def test_arxiv_floor_survives_an_explicit_narrow_window(self):
        # arXiv는 주말에 announce하지 않는다. 요일 규칙이 days=None일 때만 걸려 있어
        # 일일 배치의 `--days 1`이 그걸 덮어썼고, 그래서 배치에서만 0건이 났다.
        tuesday = datetime(2026, 9, 1, 0, 30, tzinfo=main.KST)
        self.assertEqual(self._forwarded_since_days("arxiv", days=1, now=tuesday), 4)
        self.assertEqual(self._forwarded_since_days("arxiv", now=tuesday), 4)

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
