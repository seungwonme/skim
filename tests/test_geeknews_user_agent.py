"""GeekNews 403 회귀 테스트.

news.hada.io는 토픽 페이지에서 특정 Chrome 버전(2026-08 기준 124, 139)을 403으로
막는다. 게시글은 RSS로 들어오므로 크롤은 성공으로 끝나고 댓글·지표만 빠진다.
2026-08-29~09-22에 3주간 그렇게 유실됐고, 지표 백필은 매일 연속 5건 실패로 멈췄다.
`skim doctor`가 `probe_user_agent()`로 매일 확인한다.
"""

import unittest
from unittest.mock import patch

from skim_core.crawlers.feed.geeknews import fetch_geeknews_metrics
from skim_core.feed_utils import FEED_HEADERS, probe_user_agent


class GeekNewsUserAgentTests(unittest.TestCase):
    def test_shared_header_carries_a_browser_token(self):
        """차단을 피하려면 AppleWebKit 같은 브라우저 토큰이 있어야 한다."""
        self.assertIn("AppleWebKit", FEED_HEADERS["User-Agent"])

    def test_metrics_request_uses_the_shared_header(self):
        """geeknews 전용 UA로 갈라지면 다시 403을 맞는다."""
        with patch("skim_core.crawlers.feed.geeknews.requests.get") as get:
            get.return_value.text = "<html></html>"
            fetch_geeknews_metrics("32270")

        self.assertEqual(get.call_args.kwargs["headers"], FEED_HEADERS)

    def test_probe_reports_blocked_user_agent(self):
        """403이면 doctor 경고, 200이면 조용하다."""
        with patch("skim_core.feed_utils.requests.get") as get:
            get.return_value.status_code = 403
            self.assertIn("blocked", probe_user_agent())
            get.return_value.status_code = 200
            self.assertIsNone(probe_user_agent())

        self.assertEqual(get.call_args.kwargs["headers"], FEED_HEADERS)


if __name__ == "__main__":
    unittest.main()
