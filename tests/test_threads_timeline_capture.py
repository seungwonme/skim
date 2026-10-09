"""브라우저가 받은 타임라인 응답을 크롤러가 놓치지 않는지 확인한다.

2026-10 초에 웹앱이 타임라인 쿼리를 `/graphql/query`에서 `/api/graphql`로 옮겼다.
응답을 URL 경로로 거르던 크롤러는 전부 버렸고, 세션이 살아 있는데도 "타임라인이
비어 있습니다"로 이틀 연속 0건이 났다 (#74).
"""

import unittest
from urllib.parse import urlencode

from skim_core.crawlers.api.threads import (
    TIMELINE_QUERY,
    is_timeline_request,
    timeline_threads,
)

TIMELINE_FORM = urlencode(
    {
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": TIMELINE_QUERY["friendly_name"],
        "doc_id": "123",
    }
)


class IsTimelineRequestTests(unittest.TestCase):
    def test_matches_form_field_without_looking_at_path(self):
        self.assertTrue(is_timeline_request(TIMELINE_FORM, {}))

    def test_matches_friendly_name_header(self):
        headers = {"x-fb-friendly-name": TIMELINE_QUERY["friendly_name"]}
        self.assertTrue(is_timeline_request(None, headers))

    def test_rejects_other_query(self):
        form = urlencode(
            {"fb_api_req_friendly_name": "BarcelonaSideNavigationFeedsListQuery"}
        )
        self.assertFalse(is_timeline_request(form, {}))

    def test_rejects_query_that_only_shares_the_prefix(self):
        form = urlencode(
            {"fb_api_req_friendly_name": TIMELINE_QUERY["friendly_name"] + "Extra"}
        )
        self.assertFalse(is_timeline_request(form, {}))

    def test_rejects_request_without_body(self):
        self.assertFalse(is_timeline_request(None, {}))


class TimelineThreadsTests(unittest.TestCase):
    def test_reads_aliased_root_field(self):
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

    def test_returns_empty_for_error_payload(self):
        self.assertEqual(timeline_threads({"error": 1357054}), [])
        self.assertEqual(timeline_threads({"data": {}}), [])


if __name__ == "__main__":
    unittest.main()
