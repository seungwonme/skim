"""GeekNews 조각 행 원문 복구 검증 (#33).

토픽 페이지가 막혔던 동안 저장된 행은 본문이 RSS 요약 조각뿐이다. 목록(`/newest`)을
거슬러 읽어 원문 링크를 찾고, 크롤러와 같은 함수로 본문을 다시 만든다. 네트워크는
geeknews 모듈의 requests와 extract_original에서 막는다.
"""

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skim_core.crawlers.feed import geeknews
from skim_core.crawlers.feed.geeknews import ORIGINAL_HEADER, ListingScan
from skim_core.db import get_connection, init_db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "recover_geeknews_originals.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "recover_geeknews_originals", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recovery = _load()

ARTICLE = {
    "content_markdown": "Original article body with enough words to count",
    "word_count": 8,
}
SELF_POSTS = {"198"}


def _row_html(topic_id: int) -> str:
    """목록 한 줄. 자체 글은 제목이 토픽 경로를, 나머지는 원문을 가리킨다."""
    href = (
        f"topic?id={topic_id}"
        if str(topic_id) in SELF_POSTS
        else f"https://example.com/{topic_id}"
    )
    return (
        f'<div class="topic_row" data-topic-state-id="{topic_id}">'
        f'<div class="topictitle link"><a href="{href}"><h1>t</h1></a></div>'
        f'<div class="topicinfo"><span id="tp{topic_id}">4</span>P '
        f'<a data-topic-comment-count="2" data-topic-comment-topic-id="{topic_id}">'
        "댓글</a></div></div>"
    )


# 목록은 최신순이다. 1쪽 205~201, 2쪽 200~196, 3쪽 195~191.
PAGES = {
    page: "".join(
        _row_html(i) for i in range(205 - 5 * (page - 1), 200 - 5 * (page - 1), -1)
    )
    for page in (1, 2, 3)
}


def _resp(status=200, text=""):
    return Mock(status_code=status, text=text)


def _page_of(url: str) -> int:
    return int(url.split("page=")[1]) if "page=" in url else 1


class _DbCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "skim.db"
        init_db(self.db)
        self.conn = get_connection(self.db)
        self.addCleanup(self.conn.close)
        self.requested = []
        echo = patch.object(geeknews.typer, "echo")
        echo.start()
        self.addCleanup(echo.stop)

    def _insert(self, topic_id, body=None, extra=None, summary="RSS 요약 조각이다..."):
        cur = self.conn.execute(
            """INSERT INTO posts
               (platform, external_id, author, title, content, url, timestamp,
                summary, content_markdown, likes, comments, extra)
               VALUES ('geeknews', ?, 'a', ?, '', ?, ?, ?, ?, 0, 0, ?)""",
            (
                f"gn-{topic_id}",
                f"글 {topic_id}",
                f"https://news.hada.io/topic?id={topic_id}",
                f"2026-09-{topic_id % 28 + 1:02d}T00:00:00+00:00",
                summary,
                summary if body is None else body,
                json.dumps(extra or {"enrichment_method": "failed"}),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def _row(self, row_id):
        row = self.conn.execute(
            "SELECT content_markdown, likes, comments, extra FROM posts WHERE id = ?",
            (row_id,),
        ).fetchone()
        return row, json.loads(row["extra"])

    def _get(self, responses=None):
        responses = responses or {}

        def fake_get(url, **_kwargs):
            page = _page_of(url)
            self.requested.append(page)
            return responses.get(page) or _resp(text=PAGES.get(page, ""))

        return fake_get

    def _recover(self, responses=None, original=(ARTICLE, "defuddle", None)):
        targets = recovery.fetch_targets(self.conn)
        scan = ListingScan(start_page=1, max_pages=10, interval=0)
        with (
            patch.object(geeknews.requests, "get", side_effect=self._get(responses)),
            patch.object(geeknews, "extract_original", return_value=original),
            patch.object(geeknews.time, "sleep"),
        ):
            stats = recovery.recover(self.conn, targets, scan)
        return stats, scan


class TargetTests(_DbCase):
    def test_only_rows_whose_body_is_the_feed_fragment(self):
        fragment = self._insert(203)
        empty = self._insert(204, body="")
        # 원문까지 붙은 partial 행과 GN 요약을 받은 행은 다시 만들면 손해다.
        self._insert(
            205,
            body=f"요약\n\n---\n\n{ORIGINAL_HEADER}\n\n원문",
            extra={"enrichment_method": "defuddle", "content_status": "partial"},
        )
        self._insert(
            202,
            body="GN 요약 본문",
            extra={"enrichment_method": "failed", "geeknews_topic": "ok"},
        )
        self._insert(
            201, extra={"enrichment_method": "failed", "listing_checked": "self"}
        )
        self._insert(
            200,
            extra={"enrichment_method": "failed", "original_failures": 2},
        )

        ids = {t["id"] for t in recovery.fetch_targets(self.conn)}

        self.assertEqual(ids, {fragment, empty})


class RecoverTests(_DbCase):
    def test_listing_link_brings_the_original_and_stops_past_the_oldest_target(self):
        linked = self._insert(203)
        self_post = self._insert(198)

        stats, scan = self._recover()

        body, extra = self._row(linked)
        self.assertEqual(
            body["content_markdown"],
            f"RSS 요약 조각이다...\n\n---\n\n{ORIGINAL_HEADER}\n\n"
            f"{ARTICLE['content_markdown']}",
        )
        self.assertEqual(extra["original_url"], "https://example.com/203")
        self.assertEqual(extra["enrichment_method"], "defuddle")
        self.assertEqual(extra["content_status"], "partial")
        # 목록의 지표가 빈 지표를 채운다.
        self.assertEqual((body["likes"], body["comments"]), (4, 2))
        # 자체 글은 토픽 페이지로만 채울 수 있다.
        self.assertEqual(self._row(self_post)[1]["listing_checked"], "self")
        # 2쪽에서 가장 오래된 대상(198)보다 아래로 내려갔으니 3쪽은 읽지 않는다.
        self.assertEqual(self.requested, [1, 2])
        self.assertEqual(scan.stop, "done")
        self.assertEqual((stats["filled"], stats["self"], stats["left"]), (1, 1, 0))

    def test_known_original_needs_no_listing(self):
        row_id = self._insert(
            150,
            extra={
                "enrichment_method": "failed",
                "original_url": "https://example.com/150",
            },
        )

        self._recover()

        self.assertEqual(self.requested, [])
        self.assertEqual(self._row(row_id)[1]["enrichment_method"], "defuddle")

    def test_failed_original_keeps_the_fragment_and_gives_up_on_the_second_try(self):
        row_id = self._insert(203)
        failure = (None, "failed", "http fetch failed")

        self._recover(original=failure)
        body, extra = self._row(row_id)
        self.assertEqual(body["content_markdown"], "RSS 요약 조각이다...")
        self.assertEqual(extra["original_url"], "https://example.com/203")
        self.assertEqual(extra["original_failures"], 1)

        # 두 번째는 아는 링크로 바로 받는다. 또 실패하면 대상에서 빠진다.
        self.requested.clear()
        self._recover(original=failure)
        self.assertEqual(self.requested, [])
        self.assertEqual(self._row(row_id)[1]["original_failures"], 2)
        self.assertEqual(recovery.fetch_targets(self.conn), [])

    def test_block_stops_the_scan_and_keeps_what_was_saved(self):
        linked = self._insert(203)
        later = self._insert(198)

        stats, scan = self._recover(responses={2: _resp(status=403)})

        self.assertEqual(scan.stop, "blocked")
        self.assertEqual(self.requested, [1, 2])
        self.assertEqual(self._row(linked)[1]["enrichment_method"], "defuddle")
        # 2쪽은 못 읽었다. 그 범위의 글을 지워진 글로 보면 안 된다.
        self.assertNotIn("listing_checked", self._row(later)[1])
        self.assertEqual(stats["left"], 1)
        # 막힌 시각을 적어 크롤과 토픽 백필도 창이 지날 때까지 요청하지 않는다.
        self.assertIsNotNone(geeknews.last_topic_block())

    def test_absent_inside_a_page_is_missing_but_not_at_a_page_boundary(self):
        # 1쪽(205~201) 안의 202가 빠졌다. 지워진 글이다.
        responses = {
            1: _resp(text="".join(_row_html(i) for i in (205, 204, 203, 201))),
            # 2쪽은 199부터라 200은 쪽 경계에 걸렸다. 새 글에 밀려 빠졌을 수 있다.
            2: _resp(text="".join(_row_html(i) for i in (199, 198, 197, 196, 195))),
        }
        deleted = self._insert(202)
        boundary = self._insert(200)
        self._insert(196)

        stats, _ = self._recover(responses=responses)

        self.assertEqual(self._row(deleted)[1]["listing_checked"], "missing")
        self.assertNotIn("listing_checked", self._row(boundary)[1])
        self.assertEqual((stats["missing"], stats["left"]), (1, 1))


class ListingScanTests(unittest.TestCase):
    def setUp(self):
        echo = patch.object(geeknews.typer, "echo")
        echo.start()
        self.addCleanup(echo.stop)

    def test_recorded_block_means_no_listing_request(self):
        geeknews._spend_topic_budget(time.time(), blocked=True)  # pylint: disable=protected-access
        scan = ListingScan(start_page=1, max_pages=5)

        with patch.object(geeknews.requests, "get") as get:
            pages = list(scan.pages())

        self.assertEqual(pages, [])
        get.assert_not_called()
        self.assertEqual(scan.stop, "paused")

    def test_waits_between_pages_and_starts_where_asked(self):
        scan = ListingScan(start_page=2, max_pages=2, interval=30)
        urls = []

        def fake_get(url, **_kwargs):
            urls.append(url)
            return _resp(text=PAGES[_page_of(url)])

        with (
            patch.object(geeknews.requests, "get", side_effect=fake_get),
            patch.object(geeknews.time, "sleep") as sleep,
        ):
            pages = [page for page, _ in scan.pages()]

        self.assertEqual(pages, [2, 3])
        self.assertEqual(urls[0], "https://news.hada.io/newest?page=2")
        sleep.assert_called_once()
        self.assertLessEqual(sleep.call_args.args[0], 30)
        self.assertEqual((scan.last_page, scan.stop), (3, "pages"))

    def test_empty_page_ends_the_listing(self):
        scan = ListingScan(start_page=1, max_pages=5, interval=0)

        with (
            patch.object(
                geeknews.requests, "get", side_effect=[_resp(text=PAGES[1]), _resp()]
            ),
            patch.object(geeknews.time, "sleep"),
        ):
            pages = [page for page, _ in scan.pages()]

        self.assertEqual(pages, [1])
        self.assertEqual(scan.stop, "empty")


class MainTests(_DbCase):
    def test_dry_run_sends_nothing(self):
        row_id = self._insert(203)

        with (
            patch.object(
                recovery, "get_connection", side_effect=lambda: get_connection(self.db)
            ),
            patch.object(geeknews.requests, "get") as get,
        ):
            code = recovery.main(["--dry-run"])

        self.assertEqual(code, 0)
        get.assert_not_called()
        self.assertEqual(self._row(row_id)[1], {"enrichment_method": "failed"})


if __name__ == "__main__":
    unittest.main()
