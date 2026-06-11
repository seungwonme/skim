"""SNS API crawler 메타데이터 회귀 테스트."""

import unittest
from datetime import datetime, timedelta, timezone

from skim_core.crawlers.api.linkedin import LinkedInAPICrawler
from skim_core.crawlers.api.threads import ThreadsAPICrawler
from skim_core.crawlers.api.x import XAPICrawler


class SocialAPIMetadataTests(unittest.TestCase):
    def test_threads_parse_thread_sets_external_id_and_iso_timestamp(self):
        crawler = ThreadsAPICrawler.__new__(ThreadsAPICrawler)
        taken_at = int(datetime(2024, 4, 8, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        thread = {
            "thread_items": [
                {
                    "post": {
                        "pk": "987654321",
                        "taken_at": taken_at,
                        "code": "ABC123",
                        "user": {"username": "aiden"},
                        "caption": {"text": "첫 번째 문단"},
                        "like_count": 12,
                        "text_post_app_info": {
                            "direct_reply_count": 3,
                            "repost_count": 4,
                        },
                    }
                },
                {
                    "post": {
                        "user": {"username": "aiden"},
                        "caption": {"text": "두 번째 문단"},
                    }
                },
            ]
        }

        post = getattr(crawler, "_parse_thread")(thread)

        self.assertIsNotNone(post)
        self.assertEqual(post.external_id, "ABC123")
        self.assertEqual(post.timestamp, "2024-04-08T00:00:00+00:00")
        self.assertEqual(post.url, "https://www.threads.net/@aiden/post/ABC123")
        self.assertEqual(post.content, "첫 번째 문단\n\n---\n\n두 번째 문단")

    def test_x_parse_single_tweet_sets_external_id_and_iso_timestamp(self):
        crawler = XAPICrawler.__new__(XAPICrawler)
        tweet = {
            "legacy": {
                "full_text": "hello https://t.co/short https://t.co/media",
                "created_at": "Wed Oct 10 20:19:24 +0000 2018",
                "id_str": "1050118621198921728",
                "favorite_count": 7,
                "reply_count": 2,
                "retweet_count": 3,
                "entities": {
                    "urls": [
                        {
                            "url": "https://t.co/short",
                            "expanded_url": "https://example.com/post",
                        }
                    ],
                    "media": [{"url": "https://t.co/media"}],
                },
            },
            "core": {
                "user_results": {
                    "result": {
                        "legacy": {
                            "screen_name": "jack",
                        }
                    }
                }
            },
            "views": {"count": "42"},
        }

        post = getattr(crawler, "_parse_single_tweet")(tweet)

        self.assertIsNotNone(post)
        self.assertEqual(post.external_id, "1050118621198921728")
        self.assertEqual(post.timestamp, "2018-10-10T20:19:24+00:00")
        self.assertEqual(post.url, "https://x.com/jack/status/1050118621198921728")
        self.assertEqual(post.content, "hello https://example.com/post")
        self.assertEqual(post.views, 42)

    def test_linkedin_extract_post_sets_activity_id_and_iso_timestamp(self):
        crawler = LinkedInAPICrawler.__new__(LinkedInAPICrawler)
        item = {
            "commentary": {"text": {"text": "LinkedIn post body"}},
            "actor": {
                "name": {"text": "Aiden"},
                "subDescription": {
                    "text": "2h •   ",
                    "accessibilityText": "2 hours ago • Visible to Aiden's connections",
                },
            },
            "createdAt": {"time": 1775631600000},
            "entityUrn": (
                "urn:li:fsd_update:(urn:li:activity:7441619761081294848,MAIN_FEED,EMPTY,DEFAULT,false)"
            ),
            "*socialDetail": "urn:li:socialDetail:test",
        }
        urn_index = {
            "urn:li:socialDetail:test": {
                "*totalSocialActivityCounts": "urn:li:counts:test",
            },
            "urn:li:counts:test": {
                "numLikes": 11,
                "numComments": 5,
                "numShares": 2,
            },
        }

        post = getattr(crawler, "_extract_post")(item, urn_index)

        self.assertIsNotNone(post)
        self.assertEqual(post.external_id, "7441619761081294848")
        self.assertEqual(post.timestamp, "2026-04-08T07:00:00+00:00")
        self.assertEqual(
            post.url,
            "https://www.linkedin.com/feed/update/urn:li:activity:7441619761081294848/",
        )
        self.assertEqual(post.likes, 11)
        self.assertEqual(post.comments, 5)
        self.assertEqual(post.reposts, 2)

    def test_linkedin_parse_relative_timestamp_supports_accessibility_copy(self):
        crawler = LinkedInAPICrawler.__new__(LinkedInAPICrawler)
        reference_time = datetime(2026, 4, 8, 18, 0, 0, tzinfo=timezone(timedelta(hours=9)))

        timestamp = getattr(crawler, "_parse_relative_timestamp")(
            "2 hours ago • Visible to Aiden's connections",
            reference_time=reference_time,
        )

        # reference_time KST 18:00 → 2h ago = KST 16:00 → UTC 07:00
        self.assertEqual(timestamp, "2026-04-08T07:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
