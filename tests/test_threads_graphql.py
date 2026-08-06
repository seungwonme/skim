"""Threads GraphQL 크롤러 회귀 테스트."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skim_core.crawlers.api import threads as threads_module
from skim_core.crawlers.api.threads import ThreadsAPICrawler

SESSION_STATE = {
    "cookies": [
        {"name": "sessionid", "value": "sid", "domain": ".threads.com"},
        {"name": "csrftoken", "value": "csrf", "domain": ".threads.com"},
        {"name": "ds_user_id", "value": "42", "domain": ".threads.com"},
    ]
}


def make_post(username="alice", text="hello", code="ABC"):
    return {
        "user": {"username": username},
        "caption": {"text": text},
        "taken_at": 1786014965,
        "code": code,
        "like_count": 7,
        "text_post_app_info": {"direct_reply_count": 2, "repost_count": 1},
    }


def graphql_response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json = Mock(return_value=payload)
    return response


class ThreadsGraphQLTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        sessions = Path(self.temp_dir.name)
        (sessions / "threads_session.json").write_text(
            json.dumps(SESSION_STATE), encoding="utf-8"
        )

        patcher = patch.object(threads_module, "SESSIONS_DIR", sessions)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.crawler = ThreadsAPICrawler()
        # 토큰 조회 HTTP 호출을 건너뛴다. 파싱 계층만 검증한다.
        self.crawler._tokens = {"lsd": "lsd-token", "fb_dtsg": "dtsg-token"}

    def test_timeline_skips_suggested_user_slots(self):
        payload = {
            "data": {
                "feedData": {
                    "edges": [
                        {"node": {"text_post_app_thread": None, "suggested_users": {}}},
                        {
                            "node": {
                                "text_post_app_thread": {
                                    "thread_items": [{"post": make_post()}]
                                }
                            }
                        },
                    ],
                    "page_info": {"has_next_page": True, "end_cursor": "cursor-2"},
                }
            }
        }
        self.crawler.session.post = Mock(return_value=graphql_response(payload))

        threads, cursor = self.crawler._fetch_timeline_feed()

        self.assertEqual(len(threads), 1)
        self.assertEqual(cursor, "cursor-2")

    def test_profile_edges_are_threads_themselves(self):
        payload = {
            "data": {
                "mediaData": {
                    "edges": [
                        {
                            "node": {
                                "thread_items": [{"post": make_post(username="bob")}]
                            }
                        }
                    ],
                    "page_info": {"has_next_page": False, "end_cursor": "ignored"},
                }
            }
        }
        self.crawler.session.post = Mock(return_value=graphql_response(payload))

        threads, cursor = self.crawler._fetch_user_feed("63055343223")

        self.assertEqual(len(threads), 1)
        post = self.crawler._parse_thread(threads[0])
        self.assertEqual(post.author, "bob")
        # has_next_page가 False면 커서를 흘려보내 무한 페이지네이션을 막는다.
        self.assertIsNone(cursor)

    def test_graphql_errors_do_not_look_like_an_empty_feed(self):
        payload = {"errors": [{"message": "execution error"}], "data": {}}
        self.crawler.session.post = Mock(return_value=graphql_response(payload))

        with patch.object(threads_module.typer, "echo") as echo:
            threads, cursor = self.crawler._fetch_timeline_feed()

        self.assertEqual(threads, [])
        self.assertIsNone(cursor)
        messages = " ".join(str(call.args[0]) for call in echo.call_args_list)
        self.assertIn("doc_id", messages)

    def test_expired_session_reports_relogin(self):
        self.crawler.session.post = Mock(
            return_value=graphql_response({}, status_code=403)
        )

        with patch.object(threads_module.typer, "echo") as echo:
            threads, cursor = self.crawler._fetch_timeline_feed()

        self.assertEqual(threads, [])
        self.assertIsNone(cursor)
        messages = " ".join(str(call.args[0]) for call in echo.call_args_list)
        self.assertIn("skim login threads", messages)

    def test_request_carries_persisted_query_coordinates(self):
        payload = {"data": {"feedData": {"edges": [], "page_info": {}}}}
        post = Mock(return_value=graphql_response(payload))
        self.crawler.session.post = post

        self.crawler._fetch_timeline_feed()

        _, kwargs = post.call_args
        self.assertEqual(
            kwargs["data"]["doc_id"], threads_module.TIMELINE_QUERY["doc_id"]
        )
        self.assertEqual(kwargs["headers"]["x-csrftoken"], "csrf")
        variables = json.loads(kwargs["data"]["variables"])
        # Relay provider 플래그가 빠지면 서버가 execution error를 돌려준다.
        self.assertTrue(
            variables["__relay_internal__pv__BarcelonaIsLoggedInrelayprovider"]
        )
        self.assertEqual(variables["variant"], "for_you")


if __name__ == "__main__":
    unittest.main()
