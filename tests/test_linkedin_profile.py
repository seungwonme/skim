"""LinkedIn을 전용 프로필 브라우저 안에서 받는지 확인한다 (#105).

피드 페이지를 한 번 열고 Voyager는 그 페이지 안에서 부른다. 쿠키 파일은 읽지 않는다.
"""

import asyncio
import contextlib
import json
import unittest
from unittest.mock import patch

from skim_core.crawlers.api import linkedin as linkedin_module
from skim_core.crawlers.api.linkedin import (
    LINKEDIN_FEED_PAGE,
    VOYAGER_FEED_URL,
    LinkedInAPICrawler,
    VoyagerResponse,
    signed_out_reason,
    voyager_get,
)
from skim_core.crawlers.auth import cdp


class FakePage:
    def __init__(self, landed=LINKEDIN_FEED_PAGE, answers=()):
        self.url = ""
        self._landed = landed
        self._answers = list(answers)
        self.fetched = []
        self.closed = False

    async def goto(self, url, **_kwargs):
        self.url = self._landed if url == LINKEDIN_FEED_PAGE else url

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, _script, url):
        self.fetched.append(url)
        return self._answers.pop(0)

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


def _fake_open_profile(context, opened):
    @contextlib.asynccontextmanager
    async def fake():
        opened.append(True)
        yield context

    return fake


class SignedOutReasonTests(unittest.TestCase):
    def test_feed_is_signed_in(self):
        self.assertIsNone(signed_out_reason("https://www.linkedin.com/feed/"))

    def test_login_screens_are_classified(self):
        cases = {
            "https://www.linkedin.com/login/?session_redirect=x": "login",
            "https://www.linkedin.com/authwall?trk=x": "authwall",
            "https://www.linkedin.com/checkpoint/challenge/abc": "checkpoint",
        }
        for url, expected in cases.items():
            self.assertEqual(signed_out_reason(url), expected, msg=url)


class VoyagerGetTests(unittest.TestCase):
    def test_sends_params_from_inside_the_page(self):
        page = FakePage(answers=[{"status": 200, "url": "u", "text": "{}"}])

        response = asyncio.run(
            voyager_get(page, VOYAGER_FEED_URL, {"q": "chronFeed", "count": "5"})
        )

        self.assertEqual(page.fetched, [f"{VOYAGER_FEED_URL}?q=chronFeed&count=5"])
        self.assertEqual((response.status, response.redirected), (200, False))


class CrawlTests(unittest.TestCase):
    def _crawl(self, page, **options):
        opened = []
        crawler = LinkedInAPICrawler()
        with (
            patch.object(
                linkedin_module,
                "open_profile",
                _fake_open_profile(FakeContext(page), opened),
            ),
            patch.object(linkedin_module.typer, "echo") as echo,
        ):
            posts = asyncio.run(crawler.crawl(**options))
        messages = " ".join(str(c.args[0]) for c in echo.call_args_list if c.args)
        return posts, opened, messages, crawler

    def test_login_screen_stops_before_any_voyager_request(self):
        page = FakePage(landed="https://www.linkedin.com/login/?session_redirect=x")

        posts, opened, messages, _ = self._crawl(page, count=5)

        self.assertEqual(posts, [])
        self.assertEqual(opened, [True])
        self.assertEqual(page.fetched, [], "로그인 화면에서는 Voyager를 부르지 않는다")
        self.assertIn("skim login linkedin", messages)
        self.assertTrue(page.closed)

    def test_feed_is_read_in_the_same_page(self):
        feed = {
            "included": [
                {
                    "$type": "com.linkedin.voyager.dash.feed.Update",
                    "entityUrn": "urn:li:fsd_update:(urn:li:activity:111,MAIN_FEED)",
                    "actor": {"name": {"text": "Aiden"}},
                    "commentary": {"text": {"text": "LinkedIn body text here"}},
                    "metadata": {"backendUrn": "urn:li:activity:111"},
                }
            ]
        }
        page = FakePage(
            answers=[{"status": 200, "url": VOYAGER_FEED_URL, "text": json.dumps(feed)}]
        )

        posts, opened, _, crawler = self._crawl(page, count=1, no_content=True)

        self.assertEqual(opened, [True])
        self.assertEqual(len(posts), 1)
        self.assertTrue(page.fetched[0].startswith(VOYAGER_FEED_URL))
        self.assertIsNone(crawler._page, "크롤이 끝나면 페이지를 놓는다")

    def test_feed_redirect_is_reported_as_expired_session(self):
        crawler = LinkedInAPICrawler()
        crawler._page = FakePage(
            answers=[
                {
                    "status": 200,
                    "url": "https://www.linkedin.com/uas/login",
                    "redirected": True,
                    "text": "<html>",
                }
            ]
        )
        with patch.object(linkedin_module.typer, "echo") as echo:
            raw = asyncio.run(crawler._fetch_feed_page(start=0, count=5))

        self.assertIsNone(raw)
        messages = " ".join(str(c.args[0]) for c in echo.call_args_list)
        self.assertIn("(login)", messages)


class VoyagerResponseTests(unittest.TestCase):
    def test_non_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            VoyagerResponse(200, "u", "<html>").json()


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(json.loads(message))

    def recv(self):
        # Chrome은 Browser.close를 받으면 응답 없이 연결을 끊기도 한다.
        raise OSError("connection closed")

    def close(self):
        self.closed = True


class LoginShutdownTests(unittest.TestCase):
    """skim login이 Chrome을 정상 종료해 새 로그인 쿠키를 프로필에 남기는지."""

    def test_close_chrome_sends_browser_close(self):
        socket = FakeWebSocket()
        with patch("websocket.create_connection", return_value=socket):
            self.assertTrue(cdp.close_chrome("ws://127.0.0.1:9222/devtools/browser/x"))

        self.assertEqual([m["method"] for m in socket.sent], ["Browser.close"])
        self.assertTrue(socket.closed)

    def test_close_chrome_without_debugger_reports_not_sent(self):
        self.assertFalse(cdp.close_chrome(None))

    def test_close_chrome_that_cannot_connect_reports_not_sent(self):
        with patch("websocket.create_connection", side_effect=OSError("refused")):
            self.assertFalse(cdp.close_chrome("ws://127.0.0.1:1/devtools/browser/x"))


if __name__ == "__main__":
    unittest.main()
