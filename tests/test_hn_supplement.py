"""Show/Ask HN 피드가 상한에 닿았을 때 Algolia 보충이 창 앞쪽 고득점 글을 채우는지 본다.

hnrss show/ask는 점수 문턱 없이 최신순 30건에서 잘린다. 하루 창에 글이 더 많으면
창 앞쪽에서 점수가 오른 글이 영구히 빠진다.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from skim_core.crawlers.feed import hackernews
from skim_core.feed_config import (
    HACKERNEWS_SHOW_ASK_COUNT,
    HACKERNEWS_SUPPLEMENT_MIN_POINTS,
)

SINCE = datetime(2026, 10, 5, tzinfo=timezone.utc)


def _hit(story_id: str, **overrides) -> dict:
    hit = {
        "objectID": story_id,
        "title": f"Show HN: {story_id}",
        "url": f"https://example.com/{story_id}",
        "author": "someone",
        "points": 20,
        "num_comments": 5,
        "created_at_i": 1_759_600_000,
    }
    hit.update(overrides)
    return hit


def _feed_items(source: str, count: int, start: int = 1000) -> list:
    """hnrss가 준 최신 글. 점수는 낮다."""
    return [
        hackernews._algolia_item(_hit(str(start + i), points=1), source)
        for i in range(count)
    ]


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _crawl(feeds: dict):
    with patch.object(
        hackernews, "fetch_feed", side_effect=lambda url, name, since: feeds[name]
    ):
        return asyncio.run(
            hackernews.HackerNewsCrawler().crawl(since=SINCE, no_content=True)
        )


class TestSupplement(unittest.TestCase):
    def test_adds_old_high_point_post_when_show_feed_hit_the_cap(self):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params)
            return FakeResponse({"hits": [_hit("49963394", points=32)]})

        feeds = {
            "hackernews": [],
            "hackernews/show": _feed_items(
                "hackernews/show", HACKERNEWS_SHOW_ASK_COUNT
            ),
            "hackernews/ask": [],
        }
        # newest와 ask가 0건이면 Algolia 폴백이 같이 돌므로 폴백은 막고 본다.
        with (
            patch.object(hackernews._ALGOLIA_SESSION, "get", side_effect=fake_get),
            patch.object(hackernews, "fetch_algolia_fallback", return_value=[]),
        ):
            posts = _crawl(feeds)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tags"], "story,show_hn")
        # 괄호로 싸면 Algolia가 OR로 읽어 필터가 풀린다.
        self.assertNotIn("(", calls[0]["tags"])
        self.assertIn(
            f"points>={HACKERNEWS_SUPPLEMENT_MIN_POINTS}", calls[0]["numericFilters"]
        )
        self.assertIn("49963394", {p.external_id for p in posts})
        added = next(p for p in posts if p.external_id == "49963394")
        self.assertEqual(added.source, "hackernews/show")
        self.assertEqual(added.likes, 32)
        self.assertEqual(len(posts), HACKERNEWS_SHOW_ASK_COUNT + 1)

    def test_skips_supplement_when_feed_covered_the_window(self):
        feeds = {
            "hackernews": [],
            "hackernews/show": _feed_items("hackernews/show", 3),
            "hackernews/ask": _feed_items("hackernews/ask", 2, start=2000),
        }
        with (
            patch.object(hackernews._ALGOLIA_SESSION, "get") as get,
            patch.object(hackernews, "fetch_algolia_fallback", return_value=[]),
        ):
            posts = _crawl(feeds)

        get.assert_not_called()
        self.assertEqual(len(posts), 5)

    def test_dedupes_post_the_feed_already_returned(self):
        feed = _feed_items("hackernews/show", HACKERNEWS_SHOW_ASK_COUNT)
        dup_id = feed[0]["external_id"].split("=")[1]

        def fake_get(url, params=None, timeout=None):
            return FakeResponse(
                {
                    "hits": [
                        # 피드가 이미 준 글. story id가 같다.
                        _hit(dup_id, points=40),
                        # 링크만 같은 글 (id가 다른 재게시가 아니라 같은 URL 중복).
                        _hit("7777", url=feed[1]["url"]),
                        _hit("8888", points=12),
                    ]
                }
            )

        feeds = {"hackernews": [], "hackernews/show": feed, "hackernews/ask": []}
        with (
            patch.object(hackernews._ALGOLIA_SESSION, "get", side_effect=fake_get),
            patch.object(hackernews, "fetch_algolia_fallback", return_value=[]),
        ):
            posts = _crawl(feeds)

        ids = [p.external_id for p in posts]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(posts), HACKERNEWS_SHOW_ASK_COUNT + 1)
        self.assertIn("8888", ids)
        self.assertNotIn("7777", ids)

    def test_algolia_failure_keeps_feed_results(self):
        feeds = {
            "hackernews": [],
            "hackernews/show": _feed_items(
                "hackernews/show", HACKERNEWS_SHOW_ASK_COUNT
            ),
            "hackernews/ask": [],
        }
        with (
            patch.object(
                hackernews._ALGOLIA_SESSION,
                "get",
                side_effect=RuntimeError("algolia down"),
            ),
            patch.object(hackernews, "fetch_algolia_fallback", return_value=[]),
        ):
            posts = _crawl(feeds)

        self.assertEqual(len(posts), HACKERNEWS_SHOW_ASK_COUNT)

    def test_malformed_response_keeps_feed_results(self):
        feeds = {
            "hackernews": [],
            "hackernews/show": _feed_items(
                "hackernews/show", HACKERNEWS_SHOW_ASK_COUNT
            ),
            "hackernews/ask": [],
        }
        with (
            patch.object(
                hackernews._ALGOLIA_SESSION,
                "get",
                return_value=FakeResponse(["not", "a", "dict"]),
            ),
            patch.object(hackernews, "fetch_algolia_fallback", return_value=[]),
        ):
            posts = _crawl(feeds)

        self.assertEqual(len(posts), HACKERNEWS_SHOW_ASK_COUNT)

    def test_ask_feed_uses_ask_tag(self):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params["tags"])
            return FakeResponse({"hits": []})

        feeds = {
            "hackernews": [],
            "hackernews/show": [],
            "hackernews/ask": _feed_items(
                "hackernews/ask", HACKERNEWS_SHOW_ASK_COUNT, start=2000
            ),
        }
        with (
            patch.object(hackernews._ALGOLIA_SESSION, "get", side_effect=fake_get),
            patch.object(hackernews, "fetch_algolia_fallback", return_value=[]),
        ):
            _crawl(feeds)

        self.assertEqual(calls, ["story,ask_hn"])


if __name__ == "__main__":
    unittest.main()
