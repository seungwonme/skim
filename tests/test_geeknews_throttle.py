"""GeekNews 토픽 요청의 간격과 서킷브레이커 검증.

news.hada.io는 (IP, UA) 단위로 요청량을 보고 막고, 막힌 뒤에도 계속 두드리면 차단이
갱신돼 풀리지 않는다. 회차마다 50건을 그대로 두드려 매일 차단을 새로 걸었던 것이
2026-08-29~09-22 댓글·지표 3주 유실의 원인이다.
"""

import unittest
from unittest.mock import Mock, patch

from skim_core.crawlers.feed import geeknews


def _resp(status=200, text="<html></html>"):
    return Mock(status_code=status, text=text)


class GeekNewsThrottleTests(unittest.TestCase):
    def setUp(self):
        geeknews.reset_topic_throttle()
        self.addCleanup(geeknews.reset_topic_throttle)

    def test_requests_are_spaced_out(self):
        with (
            patch.object(geeknews.requests, "get", return_value=_resp()),
            patch.object(geeknews.time, "sleep") as sleep,
        ):
            geeknews.fetch_geeknews_metrics("1")
            geeknews.fetch_geeknews_metrics("2")

        # 첫 요청은 기다릴 이유가 없고, 두 번째는 간격을 채운다.
        self.assertEqual(len(sleep.call_args_list), 1)
        self.assertLessEqual(sleep.call_args[0][0], geeknews.TOPIC_REQUEST_INTERVAL_SECONDS)

    def test_gives_up_the_run_after_consecutive_blocks(self):
        with (
            patch.object(geeknews.requests, "get", return_value=_resp(403)) as get,
            patch.object(geeknews.time, "sleep"),
        ):
            for _ in range(10):
                self.assertIsNone(geeknews.fetch_geeknews_metrics("1"))

        # 10번 불러도 서버를 두드리는 건 상한까지다. 그래야 차단이 갱신되지 않는다.
        self.assertEqual(get.call_count, geeknews.MAX_CONSECUTIVE_BLOCKS)

    def test_block_page_served_as_200_is_not_read_as_success(self):
        # 차단 페이지가 200으로 온다. 상태 코드만 보면 통과로 읽혀 빈 지표가 저장된다.
        with (
            patch.object(geeknews.requests, "get", return_value=_resp(200, "Forbidden")) as get,
            patch.object(geeknews.time, "sleep"),
        ):
            for _ in range(10):
                self.assertIsNone(geeknews.fetch_geeknews_metrics("1"))

        self.assertEqual(get.call_count, geeknews.MAX_CONSECUTIVE_BLOCKS)

    def test_a_success_clears_the_streak(self):
        pages = [_resp(403), _resp(403), _resp(), _resp(403), _resp(403)]
        with (
            patch.object(geeknews.requests, "get", side_effect=pages) as get,
            patch.object(geeknews.time, "sleep"),
        ):
            for _ in range(len(pages)):
                geeknews.fetch_geeknews_metrics("1")

        # 중간에 한 번 성공하면 연속이 끊겨 남은 요청을 계속한다.
        self.assertEqual(get.call_count, len(pages))


class MetricsIndexTests(unittest.TestCase):
    """지표는 /newest 목록에서 받는다. 글마다 토픽 페이지를 열던 때는 회차당 50건을
    요청했고 그 물량이 차단을 불렀다."""

    LISTING = """
      <div class="topic_row" data-topic-state-id="100">
        <div class="topicinfo"><span id="tp100">7</span>points by
          <a class="u" data-topic-comment-count="4" data-topic-comment-topic-id="100">댓글 4개</a>
        </div>
      </div>
      <div class="topic_row" data-topic-state-id="101">
        <div class="topicinfo"><span id="tp101">2</span>points by
          <a class="u" data-topic-comment-count="0" data-topic-comment-topic-id="101">댓글 0개</a>
        </div>
      </div>
    """

    def setUp(self):
        geeknews.reset_topic_throttle()
        self.addCleanup(geeknews.reset_topic_throttle)

    def test_reads_points_and_comment_counts_from_the_listing(self):
        with (
            patch.object(geeknews.requests, "get", return_value=_resp(text=self.LISTING)) as get,
            patch.object(geeknews.time, "sleep"),
        ):
            index = geeknews.fetch_metrics_index(["100", "101"])

        self.assertEqual(index["100"], {"likes": 7, "comments": 4})
        self.assertEqual(index["101"], {"likes": 2, "comments": 0})
        # 필요한 id를 첫 장에서 다 찾았으면 남은 장은 받지 않는다.
        self.assertEqual(get.call_count, 1)

    def test_stops_paging_when_the_listing_is_challenged(self):
        with (
            patch.object(
                geeknews.requests, "get", return_value=_resp(text="browser-check-turnstile")
            ) as get,
            patch.object(geeknews.time, "sleep"),
        ):
            self.assertEqual(geeknews.fetch_metrics_index(["100"]), {})

        self.assertEqual(get.call_count, 1)

    def test_challenge_page_is_not_read_as_a_valid_topic(self):
        # 브라우저 확인 페이지는 200에 정상 HTML로 온다. 차단으로 세지 않으면
        # 셀렉터가 전부 None을 돌려 빈 지표가 조용히 저장된다.
        page = '<html><div id="browser-check-turnstile"></div></html>'
        with (
            patch.object(geeknews.requests, "get", return_value=_resp(text=page)) as get,
            patch.object(geeknews.time, "sleep"),
        ):
            for _ in range(10):
                self.assertIsNone(geeknews.fetch_geeknews_metrics("1"))

        self.assertEqual(get.call_count, geeknews.MAX_CONSECUTIVE_BLOCKS)


if __name__ == "__main__":
    unittest.main()
