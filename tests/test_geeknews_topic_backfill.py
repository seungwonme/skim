"""GeekNews 토픽 백필 검증 (#29).

2026-08-29 ~ 09-28 차단 기간에 GeekNews 행 1,114건이 RSS 요약 조각만 남았다.
백필은 크롤러와 같은 토픽 요청 경로(간격, 서킷브레이커)로 이 행들을 하루 100건씩
채운다. 여기서는 대상 선정, 행 갱신, 멈춤 조건을 실제 스키마의 임시 DB로 본다.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skim_core.crawlers.feed import geeknews
from skim_core.crawlers.feed.geeknews import TopicPage
from skim_core.db import get_connection, init_db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "backfill_geeknews_topics.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "backfill_geeknews_topics", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load()

FRAGMENT = "RSS 요약 조각이다..."
TOPIC = TopicPage(
    summary="- GN 요약 첫 줄\n- GN 요약 둘째 줄",
    original_url="https://example.com/original",
    likes=12,
    comments=2,
    comment_section="## GeekNews Comments\n\n- **neo**: 좋은 글",
)
ARTICLE = {
    "content_markdown": "Original article body with enough words to count",
    "description": "d",
    "image": "",
}


class _DbCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "skim.db"
        init_db(self.db)
        self.conn = get_connection(self.db)
        self.addCleanup(self.conn.close)
        geeknews.reset_topic_throttle()
        self.addCleanup(geeknews.reset_topic_throttle)

    def _insert(
        self,
        topic_id,
        body=FRAGMENT,
        extra=None,
        likes=None,
        comments=None,
        timestamp="2026-09-20T00:00:00+00:00",
    ):
        if isinstance(extra, dict):
            extra = json.dumps(extra)
        cur = self.conn.execute(
            """INSERT INTO posts
               (platform, external_id, author, title, content, url, timestamp,
                likes, comments, summary, content_markdown, extra)
               VALUES ('geeknews', ?, 'xguru', ?, '', ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"topic?id={topic_id}",
                f"글 {topic_id}",
                f"https://news.hada.io/topic?id={topic_id}",
                timestamp,
                likes,
                comments,
                FRAGMENT,
                body,
                extra,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def _extra(self, row_id):
        row = self.conn.execute(
            "SELECT extra FROM posts WHERE id = ?", (row_id,)
        ).fetchone()
        return json.loads(row["extra"] or "{}")

    def _target_ids(self):
        return [t["id"] for t in backfill.fetch_targets(self.conn)]


class TargetTests(_DbCase):
    def test_body_first_then_comments_then_metrics(self):
        failed = {"enrichment_method": "failed"}
        fragment = self._insert(1, extra=failed, timestamp="2026-09-01T00:00:00+00:00")
        partial = self._insert(
            2,
            body=f"{FRAGMENT}\n\n---\n\n## Original Article\n\n원문",
            extra={"content_status": "partial", "enrichment_method": "defuddle"},
            timestamp="2026-09-02T00:00:00+00:00",
        )
        # extra가 빈 문자열이어도 json_extract가 깨지지 않아야 한다.
        empty = self._insert(3, body="", extra="")
        no_comments = self._insert(4, body="- GN 요약", likes=5, comments=3)
        no_metrics = self._insert(5, body="- GN 요약")
        # 한 번 못 읽은 글은 최신이어도 같은 우선순위의 맨 뒤로 간다.
        retried = self._insert(
            6,
            extra={**failed, "geeknews_topic_failures": 1},
            timestamp="2026-09-28T00:00:00+00:00",
        )
        # 빠지는 행: 이미 받음, 지워진 글, 실패 상한, 채울 것이 없는 행.
        self._insert(7, extra={**failed, "geeknews_topic": "ok"})
        self._insert(8, extra={**failed, "geeknews_topic": "gone"})
        self._insert(
            9, extra={**failed, "geeknews_topic_failures": backfill.MAX_ROW_FAILURES}
        )
        self._insert(
            10,
            body="- GN 요약\n\n---\n\n## GeekNews Comments\n\n- a",
            likes=5,
            comments=1,
        )

        self.assertEqual(
            self._target_ids(),
            [empty, partial, fragment, retried, no_comments, no_metrics],
        )


class BuildUpdateTests(_DbCase):
    def _update(self, original=(ARTICLE, "defuddle", None)):
        row = backfill.fetch_targets(self.conn)[0]
        with patch.object(
            geeknews, "extract_original", return_value=original
        ) as extract:
            update = backfill.build_update(row, TOPIC)
        return update, json.loads(update["extra"]), extract

    def test_fragment_row_gets_summary_original_and_comments(self):
        self._insert(1, extra={"enrichment_method": "failed"})

        update, extra, extract = self._update()

        # 원문 링크가 extra에 없던 행이다. 토픽 페이지가 준 링크로 받는다.
        extract.assert_called_once_with("https://example.com/original", "글 1")
        body = update["content_markdown"]
        self.assertTrue(body.startswith("- GN 요약 첫 줄"))
        self.assertLess(
            body.index("## Original Article"), body.index("## GeekNews Comments")
        )
        self.assertEqual(extra["enrichment_method"], "defuddle")
        self.assertEqual(extra["original_url"], "https://example.com/original")
        self.assertEqual(extra["geeknews_topic"], "ok")
        self.assertEqual((update["likes"], update["comments"]), (12, 2))

    def test_partial_row_keeps_its_original_without_refetching(self):
        # 다시 추출하다 실패하면 크롤 때 받아 둔 원문을 잃는다.
        self._insert(
            1,
            body=f"{FRAGMENT}\n\n---\n\n## Original Article\n\n크롤 때 받아 둔 원문 본문",
            extra={
                "content_status": "partial",
                "enrichment_method": "defuddle",
                "original_url": "https://example.com/original",
            },
        )

        update, extra, extract = self._update(original=(None, "failed", "timeout"))

        extract.assert_not_called()
        body = update["content_markdown"]
        self.assertTrue(body.startswith("- GN 요약 첫 줄"))
        self.assertNotIn(FRAGMENT, body)
        self.assertIn("크롤 때 받아 둔 원문 본문", body)
        self.assertIn("## GeekNews Comments", body)
        self.assertNotIn("content_status", extra)
        self.assertEqual(extra["enrichment_method"], "defuddle")

    def test_comment_only_row_keeps_its_body_and_metrics(self):
        body = "- GN 요약\n\n---\n\n## Original Article\n\n원문"
        self._insert(1, body=body, likes=5, comments=2)

        update, extra, extract = self._update()

        extract.assert_not_called()
        self.assertTrue(update["content_markdown"].startswith(body))
        self.assertIn("## GeekNews Comments", update["content_markdown"])
        # 있던 값은 지키고 빈 값만 채운다.
        self.assertEqual((update["likes"], update["comments"]), (5, 2))
        self.assertEqual(extra, {"geeknews_topic": "ok"})


class RunTests(_DbCase):
    def _run(self, outcomes):
        with (
            patch.object(backfill, "fetch_topic", side_effect=outcomes) as fetch,
            patch.object(backfill.time, "sleep"),
            patch.object(
                geeknews, "extract_original", return_value=(ARTICLE, "defuddle", None)
            ),
        ):
            stats = backfill.run(self.conn, backfill.fetch_targets(self.conn), delay=1)
        return stats, fetch

    def _fill(self, count):
        return [
            self._insert(i, extra={"enrichment_method": "failed"})
            for i in range(1, count + 1)
        ]

    def test_filled_rows_leave_the_queue(self):
        self._fill(2)

        stats, _ = self._run([(TOPIC, "ok"), (TOPIC, "ok")])

        self.assertEqual(stats, {"filled": 2, "failed": 0, "gone": 0})
        self.assertEqual(self._target_ids(), [])

    def test_deleted_topics_leave_the_queue_without_stopping_the_run(self):
        # 지워진 글이 최신순 맨 앞에 몰려 있어도 뒤의 행까지 간다.
        self._fill(7)

        stats, fetch = self._run([(None, "gone")] * 6 + [(TOPIC, "ok")])

        self.assertEqual(fetch.call_count, 7)
        self.assertEqual(stats, {"filled": 1, "failed": 0, "gone": 6})
        self.assertEqual(self._target_ids(), [])

    def test_unreadable_topic_is_dropped_after_repeated_failures(self):
        (row_id,) = self._fill(1)

        for _ in range(backfill.MAX_ROW_FAILURES):
            self.assertEqual(self._target_ids(), [row_id])
            self._run([(None, "empty")])

        self.assertEqual(self._target_ids(), [])
        self.assertEqual(
            self._extra(row_id)["geeknews_topic_failures"], backfill.MAX_ROW_FAILURES
        )

    def test_network_errors_stop_the_run_without_blaming_rows(self):
        self._fill(8)

        stats, fetch = self._run([(None, "error")] * 8)

        self.assertEqual(fetch.call_count, backfill.MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(stats["failed"], backfill.MAX_CONSECUTIVE_FAILURES)
        # 행 탓이 아니므로 아무것도 남기지 않는다. 다음 밤에 그대로 다시 시도한다.
        self.assertEqual(len(self._target_ids()), 8)
        self.assertNotIn("geeknews_topic_failures", self._extra(1))

    def test_blocked_run_stops_at_the_first_block(self):
        # 백필은 크롤 뒤에 돈다. 크롤이 한도를 다 썼으면 첫 요청에서 알 수 있다.
        self._fill(10)

        with (
            patch.object(
                geeknews.requests, "get", return_value=Mock(status_code=403, text="")
            ) as get,
            patch.object(backfill.time, "sleep"),
            patch.object(geeknews.typer, "echo"),
        ):
            backfill.run(self.conn, backfill.fetch_targets(self.conn), delay=1)

        # 막힌 뒤에 더 두드리면 차단이 연장된다. 막힌 것도 글 탓이 아니다.
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len(self._target_ids()), 10)

    def test_no_budget_left_means_no_requests(self):
        # 크롤이 한도를 다 쓴 밤이다. 백필은 두드리지 않고 끝나야 한다.
        self._fill(3)

        with (
            patch.object(backfill, "topic_budget_left", return_value=0),
            patch.object(backfill, "fetch_topic") as fetch,
        ):
            stats = backfill.run(self.conn, backfill.fetch_targets(self.conn), delay=1)

        fetch.assert_not_called()
        self.assertEqual(stats["filled"], 0)

    def test_waits_for_the_budget_when_asked(self):
        self._fill(1)

        with (
            patch.object(backfill, "topic_budget_left", side_effect=[0, 0, 5, 5]),
            patch.object(backfill, "fetch_topic", return_value=(TOPIC, "ok")) as fetch,
            patch.object(backfill.time, "sleep") as sleep,
            patch.object(geeknews, "extract_original", return_value=(ARTICLE, "defuddle", None)),
        ):
            stats = backfill.run(
                self.conn, backfill.fetch_targets(self.conn), delay=0, wait_minutes=90
            )

        self.assertEqual(sleep.call_count, 2)
        fetch.assert_called_once()
        self.assertEqual(stats["filled"], 1)

    def test_dry_run_sends_no_requests(self):
        self._fill(3)

        with (
            patch.object(sys, "argv", ["backfill_geeknews_topics.py", "--dry-run"]),
            patch.object(
                backfill, "get_connection", return_value=get_connection(self.db)
            ),
            patch.object(backfill, "fetch_topic") as fetch,
        ):
            self.assertEqual(backfill.main(), 0)

        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
