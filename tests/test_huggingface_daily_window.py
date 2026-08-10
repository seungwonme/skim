"""HF Daily Papers가 큐레이션 날짜로 창을 자르는지 검증한다.

`publishedAt`은 arXiv 발행일이라 큐레이션보다 며칠에서 몇 주 앞선다. 그 값으로
`--days` 창을 자르면 오늘 올라온 논문이 통째로 걸러져 매일 0건이 나온다
(2026-08-10 실측: 목록의 publishedAt 최댓값이 5일 전이라 3일 창에서 0건).
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from skim_core.crawlers.feed.huggingface import HuggingFaceCrawler


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _paper(title, published_at, submitted_on_daily_at):
    return {
        "title": title,
        "publishedAt": _iso(published_at),
        "summary": "abstract text",
        "thumbnail": "",
        "numComments": 0,
        "paper": {
            "id": title.lower(),
            "authors": [{"name": "Author One"}],
            "publishedAt": _iso(published_at),
            "submittedOnDailyAt": _iso(submitted_on_daily_at)
            if submitted_on_daily_at
            else None,
        },
    }


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class HuggingFaceDailyWindowTests(unittest.TestCase):
    def _crawl(self, payload, since):
        with patch(
            "skim_core.crawlers.feed.huggingface.requests.get",
            return_value=FakeResponse(payload),
        ):
            return asyncio.run(
                HuggingFaceCrawler().crawl(since=since, no_content=True, count=50)
            )

    def test_keeps_paper_curated_today_even_when_published_weeks_ago(self):
        now = datetime.now(timezone.utc)
        payload = [_paper("Fresh", now - timedelta(days=21), now - timedelta(hours=2))]

        posts = self._crawl(payload, since=now - timedelta(days=3))

        self.assertEqual([p.title for p in posts], ["Fresh"])

    def test_drops_paper_curated_before_the_window(self):
        now = datetime.now(timezone.utc)
        payload = [_paper("Stale", now - timedelta(hours=2), now - timedelta(days=10))]

        posts = self._crawl(payload, since=now - timedelta(days=3))

        self.assertEqual(posts, [], "큐레이션 날짜가 창 밖이면 제외한다")

    def test_falls_back_to_published_at_when_curation_date_is_missing(self):
        now = datetime.now(timezone.utc)
        payload = [_paper("NoDaily", now - timedelta(hours=2), None)]

        posts = self._crawl(payload, since=now - timedelta(days=3))

        self.assertEqual([p.title for p in posts], ["NoDaily"])

    def test_curation_date_is_kept_on_the_post(self):
        now = datetime.now(timezone.utc)
        curated = now - timedelta(hours=5)
        payload = [_paper("Tagged", now - timedelta(days=9), curated)]

        post = self._crawl(payload, since=now - timedelta(days=3))[0]

        self.assertTrue(getattr(post, "submitted_on_daily_at", ""))
        # timestamp는 논문 발행일 그대로 둔다 (기존 행과 축을 맞춘다).
        self.assertIn((now - timedelta(days=9)).strftime("%Y-%m-%d"), post.timestamp)


if __name__ == "__main__":
    unittest.main()
