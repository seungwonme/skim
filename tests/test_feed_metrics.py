"""Feed 크롤러 지표 회귀 테스트.

`--days`(RSS 경로)로 수집한 행은 likes/comments가 비어 저장됐다.
홈페이지, Top Stories 경로만 지표를 채우던 비대칭이 원인이라, 같은 플랫폼인데
수집 방식에 따라 지표 유무가 갈렸다. 이 파일은 그 비대칭이 되돌아오는 것을 막는다.

HN은 피드가 Points/# Comments를 이미 싣는다. GeekNews RSS(Atom)에는 없어서
토픽 페이지를 긁어야 한다. 두 플랫폼의 경로가 다른 이유가 이것이다.

YouTube 조회수는 `test_youtube_view_count.py`가 맡는다.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from skim_core.crawlers.feed.geeknews import (
    GeekNewsCrawler,
    fetch_geeknews_metrics,
    topic_id_from_url,
)
from skim_core.crawlers.feed.hackernews import (
    HackerNewsCrawler,
    fetch_hn_metrics,
    metrics_from_feed,
)

SINCE = datetime(2026, 8, 1, tzinfo=timezone.utc)

# hnrss.org가 실제로 보내는 description이다. 지표가 여기 이미 들어 있어서
# item API를 따로 부를 이유가 없다. 지어낸 픽스처는 파싱 실패를 통과시킨다.
HN_FEED_DESC = (
    '<p>Article URL: <a href="https://nate.spot/x/">https://nate.spot/x/</a></p>\n'
    '<p>Comments URL: <a href="https://news.ycombinator.com/item?id=49221939">'
    "https://news.ycombinator.com/item?id=49221939</a></p>\n"
    "<p>Points: 63</p>\n<p># Comments: 81</p>"
)

# news.hada.io 실제 마크업을 그대로 옮겼다. 지어낸 픽스처는 파싱 실패를 통과시킨다.
# 포인트는 span#tp{id}, 댓글 수는 data- 접두사가 붙은 속성으로만 나온다.
GEEKNEWS_HTML = """
<html><body>
<div class="topicinfo"><span id='tp32235'>7</span>P by <a href='/@neo'>GN</a></div>
<a id='topic-comment-link' data-topic-comment-topic-id='32235'
   data-topic-comment-count='3' href='topic?id=32235'>댓글 3개</a>
</body></html>
"""


class HackerNewsMetricsTests(unittest.TestCase):
    """피드의 Points/# Comments를 먼저 쓰고, 없을 때만 item API로 폴백한다.

    Algolia item API는 최상위에 `points`만 주고 댓글 수를 주지 않아
    지표 정본으로 쓸 수 없다. 폴백은 Firebase 쪽이다.
    """

    def _rss_item(self):
        return {
            "platform": "hackernews",
            "author": "dan",
            "title": "Story",
            "url": "https://example.com/article",
            "published": "2026-08-01T09:00:00+09:00",
            "external_id": "item?id=100",
        }

    def _stub_body(self):
        """본문 합성은 이 테스트의 관심사가 아니다. 네트워크만 막는다."""
        return patch(
            "skim_core.crawlers.feed.hackernews.fetch_hn_discussion",
            return_value=None,
        )

    def _stub_enrich(self):
        return patch(
            "skim_core.crawlers.feed.hackernews.enrich_with_content",
            side_effect=lambda targets: targets,
        )

    def test_feed_description_already_carries_metrics(self):
        """피드가 실어 보낸 값이다. 이걸 쓰면 요청이 한 번도 안 나간다."""
        self.assertEqual(metrics_from_feed(HN_FEED_DESC), {"likes": 63, "comments": 81})

    def test_feed_without_metrics_returns_none(self):
        self.assertIsNone(metrics_from_feed("<p>Article URL: x</p>"))
        self.assertIsNone(metrics_from_feed(""))

    def test_rss_path_prefers_feed_over_item_api(self):
        """지표가 피드에 있으면 item API를 부르지 않는다."""
        crawler = HackerNewsCrawler()
        item = dict(self._rss_item(), content_html=HN_FEED_DESC)
        with (
            patch("skim_core.crawlers.feed.hackernews.fetch_feed", return_value=[item]),
            self._stub_enrich(),
            self._stub_body(),
            patch("skim_core.crawlers.feed.hackernews.fetch_hn_metrics") as api,
        ):
            posts = asyncio.run(crawler.crawl(since=SINCE))

        api.assert_not_called()
        self.assertEqual(posts[0].likes, 63)
        self.assertEqual(posts[0].comments, 81)

    def test_fetch_hn_metrics_reads_score_and_descendants(self):
        resp = MagicMock()
        resp.json.return_value = {"score": 41, "descendants": 60}
        with patch(
            "skim_core.crawlers.feed.hackernews.requests.get", return_value=resp
        ):
            self.assertEqual(fetch_hn_metrics("100"), {"likes": 41, "comments": 60})

    def test_fetch_hn_metrics_returns_none_on_error(self):
        with patch(
            "skim_core.crawlers.feed.hackernews.requests.get",
            side_effect=Exception("boom"),
        ):
            self.assertIsNone(fetch_hn_metrics("100"))

    def test_rss_path_fills_metrics_from_firebase(self):
        crawler = HackerNewsCrawler()
        with (
            patch(
                "skim_core.crawlers.feed.hackernews.fetch_feed",
                return_value=[self._rss_item()],
            ),
            self._stub_enrich(),
            self._stub_body(),
            patch(
                "skim_core.crawlers.feed.hackernews.fetch_hn_metrics",
                return_value={"likes": 41, "comments": 60},
            ),
        ):
            posts = asyncio.run(crawler.crawl(since=SINCE))

        self.assertEqual(posts[0].likes, 41)
        self.assertEqual(posts[0].comments, 60)

    def test_top_story_path_skips_the_extra_request(self):
        """Top Stories 경로는 이미 Firebase 값을 갖고 있다. 다시 부르지 않는다."""
        crawler = HackerNewsCrawler()
        items = [dict(self._rss_item(), likes=10, num_comments=3)]
        with (
            patch.object(crawler, "_fetch_top_story_items", return_value=items),
            self._stub_enrich(),
            self._stub_body(),
            patch("skim_core.crawlers.feed.hackernews.fetch_hn_metrics") as metrics,
        ):
            posts = asyncio.run(crawler.crawl(count=1))

        metrics.assert_not_called()
        self.assertEqual(posts[0].likes, 10)
        self.assertEqual(posts[0].comments, 3)

    def test_failed_metrics_leave_fields_empty(self):
        """수집 실패가 0점으로 저장되면 안 된다. 0과 미수집은 다른 값이다."""
        crawler = HackerNewsCrawler()
        with (
            patch(
                "skim_core.crawlers.feed.hackernews.fetch_feed",
                return_value=[self._rss_item()],
            ),
            self._stub_enrich(),
            self._stub_body(),
            patch(
                "skim_core.crawlers.feed.hackernews.fetch_hn_metrics",
                return_value=None,
            ),
        ):
            posts = asyncio.run(crawler.crawl(since=SINCE))

        self.assertIsNone(posts[0].likes)
        self.assertIsNone(posts[0].comments)


class GeekNewsMetricsTests(unittest.TestCase):
    def test_topic_id_from_url(self):
        self.assertEqual(
            topic_id_from_url("https://news.hada.io/topic?id=32235"), "32235"
        )
        self.assertIsNone(topic_id_from_url("https://news.hada.io/"))
        self.assertIsNone(topic_id_from_url(""))

    def test_fetch_metrics_parses_points_and_comments(self):
        resp = MagicMock()
        resp.text = GEEKNEWS_HTML
        with patch("skim_core.crawlers.feed.geeknews.requests.get", return_value=resp):
            metrics = fetch_geeknews_metrics("32235")

        self.assertEqual(metrics["likes"], 7)
        self.assertEqual(metrics["comments"], 3)

    def test_fetch_metrics_returns_none_on_error(self):
        with patch(
            "skim_core.crawlers.feed.geeknews.requests.get",
            side_effect=Exception("boom"),
        ):
            self.assertIsNone(fetch_geeknews_metrics("32235"))

    def test_rss_path_attaches_metrics_to_post(self):
        crawler = GeekNewsCrawler()
        items = [
            {
                "platform": "geeknews",
                "author": "xguru",
                "title": "제목",
                "url": "https://news.hada.io/topic?id=32235",
                "published": "2026-08-01T09:00:00+09:00",
            }
        ]
        with (
            patch("skim_core.crawlers.feed.geeknews.fetch_feed", return_value=items),
            patch("skim_core.crawlers.feed.geeknews.enrich_with_content"),
            patch(
                "skim_core.crawlers.feed.geeknews.fetch_geeknews_metrics",
                return_value={"likes": 7, "comments": 3},
            ),
        ):
            posts = asyncio.run(crawler.crawl(since=SINCE))

        self.assertEqual(posts[0].likes, 7)
        self.assertEqual(posts[0].comments, 3)

    def test_no_content_skips_metric_requests(self):
        """`--no-content`는 추가 요청을 하지 않는다는 계약을 지킨다."""
        crawler = GeekNewsCrawler()
        items = [
            {
                "platform": "geeknews",
                "author": "xguru",
                "title": "제목",
                "url": "https://news.hada.io/topic?id=32235",
                "published": "2026-08-01T09:00:00+09:00",
            }
        ]
        with (
            patch("skim_core.crawlers.feed.geeknews.fetch_feed", return_value=items),
            patch("skim_core.crawlers.feed.geeknews.enrich_with_content"),
            patch(
                "skim_core.crawlers.feed.geeknews.fetch_geeknews_metrics"
            ) as fetch_metrics,
        ):
            posts = asyncio.run(crawler.crawl(since=SINCE, no_content=True))

        fetch_metrics.assert_not_called()
        self.assertIsNone(posts[0].likes)


if __name__ == "__main__":
    unittest.main()
