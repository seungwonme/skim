"""GeekNews 403 회귀 테스트.

news.hada.io는 OS 괄호만 있고 브라우저 토큰이 없는 User-Agent를 403으로 막는다
("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"). 지표 백필이 연속 5건 실패로
중단됐던 원인이고, 홈페이지 스크래핑(`--count` 경로)도 같은 문자열을 쓰고 있었다.
"""

import unittest
from unittest.mock import patch

from skim_core.crawlers.feed.geeknews import fetch_geeknews_metrics
from skim_core.feed_utils import FEED_HEADERS


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


if __name__ == "__main__":
    unittest.main()
