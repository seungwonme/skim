"""youtube 행 정체성(video ID 기반 dedup) 회귀 테스트."""

import unittest

from skim_core.crawlers.feed.youtube import _item_to_post, youtube_video_id


class YouTubeIdentityTests(unittest.TestCase):
    def test_video_id_extracted_from_url_variants(self):
        self.assertEqual(
            youtube_video_id("https://www.youtube.com/watch?v=wVB95vLg_FQ"), "wVB95vLg_FQ"
        )
        self.assertEqual(youtube_video_id("https://youtu.be/wVB95vLg_FQ"), "wVB95vLg_FQ")
        self.assertEqual(
            youtube_video_id("https://www.youtube.com/shorts/wVB95vLg_FQ"), "wVB95vLg_FQ"
        )
        self.assertIsNone(youtube_video_id("https://example.com/article"))

    def test_item_to_post_uses_video_id_as_external_id(self):
        # 제목이 바뀌어도 external_id가 같아 upsert가 같은 영상으로 병합한다
        base = {
            "url": "https://www.youtube.com/watch?v=wVB95vLg_FQ",
            "author": "LangChain",
            "published": "2026-07-03T00:00:00+00:00",
        }
        first = _item_to_post({**base, "title": "Old title"})
        renamed = _item_to_post({**base, "title": "New improved title"})

        self.assertEqual(first.external_id, "wVB95vLg_FQ")
        self.assertEqual(first.external_id, renamed.external_id)


if __name__ == "__main__":
    unittest.main()
