"""구독자 벽에서 잘린 본문이 표시되는지 검증한다.

Every.to 글은 무료 미리보기까지만 저장되고 나머지는 프로모 문구다. 표시가
없으면 소비 시점(research/digest/데스크톱)에서 반쪽짜리를 완결된 글로 요약한다.
로그인은 붙이지 않기로 했다(사용자 결정, 2026-08-10).
"""

import unittest

from skim_core.crawlers.feed.everyto import _item_to_post, is_truncated


def _item(body):
    return {
        "platform": "everyto",
        "title": "Taming Opus 5",
        "url": "https://every.to/chain-of-thought/taming-opus-5",
        "published": "2026-08-10T00:00:00+09:00",
        "author": "Dan Shipper",
        "content_markdown": body,
    }


class PaywallMarkerTests(unittest.TestCase):
    def test_detects_the_wall_copy(self):
        # 2026-08-10 프로덕션 실측 문구
        body = (
            "The first half of the piece is here.\n\n"
            "**Subscribe to unlock this piece and learn about:**\n"
            "- How Kieran Klaassen updates skills that break"
        )
        self.assertTrue(is_truncated(body))

    def test_complete_article_is_not_marked(self):
        body = "A full article that ends with a normal closing paragraph."
        self.assertFalse(is_truncated(body))

    def test_empty_body_is_not_marked(self):
        self.assertFalse(is_truncated(""))
        self.assertFalse(is_truncated(None))

    def test_marker_reaches_the_post(self):
        post = _item_to_post(_item("intro\n\nSubscribe to unlock this piece and learn"))
        self.assertEqual(getattr(post, "content_status", None), "paywalled")

    def test_complete_post_has_no_marker(self):
        post = _item_to_post(_item("a complete article body"))
        self.assertIsNone(getattr(post, "content_status", None))


if __name__ == "__main__":
    unittest.main()
