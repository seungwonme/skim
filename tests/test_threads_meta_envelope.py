"""Meta 거부 응답이 오류로 보이는지 확인한다.

2026-09-08부터 Threads GraphQL이 `for (;;);` 봉투에 담긴 거부 사유를 돌려줬는데,
봉투를 안 벗겨서 JSONDecodeError가 났다. 그 예외가 "API 요청 실패"로만 찍혀
차단 사유가 2주간 묻혔다.
"""

import unittest

from skim_core.crawlers.api.threads import parse_meta_response


class ParseMetaResponseTests(unittest.TestCase):
    def test_strips_anti_json_hijacking_prefix(self):
        body = parse_meta_response('for (;;);{"error":1357054,"errorSummary":"nope"}')
        self.assertIsNotNone(body)
        self.assertEqual(body["error"], 1357054)
        self.assertEqual(body["errorSummary"], "nope")

    def test_reads_plain_json(self):
        body = parse_meta_response('{"data":{"feed":{"edges":[]}}}')
        self.assertEqual(body, {"data": {"feed": {"edges": []}}})

    def test_returns_none_for_non_json(self):
        self.assertIsNone(parse_meta_response("<!DOCTYPE html><html></html>"))

    def test_returns_none_for_non_object_json(self):
        self.assertIsNone(parse_meta_response("[1, 2, 3]"))


if __name__ == "__main__":
    unittest.main()
