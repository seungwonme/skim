"""HN 본문+댓글+링크 원문 합성 검증."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from skim_core.crawlers.feed.hackernews import (
    HackerNewsCrawler,
    _html_to_text,
    compose_hn_body,
    fetch_hn_discussion,
)

ALGOLIA_FIXTURE = {
    "id": 100,
    "text": "<p>Ask HN 본문입니다.</p><p>둘째 문단.</p>",
    "children": [
        {
            "author": "alice",
            "text": "<p>First comment</p>",
            "children": [
                {"author": "bob", "text": "Reply to alice", "children": []},
            ],
        },
        {"author": None, "text": "", "children": []},  # 삭제된 댓글
        {"author": "carol", "text": "Second comment", "children": []},
    ],
}


class HNDiscussionTests(unittest.TestCase):
    def test_html_to_text_splits_paragraphs(self):
        self.assertEqual(_html_to_text("<p>a</p><p>b</p>"), "a\n\nb")

    def test_fetch_hn_discussion_parses_story_text_and_comments(self):
        resp = MagicMock()
        resp.json.return_value = ALGOLIA_FIXTURE
        with patch("skim_core.crawlers.feed.hackernews.requests.get", return_value=resp):
            discussion = fetch_hn_discussion("100")

        self.assertIn("Ask HN 본문입니다.", discussion["story_text"])
        self.assertEqual(
            discussion["comments"],
            [
                "- **alice**: First comment",
                "  - **bob**: Reply to alice",
                "- **carol**: Second comment",
            ],
        )

    def test_fetch_hn_discussion_returns_none_on_error(self):
        with patch(
            "skim_core.crawlers.feed.hackernews.requests.get",
            side_effect=Exception("boom"),
        ):
            self.assertIsNone(fetch_hn_discussion("100"))

    def test_compose_hn_body_orders_story_article_comments(self):
        discussion = {"story_text": "스토리 텍스트", "comments": ["- **a**: c1"]}
        body = compose_hn_body("기사 본문", discussion)
        self.assertEqual(
            body,
            "스토리 텍스트\n\n---\n\n기사 본문\n\n---\n\n## Hacker News 댓글\n\n- **a**: c1",
        )

    def test_compose_hn_body_empty_without_anything(self):
        self.assertEqual(compose_hn_body("", None), "")
        self.assertEqual(compose_hn_body("", {"story_text": "", "comments": []}), "")

    def test_crawl_top_stories_enriches_and_appends_discussion(self):
        crawler = HackerNewsCrawler()
        items = [
            {
                "platform": "hackernews",
                "author": "dan",
                "title": "Linked story",
                "url": "https://example.com/article",
                "published": "2026-07-03T09:00:00+09:00",
                "likes": 10,
                "num_comments": 3,
                "external_id": "item?id=100",
            },
            {
                "platform": "hackernews",
                "author": "dan",
                "title": "Ask HN: something",
                "url": "https://news.ycombinator.com/item?id=101",
                "published": "2026-07-03T09:00:00+09:00",
                "likes": 5,
                "num_comments": 1,
                "external_id": "item?id=101",
            },
        ]

        def fake_enrich(targets):
            for target in targets:
                target["content_markdown"] = "기사 본문"
                target["word_count"] = 2
            return targets

        with (
            patch.object(crawler, "_fetch_top_story_items", return_value=items),
            patch(
                "skim_core.crawlers.feed.hackernews.enrich_with_content",
                side_effect=fake_enrich,
            ) as enrich,
            patch(
                "skim_core.crawlers.feed.hackernews.fetch_hn_discussion",
                return_value={"story_text": "질문 본문", "comments": ["- **a**: c1"]},
            ),
        ):
            posts = asyncio.run(crawler.crawl(count=2))

        # HN item 페이지 URL은 원문 추출 대상에서 제외된다.
        enriched_urls = [i["url"] for i in enrich.call_args[0][0]]
        self.assertEqual(enriched_urls, ["https://example.com/article"])

        self.assertEqual(posts[0].external_id, "100")
        self.assertIn("기사 본문", posts[0].content_markdown)
        self.assertIn("## Hacker News 댓글", posts[0].content_markdown)
        self.assertEqual(posts[0].likes, 10)
        # Ask HN: 링크 원문 없이 스토리 텍스트 + 댓글로 채워진다.
        self.assertTrue(posts[1].content_markdown.startswith("질문 본문"))

    def test_crawl_no_content_skips_everything(self):
        crawler = HackerNewsCrawler()
        items = [
            {
                "platform": "hackernews",
                "author": "dan",
                "title": "Linked story",
                "url": "https://example.com/article",
                "published": "",
                "external_id": "item?id=100",
            }
        ]
        with (
            patch.object(crawler, "_fetch_top_story_items", return_value=items),
            patch("skim_core.crawlers.feed.hackernews.enrich_with_content") as enrich,
            patch("skim_core.crawlers.feed.hackernews.fetch_hn_discussion") as discussion,
        ):
            posts = asyncio.run(crawler.crawl(count=1, no_content=True))

        enrich.assert_not_called()
        discussion.assert_not_called()
        self.assertIsNone(posts[0].content_markdown)


if __name__ == "__main__":
    unittest.main()
