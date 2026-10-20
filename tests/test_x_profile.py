"""X를 전용 프로필 브라우저에서 받는 경로를 확인한다 (#105 3단계).

2026-10-20부터 요청을 직접 만들지 않고, 웹앱이 홈과 트윗 페이지를 열 때 스스로 보내는
HomeTimeline, UserTweets, TweetDetail 응답을 읽는다. 스레드인지, 답글을 채울 단독 트윗인지
고르는 기준은 `_parse_tweets` 한 곳에 두고, 같은 함수를 계획 단계로 한 번 더 돌려 열
트윗 페이지를 정한다.
"""

import asyncio
import unittest
from unittest.mock import patch

from playwright.async_api import TimeoutError as PlaywrightTimeout

from skim_core.crawlers.api import x as x_module
from skim_core.crawlers.api.x import (
    DETAIL_QUERY,
    OPEN_STATUS_SCRIPT,
    TIMELINE_QUERY,
    XAPICrawler,
    graphql_query_name,
    login_wall,
    screen_name_from,
)

GRAPHQL = "https://x.com/i/api/graphql/abc123"


def tweet(
    tweet_id,
    text,
    conv_id,
    reply_to_user=None,
    author_id="A",
    screen="aiden",
    replies=0,
):
    return {
        "legacy": {
            "full_text": text,
            "id_str": tweet_id,
            "conversation_id_str": conv_id,
            "created_at": "Wed Oct 10 20:19:24 +0000 2018",
            "in_reply_to_status_id_str": conv_id if reply_to_user else None,
            "in_reply_to_user_id_str": reply_to_user,
            "reply_count": replies,
            "favorite_count": 1,
        },
        "core": {
            "user_results": {
                "result": {"rest_id": author_id, "legacy": {"screen_name": screen}}
            }
        },
    }


def timeline(*tweets):
    return {"data": {"entries": [{"content": t} for t in tweets]}}


def crawler():
    instance = XAPICrawler.__new__(XAPICrawler)
    instance.debug_mode = False
    instance.platform_name = "X"
    return instance


class FakeResponse:
    def __init__(self, url, body):
        self.url = url
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeExpect:
    """웹앱이 지금 주소의 트윗에 대해 TweetDetail을 보냈는지 흉내 낸다."""

    def __init__(self, page):
        self.page = page
        self.value = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if exc[0] is not None:
            return False
        body = self.page.details.get(self.page.url.rsplit("/", 1)[-1])
        if body is None:
            raise PlaywrightTimeout("TweetDetail 응답 없음")
        future = asyncio.get_running_loop().create_future()
        future.set_result(FakeResponse(f"{GRAPHQL}/{DETAIL_QUERY}", body))
        self.value = future
        return False


class FakePage:
    """goto하면 지정한 주소로 넘어가고, 등록된 응답을 핸들러에 흘려 보낸다."""

    def __init__(self, landing=None, bodies=None, details=None, in_app_works=True):
        self.url = "about:blank"
        self.landing = landing
        self.bodies = list(bodies or [])
        self.details = details or {}
        self.in_app_works = in_app_works
        self.handlers = []
        self.visited = []
        self.scrolls = 0
        page = self

        class Mouse:
            async def wheel(self, _x, _y):
                page.scrolls += 1
                page._emit()

        self.mouse = Mouse()

    def on(self, event, handler):
        assert event == "response"
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        assert event == "response"
        self.handlers.remove(handler)

    def _emit(self):
        if self.bodies:
            response = self.bodies.pop(0)
            for handler in self.handlers:
                handler(response)

    async def goto(self, url, **_):
        self.visited.append(("goto", url))
        self.url = self.landing or url
        self._emit()

    async def evaluate(self, script, conv_id):
        assert script == OPEN_STATUS_SCRIPT
        self.visited.append(("in_app", conv_id))
        if self.in_app_works:
            self.url = f"https://x.com/i/status/{conv_id}"

    async def wait_for_timeout(self, _ms):
        for _ in range(3):
            await asyncio.sleep(0)

    def expect_response(self, predicate, timeout):
        assert predicate(FakeResponse(f"{GRAPHQL}/{DETAIL_QUERY}?v=1", None))
        assert not predicate(FakeResponse(f"{GRAPHQL}/{TIMELINE_QUERY}", None))
        assert timeout > 0
        return FakeExpect(self)


class HelperTests(unittest.TestCase):
    def test_query_name_is_the_last_graphql_path_segment(self):
        url = f"{GRAPHQL}/HomeTimeline?variables=%7B%7D"
        self.assertEqual(graphql_query_name(url), "HomeTimeline")

    def test_non_graphql_url_has_no_query_name(self):
        self.assertEqual(graphql_query_name("https://x.com/i/api/1.1/jot"), "")

    def test_login_flow_is_a_wall(self):
        url = "https://x.com/i/flow/login?redirect_after_login=%2Fhome"
        self.assertEqual(login_wall(url), "/i/flow/login")

    def test_home_is_not_a_wall(self):
        self.assertIsNone(login_wall("https://x.com/home"))

    def test_screen_name_comes_from_the_account_menu_text(self):
        self.assertEqual(screen_name_from("Aiden\n@seungwon_me"), "seungwon_me")
        self.assertEqual(screen_name_from("Aiden"), "")


class CaptureTests(unittest.TestCase):
    def test_keeps_only_the_wanted_query_and_scrolls_until_enough(self):
        first = timeline(tweet("1", "하나", "1"))
        second = timeline(tweet("2", "둘", "2"))
        page = FakePage(
            bodies=[
                FakeResponse(f"{GRAPHQL}/{TIMELINE_QUERY}", first),
                FakeResponse(f"{GRAPHQL}/UserByScreenName", {"data": {}}),
                FakeResponse(f"{GRAPHQL}/{TIMELINE_QUERY}", second),
            ]
        )
        bodies = asyncio.run(
            crawler()._capture(page, "https://x.com/home", TIMELINE_QUERY, count=1)
        )

        self.assertEqual(bodies, [first, second])
        self.assertEqual(page.scrolls, 2)
        # 같은 탭에서 트윗을 열 때 늦게 온 타임라인 응답이 목록을 바꾸지 않는다.
        self.assertEqual(page.handlers, [])

    def test_login_wall_returns_nothing(self):
        page = FakePage(landing="https://x.com/i/flow/login?redirect=%2Fhome")
        with patch.object(x_module.typer, "echo") as echo:
            bodies = asyncio.run(
                crawler()._capture(page, "https://x.com/home", TIMELINE_QUERY, count=5)
            )

        self.assertEqual(bodies, [])
        self.assertEqual(page.scrolls, 0)
        self.assertIn("skim login x", " ".join(str(c) for c in echo.call_args_list))


class DetailPlanTests(unittest.TestCase):
    def setUp(self):
        self.raw = [
            timeline(
                tweet("10", "스레드 첫 글", "10"),
                tweet("11", "스레드 둘째", "10", reply_to_user="A"),
                tweet("20", "반응 많은 글", "20", replies=5),
                tweet("30", "조용한 글", "30", replies=1),
            )
        ]

    def test_plan_lists_threads_and_busy_standalone_tweets(self):
        instance = crawler()

        self.assertEqual(instance._detail_targets(self.raw, 10), ["10", "20"])
        self.assertIsNone(instance._wanted)

    def test_no_content_skips_standalone_reply_fetches(self):
        instance = crawler()
        instance._skip_extra_reply_fetch = True

        self.assertEqual(instance._detail_targets(self.raw, 10), ["10"])

    def test_tweet_repeated_in_a_later_page_is_kept_once(self):
        # 2026-10-20 50건 실측에서 1번째 글이 35번째로 다시 나왔다.
        repeated = self.raw + [timeline(tweet("30", "조용한 글", "30", replies=1))]

        posts = crawler()._parse_tweets(repeated, 10)

        self.assertEqual([p.external_id for p in posts], ["10", "20", "30"])

    def test_parse_uses_prefetched_details(self):
        instance = crawler()
        instance._details = {
            # 타임라인에는 둘째까지만 왔지만 트윗 페이지에는 셋째와 남의 답글이 있다.
            "10": [
                tweet("10", "스레드 첫 글", "10"),
                tweet("11", "스레드 둘째", "10", reply_to_user="A"),
                tweet("12", "스레드 셋째", "10", reply_to_user="A"),
                tweet("13", "좋은 글", "10", "A", author_id="B", screen="bob"),
            ],
            "20": [
                tweet("20", "반응 많은 글", "20", replies=5),
                tweet("21", "동의합니다", "20", "A", author_id="C", screen="carol"),
            ],
        }

        posts = instance._parse_tweets(self.raw, 10)

        self.assertEqual([p.external_id for p in posts], ["10", "20", "30"])
        self.assertIn("스레드 셋째", posts[0].content)
        self.assertIn("## X Replies", posts[0].content_markdown)
        self.assertIn("@bob", posts[0].content_markdown)
        self.assertIn("@carol", posts[1].content_markdown)
        self.assertNotIn("## X Replies", posts[2].content_markdown or "")


class FetchDetailsTests(unittest.TestCase):
    def fetch(self, page, conv_ids):
        with (
            patch.object(x_module.asyncio, "sleep", side_effect=_no_sleep),
            patch.object(x_module.typer, "echo") as echo,
        ):
            details = asyncio.run(crawler()._fetch_details(page, conv_ids))
        return details, " ".join(str(c) for c in echo.call_args_list)

    def test_opens_tweets_inside_the_web_app_and_counts_failures(self):
        page = FakePage(
            details={
                "10": timeline(tweet("10", "첫 글", "10")),
                # 지워진 트윗도 TweetDetail은 온다. 트윗이 없을 뿐이다.
                "20": {"data": {}},
            }
        )

        details, log = self.fetch(page, ["10", "20"])

        self.assertEqual(list(details), ["10"])
        self.assertEqual(page.visited, [("in_app", "10"), ("in_app", "20")])
        self.assertIn("실패 1건", log)
        self.assertNotIn("새로 받아", log)

    def test_falls_back_to_page_loads_when_the_web_app_ignores_the_move(self):
        page = FakePage(
            details={
                "10": timeline(tweet("10", "첫 글", "10")),
                "20": timeline(tweet("20", "둘째 글", "20")),
            },
            in_app_works=False,
        )

        details, log = self.fetch(page, ["10", "20"])

        self.assertEqual(list(details), ["10", "20"])
        # 한 번 실패하면 남은 트윗은 웹앱 안 이동을 다시 시도하지 않는다.
        self.assertEqual(
            page.visited,
            [
                ("in_app", "10"),
                ("goto", "https://x.com/i/status/10"),
                ("goto", "https://x.com/i/status/20"),
            ],
        )
        self.assertIn("새로 받아", log)

    def test_no_targets_opens_nothing(self):
        page = FakePage()

        self.assertEqual(self.fetch(page, [])[0], {})
        self.assertEqual(page.visited, [])


_real_sleep = asyncio.sleep


async def _no_sleep(_seconds):
    await _real_sleep(0)


if __name__ == "__main__":
    unittest.main()
