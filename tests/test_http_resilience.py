"""HTTP 재시도 공용화와 렌더 스레드 상한 회귀 테스트.

`fetch_feed`가 단발 요청이라 503 한 번에 그 소스의 그날 수집분이 빈 리스트로 끝났다.
데일리가 고정 창으로 돌아 다음 날 창에는 그 항목이 다시 안 들어오므로 영구 유실이었다.
"""

import asyncio
import time
import unittest
from unittest.mock import patch

from skim_core import enrichment, feed_utils
from skim_core.crawlers.feed import ailabs
from skim_core.feed_utils import USER_AGENT, make_retrying_session


class RetryingSessionTests(unittest.TestCase):
    def test_retries_on_throttling_and_server_errors(self):
        session = make_retrying_session()
        retry = session.get_adapter("https://example.com").max_retries

        self.assertEqual(retry.total, 3)
        for status in (429, 500, 502, 503, 504):
            self.assertIn(status, retry.status_forcelist)
        # 429의 Retry-After를 무시하면 재시도가 차단을 키운다.
        self.assertTrue(retry.respect_retry_after_header)
        self.assertGreater(retry.backoff_factor, 0)

    def test_only_idempotent_methods_are_retried(self):
        retry = make_retrying_session().get_adapter("https://example.com").max_retries
        self.assertEqual(set(retry.allowed_methods), {"GET", "HEAD"})

    def test_extra_headers_do_not_drop_the_shared_user_agent(self):
        session = make_retrying_session({"Accept": "text/html,*/*"})
        self.assertEqual(session.headers["User-Agent"], USER_AGENT)
        self.assertEqual(session.headers["Accept"], "text/html,*/*")

    def test_feed_fetch_uses_the_retrying_session(self):
        self.assertIsNotNone(
            feed_utils._FEED_SESSION.get_adapter("https://x").max_retries
        )


class UserAgentConvergenceTests(unittest.TestCase):
    """UA가 흩어져 있으면 차단선이 올라갔을 때 한 곳만 고치고 넘어가게 된다."""

    def test_every_public_fetch_path_shares_one_user_agent(self):
        self.assertEqual(feed_utils.FEED_HEADERS["User-Agent"], USER_AGENT)
        self.assertEqual(enrichment._UA_HEADERS["User-Agent"], USER_AGENT)
        self.assertEqual(enrichment._HTTP_SESSION.headers["User-Agent"], USER_AGENT)
        self.assertEqual(ailabs._SESSION.headers["User-Agent"], USER_AGENT)

    def test_user_agent_clears_the_known_block_line(self):
        # news.hada.io는 Chrome 메이저 버전을 본다. 124는 403, 128 이상은 통과(2026-08-09).
        major = int(USER_AGENT.split("Chrome/")[1].split(".")[0])
        self.assertGreaterEqual(major, 128)


class RenderJoinTimeoutTests(unittest.TestCase):
    def test_hung_render_thread_does_not_block_forever(self):
        def _hang(url, timeout_ms):  # pylint: disable=unused-argument
            time.sleep(5)
            return "<html>never</html>"

        async def _call():
            return enrichment._fetch_rendered_html(
                "https://example.com", timeout_ms=100
            )

        with (
            patch.object(enrichment, "_fetch_rendered_html_sync", _hang),
            patch.object(enrichment, "RENDER_JOIN_GRACE_SECONDS", 0.2),
            patch("builtins.print"),
        ):
            started = time.monotonic()
            result = asyncio.run(_call())
            elapsed = time.monotonic() - started

        self.assertIsNone(result, "상한을 넘긴 렌더는 포기하고 None을 돌려준다")
        self.assertLess(elapsed, 3, "join이 무기한 기다리면 크롤 프로세스가 멈춘다")


if __name__ == "__main__":
    unittest.main()
