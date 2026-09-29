"""GeekNews 본문 경로 검증 (#29).

2026-09에 GeekNews 9월 저장분 1,082건 중 959건이 RSS 요약 조각만 남았다. 본문
추출이 enrichment.py에서 같은 토픽 페이지를 글마다 두 번씩, 간격도 차단 감지도
없이 열어서 매 회차 24요청쯤에서 막혔기 때문이다. 지금은 원문 링크를 목록에서
받고, 토픽 페이지는 글당 한 번만 크롤러의 간격과 서킷브레이커를 거쳐 연다.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from skim_core import enrichment
from skim_core.crawlers.feed import geeknews
from skim_core.crawlers.feed.geeknews import (
    GeekNewsCrawler,
    TopicPage,
    enrich_geeknews_item,
    parse_topic_page,
)

SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)

# news.hada.io/topic?id=34432 실제 마크업에서 필요한 부분만 남겼다 (2026-09-29).
TOPIC_HTML = """
<html><body>
<div class="topictitle link"><span id="dead34432"></span><a class="bold ud topic-title-link"
 href="https://git.mills.io/prologic/parley"><h1>Parley - 일반 IRC로 통신하는 연합형 탈중앙 채팅</h1></a>
 <span class="topicurl">(git.mills.io)</span></div>
<div class="topicinfo"><span id="tp34432">1</span>P</div>
<a data-topic-comment-count="1" data-topic-comment-topic-id="34432" href="topic?id=34432"
 id="topic-comment-link">댓글 1개</a>
<div class="topic_contents"><div><section class="article-content" id="topic_contents"
 itemprop="articleBody"><ul>
<li><strong>Parley</strong>는 각 개인이나 팀이 자기 도메인에 인스턴스를 운영하고, 기존 IRC 클라이언트로 플러그인 없이 이용하는 탈중앙 채팅 네트워크임</li>
<li>인스턴스는 <strong>DNS와 well-known 신원 문서</strong>로 서로를 발견하고, HTTPS로 서명된 메시지를 교환함</li>
</ul></section></div></div>
<div class="comment_row" data-comment-state-id="66481" id="cid66481" style="--depth:0">
<div class="commentinfo"><a href="/@neo">GN⁺</a> <a href="/topic?id=34432#cid66481"><time
 datetime="2026-09-28T22:35:40+09:00" title="2026-09-28 22:35">23시간전</time></a></div>
<div class="commentTD"><span class="comment_contents" id="contents66481"><ul><li><strong>LLM 기반
 개발</strong>에는 기존 분야의 작업을 살펴보지도 않은 채 새 프로젝트에 깊이 빠져들 수 있다는 단점이 있음.</li></ul></span></div>
</div>
</body></html>
"""

# /newest 목록 실제 마크업에서 필요한 부분만 남겼다. 링크 글은 제목이 원문을,
# Show GN 같은 자체 글은 토픽 경로를 가리킨다.
LISTING_HTML = """
<div class="topic_row" data-topic-state-id="34485">
  <div class="topictitle link"><a href="https://jagi.studio/posts/phyllotaxis/"><h1>엽서 배열 LED</h1></a></div>
  <div class="topicinfo"><span id="tp34485">5</span>P
    <a data-topic-comment-count="2" data-topic-comment-topic-id="34485">댓글 2개</a></div>
</div>
<div class="topic_row" data-topic-state-id="34487">
  <div class="topictitle"><a href="topic?id=34487"><h1>Show GN: 매크로 툴</h1></a></div>
  <div class="topicinfo"><span id="tp34487">3</span>P
    <a data-topic-comment-count="0" data-topic-comment-topic-id="34487">댓글 0개</a></div>
</div>
"""

ARTICLE = {
    "content_markdown": "Original article body with enough words to count",
    "word_count": 8,
}


def _resp(status=200, text=""):
    return Mock(status_code=status, text=text)


class _Isolated(unittest.TestCase):
    def setUp(self):
        geeknews.reset_topic_throttle()
        self.addCleanup(geeknews.reset_topic_throttle)


class ParseTests(unittest.TestCase):
    def test_topic_page_yields_everything_in_one_read(self):
        page = parse_topic_page(TOPIC_HTML, "34432")

        self.assertTrue(page.summary.startswith("- Parley"))
        self.assertEqual(page.original_url, "https://git.mills.io/prologic/parley")
        self.assertEqual(page.likes, 1)
        self.assertEqual(page.comments, 1)
        self.assertIn("## GeekNews Comments", page.comment_section)

    def test_listing_carries_the_original_link(self):
        # 원문 링크가 목록에 있어서 원문 추출에는 토픽 페이지가 필요 없다.
        with (
            patch.object(
                geeknews.requests, "get", return_value=_resp(text=LISTING_HTML)
            ),
            patch.object(geeknews.time, "sleep"),
        ):
            index = geeknews.fetch_listing_index(["34485", "34487"])

        self.assertEqual(
            index["34485"]["original_url"], "https://jagi.studio/posts/phyllotaxis/"
        )
        self.assertIsNone(index["34487"]["original_url"], "자체 글은 원문이 없다")
        self.assertEqual(index["34485"]["likes"], 5)


class CrawlRequestBudgetTests(_Isolated):
    def _items(self, n):
        return [
            {
                "platform": "geeknews",
                "author": "xguru",
                "title": f"글 {i}",
                "url": f"https://news.hada.io/topic?id={100 + i}",
                "published": "2026-09-28T09:00:00+00:00",
                "summary": f"RSS 요약 조각 {i} 이다...",
            }
            for i in range(n)
        ]

    def _crawl(self, items, topic_responses):
        topic_urls = []
        # 목록은 글마다 원문 링크를 준다. 토픽 페이지가 막혀도 원문은 이걸로 받는다.
        listing = "".join(
            f'<div class="topic_row" data-topic-state-id="{100 + i}">'
            f'<div class="topictitle link"><a href="https://example.com/{100 + i}">'
            f'<h1>t</h1></a></div><div class="topicinfo"><span id="tp{100 + i}">2</span>P'
            "</div></div>"
            for i in range(len(items))
        )

        def fake_get(url, **_kwargs):
            if "/newest" in url:
                return _resp(text=listing)
            topic_urls.append(url)
            return topic_responses(url)

        with (
            patch.object(geeknews, "fetch_feed", return_value=items),
            patch.object(geeknews.requests, "get", side_effect=fake_get),
            patch.object(geeknews.time, "sleep"),
            patch.object(
                geeknews, "extract_original", return_value=(ARTICLE, "defuddle", None)
            ),
            patch.object(geeknews.typer, "echo"),
        ):
            posts = asyncio.run(GeekNewsCrawler().crawl(since=SINCE))
        return posts, topic_urls

    def test_each_topic_page_is_opened_once(self):
        # 예전에는 요약과 원문 링크를 따로 받느라 글당 2번, 댓글까지 3번 열었다.
        posts, topic_urls = self._crawl(
            self._items(3),
            lambda url: _resp(text=TOPIC_HTML.replace("34432", url[-3:])),
        )

        self.assertEqual(len(topic_urls), 3)
        self.assertEqual(len(set(topic_urls)), 3)
        self.assertTrue(all(p.model_extra.get("geeknews_topic") == "ok" for p in posts))

    def test_blocked_run_stops_knocking_and_keeps_partial_bodies(self):
        posts, topic_urls = self._crawl(self._items(10), lambda url: _resp(403))

        # 막힌 뒤에도 두드리면 차단이 갱신된다. 서킷브레이커 상한까지만 연다.
        self.assertEqual(len(topic_urls), geeknews.MAX_CONSECUTIVE_BLOCKS)
        for post in posts:
            self.assertEqual(post.model_extra.get("content_status"), "partial")
            # 원문은 토픽 페이지 없이도 받았으므로 요약 조각만 남지는 않는다.
            self.assertIn("## Original Article", post.content_markdown)

    def test_topic_budget_goes_first_to_what_only_the_topic_page_has(self):
        # 한도(30건 안팎)가 회차 글 수(50건 안팎)보다 작다. 원문 링크 없는 자체 글은
        # 토픽 페이지가 본문 전부이고, 댓글은 토픽 페이지에만 있다.
        items = [
            {"title": "링크 글", "url": "https://news.hada.io/topic?id=1",
             "original_url": "https://example.com/1", "comments": 0},
            {"title": "Show GN", "url": "https://news.hada.io/topic?id=2", "comments": 0},
            {"title": "토론 글", "url": "https://news.hada.io/topic?id=3",
             "original_url": "https://example.com/3", "comments": 5},
        ]
        requested = []

        def fetch(topic_id):
            requested.append(topic_id)
            return None, "error"

        with (
            patch.object(geeknews, "fetch_topic", side_effect=fetch),
            patch.object(
                geeknews, "extract_original", return_value=(ARTICLE, "defuddle", None)
            ),
        ):
            result = geeknews.enrich_geeknews_items(items)

        self.assertEqual(requested, ["2", "3", "1"])
        # 저장 순서는 피드 순서 그대로다.
        self.assertEqual([i["title"] for i in result], ["링크 글", "Show GN", "토론 글"])

    def test_crawl_stops_at_the_hourly_budget_and_keeps_originals(self):
        # 크롤과 백필이 한 시간에 쓰는 토픽 요청을 합쳐 한도 아래로 둔다. 한도를 넘긴
        # 글은 요청하지 않고, 목록에서 받은 원문 링크로 원문만 붙인다.
        count = geeknews.TOPIC_BUDGET + 5
        posts, topic_urls = self._crawl(
            self._items(count),
            lambda url: _resp(text=TOPIC_HTML.replace("34432", url.split("=")[-1])),
        )

        self.assertEqual(len(topic_urls), geeknews.TOPIC_BUDGET)
        self.assertEqual(geeknews.topic_budget_left(), 0)
        partial = [p for p in posts if p.model_extra.get("content_status") == "partial"]
        self.assertEqual(len(partial), 5)
        for post in partial:
            self.assertIn("## Original Article", post.content_markdown)

    def test_generic_enrichment_does_not_open_topic_pages(self):
        # enrich_with_content가 토픽 페이지를 열면 간격과 서킷브레이커를 우회한다.
        item = {
            "platform": "geeknews",
            "title": "t",
            "url": "https://news.hada.io/topic?id=1",
        }
        with (
            patch.object(enrichment, "defuddle") as defuddle,
            patch.object(enrichment, "_HTTP_SESSION") as session,
        ):
            enrichment.enrich_with_content([item])

        defuddle.assert_not_called()
        session.get.assert_not_called()


class BodyCompositionTests(unittest.TestCase):
    """본문 순서는 GN 요약, 원문, 댓글이다. 토픽 페이지가 없으면 RSS 요약 조각이 최저선이다."""

    def _enrich(self, item, topic=None, original=(None, "failed", "paywalled")):
        with patch.object(geeknews, "extract_original", return_value=original):
            enrich_geeknews_item(item, topic)
        return item

    def test_full_body_when_topic_page_answers(self):
        topic = parse_topic_page(TOPIC_HTML, "34432")
        item = self._enrich(
            {"title": "Parley", "url": "https://news.hada.io/topic?id=34432"},
            topic,
            (ARTICLE, "defuddle", None),
        )

        body = item["content_markdown"]
        self.assertTrue(body.startswith("- Parley"))
        self.assertLess(
            body.index("## Original Article"), body.index("## GeekNews Comments")
        )
        self.assertEqual(item["original_url"], "https://git.mills.io/prologic/parley")
        self.assertNotIn("content_status", item)

    def test_useful_original_survives_without_topic_page(self):
        item = self._enrich(
            {
                "title": "Useful article",
                "url": "u",
                "original_url": "https://example.com/a",
            },
            original=(ARTICLE, "trafilatura", None),
        )

        self.assertEqual(item["content_markdown"], ARTICLE["content_markdown"])
        self.assertEqual(item["content_status"], "partial")
        self.assertEqual(item["enrichment_method"], "trafilatura")

    def test_feed_summary_is_the_floor_and_marks_itself_retryable(self):
        # save_posts는 method=failed인 행만 덮어쓴다. 마커가 없으면 조각이 정본으로 굳는다.
        item = self._enrich(
            {
                "title": "Jeff Dean",
                "url": "u",
                "summary": "구글 최고 과학자 제프 딘이 회사를 떠난다",
            }
        )

        self.assertEqual(
            item["content_markdown"], "구글 최고 과학자 제프 딘이 회사를 떠난다"
        )
        self.assertEqual(item["word_count"], 7)
        self.assertEqual(item["enrichment_method"], "failed")
        self.assertEqual(item["content_status"], "partial")

    def test_summary_that_only_echoes_the_title_is_dropped(self):
        item = self._enrich(
            {
                "title": "Alphabet을 떠나는 Jeff Dean",
                "url": "u",
                "summary": "Alphabet을 떠나는 Jeff Dean",
            }
        )

        self.assertEqual(item["content_markdown"], "")
        self.assertEqual(item["enrichment_method"], "failed")

    def test_stays_empty_without_any_feed_summary(self):
        item = self._enrich({"title": "No summary anywhere", "url": "u"})

        self.assertEqual(item["content_markdown"], "")
        self.assertEqual(item["word_count"], 0)

    def test_topic_summary_replaces_the_feed_fragment(self):
        topic = TopicPage(
            summary="- 전체 요약 첫 줄\n- 전체 요약 둘째 줄",
            original_url=None,
            likes=3,
            comments=0,
            comment_section=None,
        )
        item = self._enrich(
            {"title": "t", "url": "u", "summary": "전체 요약 첫..."}, topic
        )

        self.assertEqual(
            item["content_markdown"], "- 전체 요약 첫 줄\n- 전체 요약 둘째 줄"
        )
        self.assertEqual(item["likes"], 3)
        self.assertEqual(item["geeknews_topic"], "ok")


class SavedOriginalTests(unittest.TestCase):
    """백필이 partial 행의 원문을 다시 추출하지 않으려고 본문에서 원문 섹션을 꺼낸다."""

    def test_original_section_without_comments(self):
        body = (
            "- GN 요약\n\n---\n\n## Original Article\n\n원문 본문\n\n---\n\n"
            "## GeekNews Comments\n\n- **a**: b"
        )
        self.assertEqual(geeknews.saved_original_from(body), "원문 본문")

    def test_body_without_original_section(self):
        self.assertIsNone(geeknews.saved_original_from("RSS 요약 조각"))
        self.assertIsNone(geeknews.saved_original_from(None))
        self.assertIsNone(geeknews.saved_original_from("요약\n\n## Original Article\n\n"))


class TopicSummaryTests(unittest.TestCase):
    """enrichment.py에 있던 요약 파싱과 조합 규칙을 그대로 옮겨 왔다."""

    def _enrich(self, item, topic, original):
        with patch.object(geeknews, "extract_original", return_value=original):
            enrich_geeknews_item(item, topic)
        return item

    def _topic(self, summary):
        return TopicPage(
            summary=summary,
            original_url=None,
            likes=1,
            comments=0,
            comment_section=None,
        )

    def test_summary_keeps_bullets_and_paragraphs(self):
        html = (
            '<div class="topic_contents">'
            "<ul><li>첫 <strong>번째</strong> 요점</li><li>두 번째 요점</li></ul>"
            "<p>마무리 문단</p></div>"
        )

        self.assertEqual(
            parse_topic_page(html, "1").summary,
            "- 첫 번째 요점\n- 두 번째 요점\n마무리 문단",
        )

    def test_summary_is_none_without_container(self):
        self.assertIsNone(parse_topic_page("<div>no topic here</div>", "1").summary)

    def test_topic_summary_survives_a_junk_original(self):
        # 원문이 랜딩/디렉터리 페이지라 잡문만 나와도 본문이 무너지지 않는다.
        summary = "- 노동자 소유 기업 디렉터리 요약\n- 22,000개 이상 제품"
        item = self._enrich(
            {
                "title": "Directory landing",
                "url": "u",
                "original_url": "https://example.com/l",
            },
            self._topic(summary),
            (None, "failed", "landing junk"),
        )

        self.assertEqual(item["content_markdown"], summary)

    def test_original_follows_the_topic_summary(self):
        original = "Original article body with actual details"
        item = self._enrich(
            {
                "title": "Useful article",
                "url": "u",
                "original_url": "https://example.com/a",
            },
            self._topic("- 한국어 요약"),
            ({"content_markdown": original, "word_count": 6}, "defuddle", None),
        )

        self.assertEqual(
            item["content_markdown"],
            f"- 한국어 요약\n\n---\n\n## Original Article\n\n{original}",
        )


class ExtractOriginalTests(unittest.TestCase):
    def test_placeholder_from_defuddle_falls_back_to_the_ladder(self):
        body = {
            "content_markdown": "Recovered dynamic page content with enough detail",
            "word_count": 7,
        }
        with (
            patch.object(
                geeknews,
                "defuddle",
                return_value={"content_markdown": "Loading", "word_count": 1},
            ),
            patch.object(
                geeknews,
                "extract_article_content",
                return_value=(body, "playwright+trafilatura", None),
            ),
        ):
            data, method, _ = geeknews.extract_original(
                "https://example.com/d", "Dynamic page"
            )

        self.assertEqual(data, body)
        self.assertEqual(method, "playwright+trafilatura")

    def test_thin_ladder_result_is_not_attached(self):
        # 사다리는 실패해도 얇은 결과를 돌려준다. 차단 화면 문구가 원문으로 붙으면 안 된다.
        with (
            patch.object(geeknews, "defuddle", return_value=None),
            patch.object(
                geeknews,
                "extract_article_content",
                return_value=(
                    {"content_markdown": "Just a moment...", "word_count": 3},
                    "trafilatura",
                    "thin",
                ),
            ),
        ):
            data, method, _ = geeknews.extract_original("https://example.com/x", "X")

        self.assertIsNone(data)
        self.assertEqual(method, "failed")


if __name__ == "__main__":
    unittest.main()
