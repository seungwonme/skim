"""HF Daily Papers가 큐레이션 날짜로 창을 자르는지 검증한다.

`publishedAt`은 arXiv 발행일이라 큐레이션보다 며칠에서 몇 주 앞선다. 그 값으로
`--days` 창을 자르면 오늘 올라온 논문이 통째로 걸러져 매일 0건이 나온다
(2026-08-10 실측: 목록의 publishedAt 최댓값이 5일 전이라 3일 창에서 0건).
"""

import asyncio
import html
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from skim_core.crawlers.feed import huggingface
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


class FakePage:
    """댓글 파싱용 가짜 HTML 응답."""

    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class HuggingFaceDailyWindowTests(unittest.TestCase):
    def _crawl(self, payload, since):
        with patch.object(
            huggingface._SESSION, "get", return_value=FakeResponse(payload)
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


class HuggingFaceCommentTests(unittest.TestCase):
    """HF 행은 본문을 arXiv에서 뽑아오므로 arxiv 행과 거의 같다.

    HF만 갖는 것(저자 코멘트, 데모 링크, 독자 반응)을 안 담으면 HF Daily Papers를
    따로 읽을 이유가 남지 않는다.
    """

    def _page(self, comments):
        props = html.escape(json.dumps({"comments": comments}), quote=True)
        return FakePage(f'<div data-target="PaperContent" data-props="{props}"></div>')

    def _comment(self, author, body, hidden=False, node_type="comment"):
        return {
            "type": node_type,
            "author": {"name": author},
            "createdAt": "2026-08-10T07:05:00.000Z",
            "data": {"hidden": hidden, "latest": {"raw": body}},
        }

    def test_comments_become_a_section(self):
        page = self._page([self._comment("onnookk", "this paper replaces both")])
        with patch.object(huggingface._SESSION, "get", return_value=page):
            section = huggingface.fetch_comment_section(
                "https://huggingface.co/papers/1"
            )

        self.assertIn("## Hugging Face Comments", section)
        self.assertIn("onnookk", section)
        self.assertIn("this paper replaces both", section)

    def test_hidden_and_non_comment_nodes_are_skipped(self):
        page = self._page(
            [
                self._comment("ghost", "hidden body", hidden=True),
                self._comment("bot", "review body", node_type="review"),
                self._comment("real", "kept body"),
            ]
        )
        with patch.object(huggingface._SESSION, "get", return_value=page):
            section = huggingface.fetch_comment_section(
                "https://huggingface.co/papers/1"
            )

        self.assertIn("kept body", section)
        self.assertNotIn("hidden body", section)
        self.assertNotIn("review body", section)

    def test_parse_failure_does_not_break_the_paper(self):
        with (
            patch.object(
                huggingface._SESSION, "get", return_value=FakePage("<html></html>")
            ),
            patch("skim_core.crawlers.feed.huggingface.typer.echo"),
        ):
            self.assertIsNone(
                huggingface.fetch_comment_section("https://huggingface.co/papers/1")
            )

    def test_zero_comment_papers_are_not_requested(self):
        # numComments가 목록 응답에 이미 있어 판정 비용이 0이다.
        crawler = HuggingFaceCrawler()
        items = [{"url": "https://huggingface.co/papers/1", "num_comments": 0}]
        with patch.object(
            huggingface, "fetch_comment_section"
        ) as fetch:
            crawler._attach_comments(items)
        fetch.assert_not_called()

    def test_comment_count_lands_on_the_post_field(self):
        # num_comments= 로 넣으면 Post의 extra="allow"에 흡수돼
        # db.py가 읽는 posts.comments가 항상 None이 된다.
        now = datetime.now(timezone.utc)
        payload = [_paper("Counted", now, now)]
        payload[0]["numComments"] = 7
        with patch.object(
            huggingface._SESSION, "get", return_value=FakeResponse(payload)
        ):
            posts = asyncio.run(
                HuggingFaceCrawler().crawl(
                    since=now - timedelta(days=3), no_content=True, count=50
                )
            )

        self.assertEqual(posts[0].comments, 7)


if __name__ == "__main__":
    unittest.main()
