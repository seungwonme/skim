"""LinkedIn/Reddit 댓글 수집에서 "실패"와 "보여줄 댓글 없음"을 구분하는지 검증한다 (#61).

fetch_comment_section은 요청·파싱 실패에 None, 응답은 정상인데 보여줄 댓글이 없으면
빈 문자열을 돌려준다. 네트워크는 쓰지 않는다.
"""

import unittest
from unittest.mock import MagicMock, patch

from skim_core.crawlers.api import linkedin as linkedin_mod
from skim_core.crawlers.api import reddit as reddit_mod
from skim_core.crawlers.api.linkedin import LinkedInAPICrawler
from skim_core.crawlers.api.reddit import (
    MAX_CONSECUTIVE_COMMENT_FAILURES,
    RedditAPICrawler,
)
from skim_core.models import Post


def _post(**overrides):
    data = {
        "platform": "reddit",
        "author": "tester",
        "content": "original body",
        "timestamp": "2026-08-10T00:00:00+09:00",
        "url": "https://www.reddit.com/r/python/comments/abc/title/",
        "external_id": "7492224454731714560",
        "comments": 1,
    }
    data.update(overrides)
    return Post(**data)


def _reddit_payload(*bodies):
    children = [
        {"kind": "t1", "data": {"author": "u", "body": body, "score": 1}}
        for body in bodies
    ]
    return [{"data": {"children": []}}, {"data": {"children": children}}]


def _linkedin_response(included, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = {"included": included}
    return response


def _reddit_crawler():
    with patch.object(RedditAPICrawler, "_load_session_cookies", return_value=None):
        return RedditAPICrawler()


def _linkedin_crawler():
    with patch.object(LinkedInAPICrawler, "_load_session_cookies", return_value=None):
        return LinkedInAPICrawler()


class RedditFetchTests(unittest.TestCase):
    def _fetch(self, payload=None, error=None):
        crawler = _reddit_crawler()
        with patch.object(
            crawler, "fetch_listing_page", return_value=payload, side_effect=error
        ):
            return crawler.fetch_comment_section(_post().url)

    def test_only_deleted_comments_is_empty_string(self):
        self.assertEqual(self._fetch(_reddit_payload("[deleted]", "[removed]")), "")

    def test_no_comment_children_is_empty_string(self):
        self.assertEqual(self._fetch(_reddit_payload()), "")

    def test_visible_comment_renders_section(self):
        section = self._fetch(_reddit_payload("hello"))
        self.assertIn("## Reddit Comments", section)

    def test_request_error_is_none(self):
        self.assertIsNone(self._fetch(error=OSError("reset")))

    def test_unexpected_payload_shape_is_none(self):
        self.assertIsNone(self._fetch(payload={"not": "a list"}))


class RedditAttachTests(unittest.TestCase):
    def _run(self, results, posts):
        crawler = _reddit_crawler()
        with (
            patch.object(crawler, "fetch_comment_section", side_effect=results),
            patch.object(reddit_mod.time, "sleep"),
            patch.object(reddit_mod.typer, "echo") as echo,
        ):
            crawler.attach_comments(posts)
        return [call.args[0] for call in echo.call_args_list]

    def test_empty_is_logged_separately_from_failure(self):
        posts = [_post(external_id=str(i)) for i in range(3)]
        lines = self._run(["", None, "## Reddit Comments\n\n- **u/a**: hi"], posts)

        self.assertTrue(any("수집 실패 1건 (본문만 저장)" in line for line in lines))
        self.assertTrue(any("보여줄 댓글 없음 1건" in line for line in lines))
        # 빈 응답은 섹션 없이 본문만, 실패는 content_markdown을 건드리지 않는다.
        self.assertEqual(posts[0].content_markdown, "original body")
        self.assertIsNone(posts[1].content_markdown)
        self.assertIn("Reddit Comments", posts[2].content_markdown)

    def test_only_empty_logs_no_failure_line(self):
        lines = self._run(["", ""], [_post(external_id="a"), _post(external_id="b")])

        self.assertFalse(any("수집 실패" in line for line in lines))
        self.assertTrue(any("보여줄 댓글 없음 2건" in line for line in lines))

    def test_empty_responses_do_not_trip_the_circuit_breaker(self):
        n = MAX_CONSECUTIVE_COMMENT_FAILURES + 2
        posts = [_post(external_id=str(i)) for i in range(n)]
        lines = self._run([""] * n, posts)

        self.assertFalse(any("중단" in line for line in lines))
        self.assertTrue(any(f"보여줄 댓글 없음 {n}건" in line for line in lines))

    def test_empty_resets_the_failure_streak(self):
        # 실패 2건 -> 빈 응답 -> 실패 2건은 연속 3건이 아니므로 끝까지 간다.
        results = [None, None, "", None, None, "## Reddit Comments\n\n- **u/a**: hi"]
        posts = [_post(external_id=str(i)) for i in range(len(results))]
        lines = self._run(results, posts)

        self.assertFalse(any("중단" in line for line in lines))
        self.assertIn("Reddit Comments", posts[-1].content_markdown)

    def test_real_failures_still_trip_the_circuit_breaker(self):
        n = MAX_CONSECUTIVE_COMMENT_FAILURES + 2
        posts = [_post(external_id=str(i)) for i in range(n)]
        lines = self._run([None] * n, posts)

        self.assertTrue(any("중단" in line for line in lines))
        for post in posts:
            self.assertEqual(post.content, "original body")


class LinkedInFetchTests(unittest.TestCase):
    def _fetch(self, response=None, error=None):
        crawler = _linkedin_crawler()
        with patch.object(
            crawler.session, "get", return_value=response, side_effect=error
        ):
            return crawler.fetch_comment_section("7492224454731714560")

    @staticmethod
    def _comment(text="", parent=None):
        entry = {
            "$type": "com.linkedin.voyager.feed.Comment",
            "commentV2": {"text": text},
        }
        if parent:
            entry["parentCommentUrn"] = parent
        return entry

    def test_reply_only_is_empty_string(self):
        response = _linkedin_response([self._comment("a reply", parent="urn:x")])
        self.assertEqual(self._fetch(response), "")

    def test_blank_text_comment_is_empty_string(self):
        self.assertEqual(self._fetch(_linkedin_response([self._comment("")])), "")

    def test_no_included_is_empty_string(self):
        self.assertEqual(self._fetch(_linkedin_response([])), "")

    def test_visible_comment_renders_section(self):
        section = self._fetch(_linkedin_response([self._comment("nice post")]))
        self.assertIn("## LinkedIn Comments", section)

    def test_request_error_is_none(self):
        self.assertIsNone(self._fetch(error=OSError("reset")))

    def test_non_200_is_none(self):
        self.assertIsNone(self._fetch(_linkedin_response([], status=403)))

    def test_invalid_json_is_none(self):
        response = MagicMock(status_code=200)
        response.json.side_effect = ValueError("not json")
        self.assertIsNone(self._fetch(response))


class LinkedInAttachTests(unittest.TestCase):
    def test_empty_is_logged_separately_from_failure(self):
        crawler = _linkedin_crawler()
        posts = [_post(platform="linkedin", external_id=str(i)) for i in range(3)]
        with (
            patch.object(
                crawler,
                "fetch_comment_section",
                side_effect=["", None, "## LinkedIn Comments\n\n- **a**: hi"],
            ),
            patch.object(linkedin_mod.time, "sleep"),
            patch.object(linkedin_mod.typer, "echo") as echo,
        ):
            crawler.attach_comments(posts)

        lines = [call.args[0] for call in echo.call_args_list]
        self.assertTrue(any("수집 실패 1건 (본문만 저장)" in line for line in lines))
        self.assertTrue(any("보여줄 댓글 없음 1건" in line for line in lines))
        self.assertEqual(posts[0].content, "original body")
        self.assertIn("LinkedIn Comments", posts[2].content_markdown)


if __name__ == "__main__":
    unittest.main()
