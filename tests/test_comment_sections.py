"""댓글 본문 합성 회귀 테스트.

`AGENTS.md`의 데이터 계약상 토론은 content_markdown에 함께 담긴다. 플랫폼마다
응답 구조가 달라 파서가 조용히 틀리기 쉬우므로, 실제 응답 모양을 픽스처로 고정한다.
"""

import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from skim_core.comments import (
    Comment,
    append_comment_section,
    render_comment_section,
)
from skim_core.crawlers.api.linkedin import LinkedInAPICrawler
from skim_core.crawlers.api.reddit import RedditAPICrawler
from skim_core.crawlers.feed.geeknews import _parse_comment_section
from skim_core.crawlers.feed.producthunt import fetch_comment_section as ph_comments


class RenderTests(unittest.TestCase):
    def test_renders_author_score_and_time(self):
        section = render_comment_section(
            "Demo Comments",
            [
                Comment(
                    author="alice", text="hello", score=3, created="2026-08-09 10:00"
                )
            ],
        )
        self.assertIn("## Demo Comments", section)
        self.assertIn("- **alice** (3 points, 2026-08-09 10:00): hello", section)

    def test_score_unit_is_configurable_and_singular(self):
        section = render_comment_section(
            "Demo", [Comment(author="a", text="t", score=1)], score_unit="like"
        )
        self.assertIn("(1 like)", section)

    def test_depth_indents_and_newlines_are_folded(self):
        section = render_comment_section(
            "Demo",
            [
                Comment(author="a", text="parent"),
                Comment(author="b", text="line1\nline2", depth=1),
            ],
        )
        # 줄바꿈이 남으면 목록 항목이 깨진다.
        self.assertIn("  - **b**: line1 line2", section)

    def test_returns_none_when_nothing_usable(self):
        self.assertIsNone(render_comment_section("Demo", []))
        self.assertIsNone(
            render_comment_section("Demo", [Comment(author="a", text="  ")])
        )

    def test_long_text_is_truncated(self):
        section = render_comment_section(
            "Demo", [Comment(author="a", text="x" * 5000)], max_chars=100
        )
        self.assertIn("...", section)
        self.assertLess(len(section), 300)

    def test_max_comments_caps_output(self):
        section = render_comment_section(
            "Demo",
            [Comment(author=f"u{i}", text=f"t{i}") for i in range(50)],
            max_comments=3,
        )
        self.assertEqual(section.count("\n- **"), 3)

    def test_append_keeps_body_and_separator(self):
        self.assertEqual(append_comment_section("body", None), "body")
        self.assertEqual(append_comment_section(None, "## S\n\n- a"), "## S\n\n- a")
        self.assertEqual(append_comment_section("body", "## S"), "body\n\n---\n\n## S")


GEEKNEWS_HTML = """
<html><body>
<div class="comment_row" style="--depth:0">
  <div class="commentinfo">
    <a href="/@princox">princox</a>
    <a href="/topic?id=1#c1"><time title="2026-08-09 17:23">14시간전</time></a>
  </div>
  <div class="commentTD"><span class="comment_contents"><p>본문 하나</p></span></div>
</div>
<div class="comment_row" style="--depth:1">
  <div class="commentinfo"><a href="/@xguru">xguru</a></div>
  <div class="commentTD"><span class="comment_contents"><p>답글</p></span></div>
</div>
</body></html>
"""


class GeekNewsCommentTests(unittest.TestCase):
    def test_parses_author_time_and_depth(self):
        section = _parse_comment_section(BeautifulSoup(GEEKNEWS_HTML, "html.parser"))
        self.assertIn("- **princox** (2026-08-09 17:23): 본문 하나", section)
        self.assertIn("  - **xguru**: 답글", section)

    def test_no_comments_returns_none(self):
        self.assertIsNone(
            _parse_comment_section(BeautifulSoup("<html></html>", "html.parser"))
        )


REDDIT_PAYLOAD = [
    {"kind": "Listing", "data": {"children": []}},
    {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t1",
                    "data": {
                        "author": "alice",
                        "body": "top comment",
                        "score": 12,
                        "created_utc": 1786000000,
                        "replies": {
                            "data": {
                                "children": [
                                    {
                                        "kind": "t1",
                                        "data": {
                                            "author": "bob",
                                            "body": "a reply",
                                            "score": 3,
                                            "created_utc": 1786000100,
                                        },
                                    }
                                ]
                            }
                        },
                    },
                },
                {
                    "kind": "t1",
                    "data": {"author": "x", "body": "[deleted]", "score": 1},
                },
                {"kind": "more", "data": {"count": 20}},
            ]
        },
    },
]


class RedditCommentTests(unittest.TestCase):
    def _crawler(self):
        with patch.object(RedditAPICrawler, "_load_session_cookies", return_value=None):
            return RedditAPICrawler()

    def test_parses_tree_and_skips_deleted_and_more(self):
        crawler = self._crawler()
        with patch.object(crawler, "fetch_listing_page", return_value=REDDIT_PAYLOAD):
            section = crawler.fetch_comment_section(
                "https://www.reddit.com/r/a/comments/b/c/"
            )

        self.assertIn("- **u/alice** (12 points,", section)
        self.assertIn("  - **u/bob** (3 points,", section)
        self.assertNotIn("[deleted]", section)

    def test_request_failure_returns_none(self):
        crawler = self._crawler()
        with patch.object(crawler, "fetch_listing_page", side_effect=Exception("boom")):
            self.assertIsNone(
                crawler.fetch_comment_section(
                    "https://www.reddit.com/r/a/comments/b/c/"
                )
            )

    def test_attach_stops_after_consecutive_failures(self):
        """세션이 죽으면 남은 게시글까지 헛 요청하지 않는다."""
        crawler = self._crawler()
        posts = [
            MagicMock(
                url=f"https://www.reddit.com/r/a/comments/{i}/x/",
                content="b",
                content_markdown=None,
            )
            for i in range(10)
        ]
        with (
            patch.object(crawler, "fetch_comment_section", return_value=None) as fetch,
            patch("skim_core.crawlers.api.reddit.time.sleep"),
        ):
            crawler.attach_comments(posts)

        self.assertEqual(fetch.call_count, 3)


LINKEDIN_PAYLOAD = {
    "included": [
        {
            "$type": "com.linkedin.voyager.feed.Comment",
            "commentV2": {"text": "top level"},
            "commenterForDashConversion": {"title": {"text": "Jane Doe"}},
            "createdTime": 1786054696447,
            "parentCommentUrn": None,
        },
        {
            "$type": "com.linkedin.voyager.feed.Comment",
            "commentV2": {"text": "a reply"},
            "commenterForDashConversion": {"title": {"text": "John Roe"}},
            "createdTime": 1786054796447,
            "parentCommentUrn": "urn:li:comment:(1,2)",
        },
        {
            "$type": "com.linkedin.voyager.identity.shared.MiniProfile",
            "lastName": "Doe",
        },
    ]
}


class LinkedInCommentTests(unittest.TestCase):
    def _crawler(self):
        with patch.object(
            LinkedInAPICrawler, "_load_session_cookies", return_value=None
        ):
            return LinkedInAPICrawler()

    def test_reads_included_and_skips_replies(self):
        crawler = self._crawler()
        response = MagicMock(status_code=200)
        response.json.return_value = LINKEDIN_PAYLOAD
        with patch.object(crawler.session, "get", return_value=response):
            section = crawler.fetch_comment_section("7492224454731714560")

        self.assertIn("- **Jane Doe**", section)
        self.assertIn("top level", section)
        # RELEVANCE 정렬이 답글을 부모보다 먼저 주기도 해서 최상위만 담는다.
        self.assertNotIn("a reply", section)

    def test_non_200_returns_none(self):
        crawler = self._crawler()
        with patch.object(
            crawler.session, "get", return_value=MagicMock(status_code=403)
        ):
            self.assertIsNone(crawler.fetch_comment_section("7492224454731714560"))


PRODUCTHUNT_HTML = """
<html><body>
<div data-test="comments-feed">
  <div data-test="comment-1">
    <a class="block"><img alt="Chirag Chopra" src="x"/></a>
    <div class="flex flex-1 flex-col">
      <div class="flex flex-row"><img alt="Workflo" src="y"/><span>Chirag Chopra Workflo Maker</span></div>
      <div>실제 본문입니다</div>
      <div><span>Upvote (2)</span><time datetime="2026-08-08T15:35:23-07:00">1d ago</time></div>
    </div>
  </div>
  <div data-test="comment-2">
    <div class="flex flex-1 flex-col">
      <div>Abdullah Javaid</div>
      <div>아바타 없는 댓글</div>
      <div><time datetime="2026-08-09T12:41:00-07:00">3h ago</time></div>
    </div>
  </div>
  <div data-test="comment-form"><span>Login to comment</span></div>
</div>
</body></html>
"""


class ProductHuntCommentTests(unittest.TestCase):
    def test_author_comes_from_avatar_not_product_logo(self):
        response = MagicMock(status_code=200, text=PRODUCTHUNT_HTML)
        response.raise_for_status.return_value = None
        with patch(
            "skim_core.crawlers.feed.producthunt.requests.get", return_value=response
        ):
            section = ph_comments("https://www.producthunt.com/products/workflo-2")

        # 헤더 안의 제품 로고(Workflo)를 작성자로 오인하면 안 된다.
        self.assertIn(
            "- **Chirag Chopra** (2026-08-08 15:35): 실제 본문입니다", section
        )
        # 아바타가 없으면 헤더 텍스트가 작성자다.
        self.assertIn("- **Abdullah Javaid**", section)
        self.assertIn("아바타 없는 댓글", section)
        # 컨테이너(comment-form)는 댓글이 아니다.
        self.assertNotIn("Login to comment", section)

    def test_non_product_url_skips_request(self):
        with patch("skim_core.crawlers.feed.producthunt.requests.get") as get:
            self.assertIsNone(ph_comments("https://example.com/x"))
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
