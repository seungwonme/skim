"""이미지만 올린 글이 버려지지 않는지 검증한다.

threads와 linkedin은 본문이 비면 파싱 단계에서 None을 돌려 DB에 행 자체가
안 생겼다. 그래서 그날 그 계정이 무엇을 올렸는지가 통째로 빠졌다.
x는 이미 사다리(alt text -> media link -> drop)를 갖고 있었다.
"""

import unittest
from unittest.mock import patch

from skim_core.crawlers.api.linkedin import LinkedInAPICrawler
from skim_core.crawlers.api.threads import ThreadsAPICrawler

IMG = "https://cdn.example.com/photo.jpg"


def _threads_item(text, image=None):
    post = {
        "user": {"username": "aiden"},
        "caption": {"text": text} if text else None,
        "code": "abc123",
        "taken_at": 1760000000,
        "like_count": 3,
        "text_post_app_info": {"direct_reply_count": 0, "repost_count": 0},
    }
    if image:
        post["image_versions2"] = {"candidates": [{"url": image}]}
    return {"post": post}


class ThreadsMediaOnlyTests(unittest.TestCase):
    def _crawler(self):
        with patch.object(ThreadsAPICrawler, "__init__", lambda self: None):
            return ThreadsAPICrawler()

    def test_image_only_post_is_kept(self):
        crawler = self._crawler()
        post = crawler._parse_thread({"thread_items": [_threads_item("", IMG)]})

        self.assertIsNotNone(post, "이미지만 있는 글이 버려지면 안 된다")
        self.assertIn(IMG, post.content)
        self.assertEqual(getattr(post, "content_status", None), "media_link")

    def test_text_post_has_no_fallback_marker(self):
        crawler = self._crawler()
        post = crawler._parse_thread({"thread_items": [_threads_item("real body", IMG)]})

        self.assertEqual(post.content, "real body")
        self.assertIsNone(getattr(post, "content_status", None))

    def test_post_without_text_or_media_is_dropped(self):
        crawler = self._crawler()
        self.assertIsNone(crawler._parse_thread({"thread_items": [_threads_item("")]}))


class LinkedInMediaOnlyTests(unittest.TestCase):
    def _crawler(self):
        with patch.object(LinkedInAPICrawler, "__init__", lambda self: None):
            return LinkedInAPICrawler()

    def _item(self, commentary):
        return {
            "commentary": {"text": {"text": commentary}},
            "actor": {"name": {"text": "Aiden"}},
            "entityUrn": "urn:li:fsd_update:(urn:li:activity:7492224454731714560,x)",
            "content": {
                "rootUrl": "https://media.example.com/",
                "artifacts": [
                    {"width": 800, "fileIdentifyingUrlPathSegment": "big.jpg"}
                ],
            },
            "createdAt": 1760000000000,
        }

    def test_image_only_post_is_kept(self):
        crawler = self._crawler()
        with patch.object(
            LinkedInAPICrawler, "_resolve_engagement", return_value=(1, 2, 3, 4)
        ):
            post = crawler._extract_post(self._item(""), {})

        self.assertIsNotNone(post, "이미지만 있는 글이 버려지면 안 된다")
        self.assertIn("big.jpg", post.content)
        self.assertEqual(getattr(post, "content_status", None), "media_link")

    def test_empty_commentary_does_not_become_a_dict_repr(self):
        # _extract_text의 str() 폴백이 `{'text': ''}`를 본문으로 만들었다.
        # 길이 임계(<10)가 우연히 막고 있어서 임계를 낮추자 표면화됐다.
        crawler = self._crawler()
        self.assertEqual(crawler._extract_text({"text": {"text": ""}}), "")
        self.assertEqual(crawler._extract_text({"nested": {"deep": {}}}), "")
        self.assertEqual(crawler._extract_text({"text": {"text": "hi"}}), "hi")

    def test_short_but_real_body_is_kept(self):
        # 임계가 10자였을 때 "명문이 아닐 수 없다." 같은 유효한 글이 잘렸다.
        crawler = self._crawler()
        with patch.object(
            LinkedInAPICrawler, "_resolve_engagement", return_value=(0, 0, 0, 0)
        ):
            post = crawler._extract_post(self._item("꾸준함은 능력이다."), {})

        self.assertEqual(post.content, "꾸준함은 능력이다.")
        self.assertIsNone(getattr(post, "content_status", None))


if __name__ == "__main__":
    unittest.main()
