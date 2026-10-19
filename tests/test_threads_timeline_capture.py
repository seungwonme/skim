"""브라우저가 받은 피드 응답을 크롤러가 놓치지 않는지 확인한다.

2026-10 초에 웹앱이 타임라인 쿼리를 `/graphql/query`에서 `/api/graphql`로 옮겼다.
응답을 URL 경로로 거르던 크롤러는 전부 버렸고, 세션이 살아 있는데도 "타임라인이
비어 있습니다"로 이틀 연속 0건이 났다 (#74).

2026-10-20부터 타임라인, 프로필 피드, 답글을 전용 프로필 브라우저 하나에서 받는다 (#105).
"""

import asyncio
import contextlib
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from skim_core.crawlers.api import threads as threads_module
from skim_core.crawlers.api.threads import (
    PROFILE_QUERY_NAME,
    TIMELINE_QUERY_NAME,
    ThreadsAPICrawler,
    profile_handle,
    profile_threads,
    request_query_name,
    timeline_threads,
)


def _form(name):
    return urlencode(
        {"fb_api_caller_class": "RelayModern", "fb_api_req_friendly_name": name}
    )


def make_post(username="alice", text="hello", code="ABC"):
    return {
        "user": {"username": username},
        "caption": {"text": text},
        "taken_at": 1786014965,
        "code": code,
        "like_count": 7,
        "text_post_app_info": {"direct_reply_count": 2, "repost_count": 1},
    }


class RequestQueryNameTests(unittest.TestCase):
    def test_reads_form_field_without_looking_at_path(self):
        self.assertEqual(
            request_query_name(_form(TIMELINE_QUERY_NAME), {}), TIMELINE_QUERY_NAME
        )

    def test_reads_friendly_name_header(self):
        headers = {"x-fb-friendly-name": PROFILE_QUERY_NAME}
        self.assertEqual(request_query_name(None, headers), PROFILE_QUERY_NAME)

    def test_query_that_only_shares_the_prefix_is_another_query(self):
        name = request_query_name(_form(TIMELINE_QUERY_NAME + "Extra"), {})
        self.assertNotEqual(name, TIMELINE_QUERY_NAME)

    def test_request_without_body_has_no_name(self):
        self.assertIsNone(request_query_name(None, {}))


class FeedPayloadTests(unittest.TestCase):
    def test_timeline_reads_aliased_root_field_and_skips_suggestions(self):
        # 2026-10 응답은 루트 필드를 `feedData` 별칭으로 싣는다.
        payload = {
            "data": {
                "feedData": {
                    "edges": [
                        {"node": {"text_post_app_thread": {"id": "a"}}},
                        {"node": {"suggested_users": {}}},
                        {"node": {"text_post_app_thread": {"id": "b"}}},
                    ]
                }
            }
        }
        self.assertEqual(timeline_threads(payload), [{"id": "a"}, {"id": "b"}])

    def test_error_payload_has_no_threads(self):
        self.assertEqual(timeline_threads({"error": 1357054}), [])
        self.assertEqual(profile_threads({"data": {}}), [])

    def test_profile_edges_are_threads_themselves(self):
        # 2026-10-20 프로필 페이지 스크롤 응답: data.mediaData.edges[].node가 스레드다.
        thread = {"thread_items": [{"post": make_post(username="bob")}]}
        payload = {
            "data": {
                "mediaData": {
                    "edges": [{"node": thread}, {"node": {"__typename": "Other"}}]
                }
            }
        }

        threads = profile_threads(payload)

        self.assertEqual(threads, [thread])
        post = ThreadsAPICrawler.__new__(ThreadsAPICrawler)._parse_thread(threads[0])
        self.assertEqual(post.author, "bob")


class ProfileHandleTests(unittest.TestCase):
    def test_accepts_name_with_or_without_at(self):
        self.assertEqual(profile_handle("@zuck"), "zuck")
        self.assertEqual(profile_handle("zuck"), "zuck")

    def test_numeric_id_cannot_open_a_profile_page(self):
        with self.assertRaises(ValueError):
            profile_handle("314216")


class FakeRequest:
    def __init__(self, name):
        self.post_data = _form(name)
        self.headers = {}


class FakeResponse:
    def __init__(self, name, payload, url="https://www.threads.com/api/graphql"):
        self.url = url
        self.request = FakeRequest(name)
        self._text = "for (;;);" + json.dumps(payload)

    async def text(self):
        return self._text


class FakeMouse:
    def __init__(self, page):
        self._page = page

    async def wheel(self, _x, _y):
        self._page.scrolls += 1
        self._page.emit()


class FakePage:
    """스크롤할 때마다 batches의 다음 묶음을 응답으로 내보내는 페이지 대역."""

    def __init__(self, batches):
        self._batches = list(batches)
        self._handler = None
        self.mouse = FakeMouse(self)
        self.visited = []
        self.scrolls = 0
        self.closed = False

    def on(self, event, handler):
        assert event == "response"
        self._handler = handler

    def emit(self):
        if self._batches:
            for response in self._batches.pop(0):
                self._handler(response)

    async def goto(self, url, **_kwargs):
        self.visited.append(url)

    async def wait_for_timeout(self, _ms):
        # 핸들러가 만든 태스크가 돌 기회를 준다.
        for _ in range(5):
            await asyncio.sleep(0)

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pages):
        self._pages = list(pages)
        self.opened = []

    async def new_page(self):
        page = self._pages.pop(0)
        self.opened.append(page)
        return page


def _timeline_payload(*codes):
    return {
        "data": {
            "feedData": {
                "edges": [
                    {
                        "node": {
                            "text_post_app_thread": {
                                "thread_items": [{"post": make_post(code=code)}]
                            }
                        }
                    }
                    for code in codes
                ]
            }
        }
    }


class CaptureThreadsTests(unittest.TestCase):
    def _capture(self, page, url, name, extract, count):
        crawler = ThreadsAPICrawler.__new__(ThreadsAPICrawler)
        crawler._context = FakeContext([page])
        with patch.object(threads_module.typer, "echo") as echo:
            threads = asyncio.run(crawler._capture_threads(url, name, extract, count))
        messages = " ".join(str(c.args[0]) for c in echo.call_args_list if c.args)
        return threads, messages

    def test_reads_only_the_feed_query_and_stops_when_full(self):
        page = FakePage(
            [
                [
                    FakeResponse("BarcelonaSideNavigationFeedsListQuery", {"data": {}}),
                    FakeResponse(TIMELINE_QUERY_NAME, _timeline_payload("A", "B")),
                ],
                [FakeResponse(TIMELINE_QUERY_NAME, _timeline_payload("C"))],
            ]
        )

        threads, _ = self._capture(
            page, "https://www.threads.com/", TIMELINE_QUERY_NAME, timeline_threads, 2
        )

        self.assertEqual(len(threads), 2)
        self.assertEqual(page.scrolls, 1, "2건을 채우면 더 스크롤하지 않는다")
        self.assertTrue(page.closed)

    def test_no_feed_response_is_reported(self):
        page = FakePage([])

        threads, messages = self._capture(
            page, "https://www.threads.com/", TIMELINE_QUERY_NAME, timeline_threads, 5
        )

        self.assertEqual(threads, [])
        self.assertIn("응답이 없었습니다", messages)
        self.assertEqual(page.scrolls, threads_module.MAX_TIMELINE_SCROLLS)


class CrawlBrowserTests(unittest.TestCase):
    def _fake_open_profile(self, context, opened):
        @contextlib.asynccontextmanager
        async def fake():
            opened.append(True)
            yield context

        return fake

    def test_profile_feed_opens_the_profile_page_in_one_browser(self):
        thread = {"thread_items": [{"post": make_post(username="zuck")}]}
        page = FakePage(
            [
                [
                    FakeResponse(
                        PROFILE_QUERY_NAME,
                        {"data": {"mediaData": {"edges": [{"node": thread}]}}},
                    )
                ]
            ]
        )
        context = FakeContext([page])
        opened = []
        crawler = ThreadsAPICrawler()

        with (
            patch.object(
                threads_module,
                "open_profile",
                self._fake_open_profile(context, opened),
            ),
            patch.object(threads_module.typer, "echo"),
        ):
            posts = asyncio.run(
                crawler.crawl(count=1, user_id="@zuck", no_content=True)
            )

        self.assertEqual(opened, [True])
        self.assertEqual(page.visited, ["https://www.threads.com/@zuck"])
        self.assertEqual([p.author for p in posts], ["zuck"])
        self.assertIsNone(crawler._context, "크롤이 끝나면 컨텍스트를 놓는다")

    def test_numeric_user_id_fails_before_starting_chrome(self):
        opened = []
        with (
            patch.object(
                threads_module,
                "open_profile",
                self._fake_open_profile(FakeContext([]), opened),
            ),
            self.assertRaises(ValueError),
        ):
            asyncio.run(ThreadsAPICrawler().crawl(count=1, user_id="314216"))

        self.assertEqual(opened, [])

    def test_replies_use_a_tab_in_the_same_browser(self):
        page = FakePage([])

        async def evaluate(_script, _args):
            return [{"data": {}}]

        page.evaluate = evaluate
        crawler = ThreadsAPICrawler.__new__(ThreadsAPICrawler)
        crawler._context = FakeContext([page])

        async def ready(_page, _urls):
            return "ok"

        async def run():
            with patch.object(
                ThreadsAPICrawler, "_boot_first_ready", staticmethod(ready)
            ):
                async with crawler._reply_fetcher([]) as fetch:
                    return await fetch("1", None)

        self.assertEqual(asyncio.run(run()), [{"data": {}}])
        self.assertTrue(page.closed, "답글용 탭은 닫고 브라우저는 크롤이 닫는다")


if __name__ == "__main__":
    unittest.main()
