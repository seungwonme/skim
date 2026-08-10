"""--count가 enrichment 앞에서 잘리는지 검증한다.

CLI는 마지막에 posts[:count]로 자른다. 크롤러가 그 전에 안 자르면 버려질
항목까지 원문 추출(defuddle/yt-dlp)과 댓글·지표 조회를 끝낸 뒤 버려진다.
N건을 얻자고 수십 건분의 시간과 외부 요청을 치르고 결과는 저장도 안 된다.
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from skim_core.crawlers.feed import ailabs, blogs, everyto, geeknews, producthunt


def _items(n, prefix="https://example.com/"):
    now = datetime.now(timezone.utc)
    return [
        {
            "platform": "x",
            "title": f"post {i}",
            "url": f"{prefix}{i}",
            "published": (now - timedelta(minutes=i)).isoformat(),
            "author": "a",
            "summary": "s",
            "content_html": "<p>tagline</p>",
        }
        for i in range(n)
    ]


class CountTruncationTests(unittest.TestCase):
    """enrich_with_content가 count개만 받는지 본다."""

    def _assert_truncated(self, module, crawler, count, feed_items, **kwargs):
        seen = {}

        def spy(items):
            seen["n"] = len(items)
            for item in items:
                item.setdefault("content_markdown", "body")
            return items

        with (
            patch.object(module, "enrich_with_content", side_effect=spy),
            patch.object(module, "fetch_feed", return_value=list(feed_items)),
        ):
            asyncio.run(
                crawler.crawl(
                    since=datetime.now(timezone.utc) - timedelta(days=1),
                    count=count,
                    **kwargs,
                )
            )
        self.assertEqual(
            seen.get("n"),
            count,
            f"{module.__name__}: enrichment가 {seen.get('n')}건을 받았다 (count={count})",
        )

    def test_producthunt(self):
        with patch.object(producthunt, "fetch_comment_section", return_value=None):
            self._assert_truncated(
                producthunt, producthunt.ProductHuntCrawler(), 3, _items(20)
            )

    def test_geeknews(self):
        with patch.object(geeknews, "fetch_geeknews_metrics", return_value=None):
            self._assert_truncated(geeknews, geeknews.GeekNewsCrawler(), 3, _items(20))

    def test_everyto(self):
        self._assert_truncated(everyto, everyto.EveryToCrawler(), 3, _items(20))

    def test_blogs(self):
        self._assert_truncated(blogs, blogs.BlogsCrawler(), 3, _items(20))


class AiLabsGlobalCapTests(unittest.TestCase):
    def test_count_is_a_global_cap_not_per_source(self):
        # 소스별 limit이 이미 count라, 소스 9개면 최대 9*count개가 모인다.
        seen = {}

        def spy(items):
            seen["n"] = len(items)
            for item in items:
                item.setdefault("content_markdown", "body")
            return items

        many = _items(40)
        with (
            patch.object(ailabs, "enrich_with_content", side_effect=spy),
            patch.object(ailabs, "_dispatch", return_value=iter(many)),
        ):
            asyncio.run(
                ailabs.AILabsCrawler().crawl(
                    since=datetime.now(timezone.utc) - timedelta(days=1), count=5
                )
            )

        self.assertEqual(seen.get("n"), 5)


if __name__ == "__main__":
    unittest.main()
