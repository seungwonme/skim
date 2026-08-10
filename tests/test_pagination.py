"""창이 한 페이지보다 넓을 때 다음 페이지를 받는지 검증한다.

arXiv 한 장(50건)은 실측 4시간25분치다(2026-08-10, cs.AI). count를 그보다 크게
잡으면 페이징 없이는 못 채운다. hackernews는 hnrss count 기본값이 20이라
24시간 창에 65건이 있어도 20건만 들어왔다.
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from skim_core.crawlers.feed import arxiv
from skim_core.crawlers.feed.arxiv import ArxivCrawler
from skim_core.feed_config import (
    ARXIV_MAX_RESULTS_PER_CATEGORY,
    HACKERNEWS_FEED_COUNT,
    HACKERNEWS_FEEDS,
    HACKERNEWS_SHOW_ASK_COUNT,
    arxiv_api_url,
)


def _entry(idx, when):
    e = {
        "title": f"paper {idx}",
        "link": f"https://arxiv.org/abs/{idx}",
        "published": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": "abstract",
        "authors": [{"name": "A"}],
    }
    return e


class ArxivPaginationTests(unittest.TestCase):
    def _run(self, count, pages):
        """pages: {start: [entry, ...]}"""
        calls = []

        def fake_fetch(category, start=0):
            calls.append((category, start))
            feed = MagicMock()
            feed.entries = pages.get(start, []) if category == "cs.AI" else []
            return feed

        with (
            patch.object(arxiv, "_fetch_category", fake_fetch),
            patch.object(arxiv.time, "sleep"),
        ):
            posts = asyncio.run(
                ArxivCrawler().crawl(
                    since=datetime.now(timezone.utc) - timedelta(days=4),
                    no_content=True,
                    count=count,
                )
            )
        return posts, calls

    def test_single_page_when_count_fits(self):
        # count가 한 장 안에 들어오면 요청은 카테고리당 1회. 기존과 같다.
        now = datetime.now(timezone.utc)
        page0 = [_entry(i, now - timedelta(minutes=i)) for i in range(50)]
        posts, calls = self._run(10, {0: page0})

        starts = [s for c, s in calls if c == "cs.AI"]
        self.assertEqual(starts, [0], "한 장이면 다음 페이지를 부르지 않는다")
        self.assertEqual(len(posts), 10)

    def test_second_page_is_fetched_when_count_exceeds_one_page(self):
        now = datetime.now(timezone.utc)
        page0 = [_entry(i, now - timedelta(minutes=i)) for i in range(50)]
        page1 = [
            _entry(100 + i, now - timedelta(hours=5, minutes=i)) for i in range(50)
        ]
        posts, calls = self._run(80, {0: page0, ARXIV_MAX_RESULTS_PER_CATEGORY: page1})

        starts = [s for c, s in calls if c == "cs.AI"]
        self.assertIn(
            ARXIV_MAX_RESULTS_PER_CATEGORY, starts, "두 번째 장을 받아야 한다"
        )
        self.assertEqual(len(posts), 80)

    def test_stops_when_page_falls_out_of_the_window(self):
        # 마지막 항목이 창 밖이면 다음 페이지는 전부 창 밖이다.
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        page0 = [_entry(i, old) for i in range(50)]
        _, calls = self._run(200, {0: page0, ARXIV_MAX_RESULTS_PER_CATEGORY: []})

        starts = [s for c, s in calls if c == "cs.AI"]
        self.assertEqual(starts, [0], "창을 벗어나면 멈춘다")

    def test_start_param_is_in_the_url(self):
        self.assertIn("start=0", arxiv_api_url("cs.AI"))
        self.assertIn("start=50", arxiv_api_url("cs.AI", start=50))


class HackerNewsFeedCountTests(unittest.TestCase):
    def test_all_feeds_request_more_than_the_default_twenty(self):
        # 기본값 20은 24시간 창(실측 65건)의 3분의 1도 못 덮는다.
        self.assertGreaterEqual(HACKERNEWS_FEED_COUNT, 100)
        self.assertIn(f"count={HACKERNEWS_FEED_COUNT}", HACKERNEWS_FEEDS["hackernews"])
        for name in ("hackernews/show", "hackernews/ask"):
            self.assertIn(f"count={HACKERNEWS_SHOW_ASK_COUNT}", HACKERNEWS_FEEDS[name])

    def test_show_and_ask_stay_smaller_than_newest(self):
        # 점수 문턱이 없어서 하루치를 다 받으면 저품질까지 전부 enrichment를 돈다.
        self.assertLess(HACKERNEWS_SHOW_ASK_COUNT, HACKERNEWS_FEED_COUNT)

    def test_score_gate_stays_off_for_show_and_ask(self):
        for name in ("hackernews/show", "hackernews/ask"):
            self.assertNotIn("points=", HACKERNEWS_FEEDS[name])


if __name__ == "__main__":
    unittest.main()
