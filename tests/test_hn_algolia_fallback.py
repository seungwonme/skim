"""hnrss가 죽은 회차에 HN이 통째로 유실되지 않는지 검증한다.

2026-08-10 프로덕션에서 hnrss.org가 502를 내며 세 피드가 동시에 0건이 됐고,
데일리는 고정 창으로 돌아 그날 HN이 그대로 사라졌다.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from skim_core.crawlers.feed import hackernews


def _hit(story_id: str, **overrides) -> dict:
    hit = {
        "objectID": story_id,
        "title": f"Story {story_id}",
        "url": f"https://example.com/{story_id}",
        "author": "someone",
        "points": 42,
        "num_comments": 7,
        "created_at_i": 1_754_800_000,
    }
    hit.update(overrides)
    return hit


def _item(story_id: str) -> dict:
    return hackernews._algolia_item(_hit(story_id), "hackernews")


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestAlgoliaItemShape(unittest.TestCase):
    def test_hit_maps_to_feed_item_shape(self):
        item = _item("111")
        # external_id는 hnrss guid와 같은 모양이어야 _story_id가 같은 값을 뽑는다.
        self.assertEqual(hackernews.HackerNewsCrawler._story_id(item), "111")
        self.assertEqual(item["likes"], 42)
        self.assertEqual(item["num_comments"], 7)
        self.assertTrue(item["published"].endswith("+00:00"))

    def test_ask_hn_without_url_points_at_discussion_page(self):
        item = hackernews._algolia_item(_hit("222", url=None), "hackernews/ask")
        self.assertEqual(item["url"], "https://news.ycombinator.com/item?id=222")

    def test_hit_without_id_is_dropped(self):
        self.assertIsNone(hackernews._algolia_item({"title": "x"}, "hackernews"))


class TestAlgoliaFallbackQuery(unittest.TestCase):
    def setUp(self):
        self.since = datetime(2026, 8, 10, tzinfo=timezone.utc)

    def test_queries_three_feeds_with_and_tags(self):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params)
            return FakeResponse({"hits": [_hit(str(len(calls)))]})

        with patch.object(hackernews._ALGOLIA_SESSION, "get", side_effect=fake_get):
            items = hackernews.fetch_algolia_fallback(self.since)

        self.assertEqual(len(items), 3)
        self.assertEqual(
            [c["tags"] for c in calls], ["story", "story,show_hn", "story,ask_hn"]
        )
        # 괄호로 싸면 Algolia가 OR로 읽어 필터가 통째로 풀린다 (실측: 전체 글이 온다).
        for call in calls:
            self.assertNotIn("(", call["tags"])
        ts = int(self.since.timestamp())
        self.assertIn(f"created_at_i>={ts}", calls[0]["numericFilters"])
        self.assertIn("points>=30", calls[0]["numericFilters"])
        # Show/Ask는 점수 문턱이 없다. 본문이 알맹이라 문턱에 걸리면 대부분 사라진다.
        self.assertNotIn("points", calls[1]["numericFilters"])

    def test_one_feed_failing_does_not_lose_the_others(self):
        def fake_get(url, params=None, timeout=None):
            if params["tags"] == "story":
                raise RuntimeError("algolia down")
            return FakeResponse({"hits": [_hit("9")]})

        with patch.object(hackernews._ALGOLIA_SESSION, "get", side_effect=fake_get):
            items = hackernews.fetch_algolia_fallback(self.since)

        self.assertEqual(len(items), 2)

    def test_naive_since_is_read_as_kst(self):
        """CLI는 KST aware를 주지만, naive가 와도 창이 9시간 밀리면 안 된다."""
        captured = []

        def fake_get(url, params=None, timeout=None):
            captured.append(params["numericFilters"])
            return FakeResponse({"hits": []})

        naive = datetime(2026, 8, 10, 0, 0, 0)
        aware = naive.replace(tzinfo=hackernews.KST)
        with patch.object(hackernews._ALGOLIA_SESSION, "get", side_effect=fake_get):
            hackernews.fetch_algolia_fallback(naive)

        self.assertIn(f"created_at_i>={int(aware.timestamp())}", captured[0])


class TestFallbackTrigger(unittest.TestCase):
    def setUp(self):
        self.since = datetime(2026, 8, 10, tzinfo=timezone.utc)

    def test_falls_back_when_every_hnrss_feed_is_empty(self):
        with (
            patch.object(hackernews, "fetch_feed", return_value=[]),
            patch.object(
                hackernews, "fetch_algolia_fallback", return_value=[_item("333")]
            ),
        ):
            posts = asyncio.run(
                hackernews.HackerNewsCrawler().crawl(since=self.since, no_content=True)
            )

        self.assertEqual([p.external_id for p in posts], ["333"])

    def test_does_not_fall_back_when_hnrss_works(self):
        with (
            patch.object(hackernews, "fetch_feed", return_value=[_item("444")]),
            patch.object(hackernews, "fetch_algolia_fallback") as fallback,
        ):
            posts = asyncio.run(
                hackernews.HackerNewsCrawler().crawl(since=self.since, no_content=True)
            )

        fallback.assert_not_called()
        # 피드 3장이 같은 항목을 주므로 url 중복 제거로 1건만 남는다.
        self.assertEqual(len(posts), 1)

    def test_partial_feed_failure_does_not_trigger_fallback(self):
        """한 장이라도 살아 있으면 폴백을 돌리지 않는다. 중복 요청만 늘어난다."""
        responses = iter([[_item("1")], [], []])

        with (
            patch.object(
                hackernews, "fetch_feed", side_effect=lambda *a, **k: next(responses)
            ),
            patch.object(hackernews, "fetch_algolia_fallback") as fallback,
        ):
            asyncio.run(
                hackernews.HackerNewsCrawler().crawl(since=self.since, no_content=True)
            )

        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
