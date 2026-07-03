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

    def test_x_parse_single_tweet_keeps_entities_media_link_when_media_only(self):
        crawler = XAPICrawler.__new__(XAPICrawler)
        tweet = {
            "legacy": {
                "full_text": "https://t.co/media",
                "created_at": "Wed Oct 10 20:19:24 +0000 2018",
                "id_str": "1050118621198921728",
                "entities": {
                    "media": [
                        {
                            "url": "https://t.co/media",
                            "expanded_url": "https://x.com/jack/status/1050118621198921728/video/1",
                        }
                    ]
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
        }

        post = getattr(crawler, "_parse_single_tweet")(tweet)

        self.assertIsNotNone(post)
        self.assertEqual(
            post.content,
            "https://x.com/jack/status/1050118621198921728/video/1",
        )
        self.assertEqual(post.content_status, "media_link")

    def test_x_parse_single_tweet_keeps_extended_media_link_when_media_only(self):
        crawler = XAPICrawler.__new__(XAPICrawler)
        tweet = {
            "legacy": {
                "full_text": "https://t.co/media",
                "created_at": "Wed Oct 10 20:19:24 +0000 2018",
                "id_str": "1050118621198921728",
                "entities": {},
                "extended_entities": {
                    "media": [
                        {
                            "url": "https://t.co/media",
                            "expanded_url": "https://x.com/jack/status/1050118621198921728/video/1",
                        }
                    ]
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
        }

        post = getattr(crawler, "_parse_single_tweet")(tweet)

        self.assertIsNotNone(post)
        self.assertEqual(
            post.content,
            "https://x.com/jack/status/1050118621198921728/video/1",
        )
        self.assertEqual(post.content_status, "media_link")

    def test_x_parse_single_tweet_skips_empty_after_cleanup_without_fallback(self):
        crawler = XAPICrawler.__new__(XAPICrawler)
        tweet = {
            "legacy": {
                "full_text": "https://t.co/media",
                "created_at": "Wed Oct 10 20:19:24 +0000 2018",
                "id_str": "1050118621198921728",
                "entities": {"media": [{"url": "https://t.co/media"}]},
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
        }

        post = getattr(crawler, "_parse_single_tweet")(tweet)

        self.assertIsNone(post)

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

    def test_linkedin_parse_response_keeps_feed_order_and_share_url_id(self):
        crawler = LinkedInAPICrawler.__new__(LinkedInAPICrawler)
        first_urn = "urn:li:fsd_update:(urn:li:activity:999,MAIN_FEED,EMPTY,DEFAULT,false)"
        second_urn = "urn:li:fsd_update:(urn:li:activity:888,MAIN_FEED,EMPTY,DEFAULT,false)"
        payload = {
            "data": {
                "data": {
                    "feedDashMainFeedByMainFeed": {
                        "*elements": [first_urn, second_urn],
                    },
                },
            },
            "included": [
                {
                    "$type": "com.linkedin.voyager.dash.feed.Update",
                    "entityUrn": second_urn,
                    "metadata": {"backendUrn": "urn:li:activity:888"},
                    "commentary": {"text": {"text": "Second LinkedIn body"}},
                    "actor": {"name": {"text": "Second Author"}},
                    "createdAt": {"time": 1775631600000},
                    "*socialDetail": "urn:li:socialDetail:888",
                },
                {
                    "$type": "com.linkedin.voyager.dash.feed.Update",
                    "entityUrn": first_urn,
                    "metadata": {"backendUrn": "urn:li:activity:999"},
                    "socialContent": {
                        "shareUrl": "https://www.linkedin.com/posts/aiden_activity-111-abc"
                    },
                    "commentary": {"text": {"text": "First LinkedIn body"}},
                    "actor": {"name": {"text": "First Author"}},
                    "createdAt": {"time": 1775631600000},
                    "*socialDetail": "urn:li:socialDetail:111",
                },
                {
                    "entityUrn": "urn:li:socialDetail:111",
                    "*totalSocialActivityCounts": "urn:li:counts:111",
                },
                {
                    "entityUrn": "urn:li:counts:111",
                    "numLikes": 7,
                    "numComments": 2,
                    "numShares": 1,
                    "numImpressions": 50,
                },
            ],
        }

        posts = getattr(crawler, "_parse_response")(payload)

        self.assertEqual([post.external_id for post in posts], ["111", "888"])
        self.assertEqual(posts[0].author, "First Author")
        self.assertEqual(
            posts[0].url,
            "https://www.linkedin.com/feed/update/urn:li:activity:111/",
        )
        self.assertEqual(posts[0].likes, 7)
        self.assertEqual(posts[0].comments, 2)
        self.assertEqual(posts[0].reposts, 1)
        self.assertEqual(posts[0].views, 50)

    def test_linkedin_parse_relative_timestamp_supports_accessibility_copy(self):
        crawler = LinkedInAPICrawler.__new__(LinkedInAPICrawler)
        reference_time = datetime(2026, 4, 8, 18, 0, 0, tzinfo=timezone(timedelta(hours=9)))

        timestamp = getattr(crawler, "_parse_relative_timestamp")(
            "2 hours ago • Visible to Aiden's connections",
            reference_time=reference_time,
        )

        # reference_time KST 18:00 → 2h ago = KST 16:00 → UTC 07:00
        self.assertEqual(timestamp, "2026-04-08T07:00:00+00:00")

    def test_threads_parse_thread_collects_attached_image_urls(self):
        crawler = ThreadsAPICrawler.__new__(ThreadsAPICrawler)
        thread = {
            "thread_items": [
                {
                    "post": {
                        "pk": "1",
                        "code": "IMG1",
                        "taken_at": 1712534400,
                        "user": {"username": "aiden"},
                        "caption": {"text": "사진 있는 글"},
                        "image_versions2": {
                            "candidates": [
                                {"url": "https://cdn.threads.net/img-large.jpg", "width": 1080},
                                {"url": "https://cdn.threads.net/img-small.jpg", "width": 320},
                            ]
                        },
                        "carousel_media": [
                            {
                                "image_versions2": {
                                    "candidates": [
                                        {"url": "https://cdn.threads.net/carousel-1.jpg"}
                                    ]
                                }
                            }
                        ],
                    }
                }
            ]
        }

        post = getattr(crawler, "_parse_thread")(thread)

        self.assertIsNotNone(post)
        self.assertEqual(
            post.images,
            [
                "https://cdn.threads.net/img-large.jpg",
                "https://cdn.threads.net/carousel-1.jpg",
            ],
        )

    def test_x_parse_single_tweet_collects_photo_cdn_urls(self):
        crawler = XAPICrawler.__new__(XAPICrawler)
        tweet = {
            "legacy": {
                "full_text": "photo tweet https://t.co/media",
                "created_at": "Wed Oct 10 20:19:24 +0000 2018",
                "id_str": "1050118621198921728",
                "entities": {
                    "media": [
                        {
                            "url": "https://t.co/media",
                            "type": "photo",
                            "media_url_https": "https://pbs.twimg.com/media/abc.jpg",
                        }
                    ]
                },
            },
            "core": {
                "user_results": {"result": {"legacy": {"screen_name": "jack"}}}
            },
        }

        post = getattr(crawler, "_parse_single_tweet")(tweet)

        self.assertIsNotNone(post)
        self.assertEqual(post.images, ["https://pbs.twimg.com/media/abc.jpg"])

    def test_reddit_extract_image_urls_covers_direct_preview_and_gallery(self):
        from skim_core.crawlers.api.reddit import RedditAPICrawler

        urls = RedditAPICrawler._extract_image_urls(
            {
                "url_overridden_by_dest": "https://i.redd.it/direct.png",
                "preview": {
                    "images": [
                        {"source": {"url": "https://preview.redd.it/p.jpg?width=640&amp;s=x"}}
                    ]
                },
                "media_metadata": {
                    "m1": {"s": {"u": "https://preview.redd.it/g1.jpg?auto=webp&amp;s=y"}}
                },
            }
        )

        self.assertEqual(
            urls,
            [
                "https://i.redd.it/direct.png",
                "https://preview.redd.it/p.jpg?width=640&s=x",
                "https://preview.redd.it/g1.jpg?auto=webp&s=y",
            ],
        )

    def test_linkedin_extract_image_urls_picks_largest_vector_artifact(self):
        crawler = LinkedInAPICrawler.__new__(LinkedInAPICrawler)
        content = {
            "images": [
                {
                    "attributes": [
                        {
                            "vectorImage": {
                                "rootUrl": "https://media.licdn.com/dms/image/v2/abc/",
                                "artifacts": [
                                    {"width": 800, "fileIdentifyingUrlPathSegment": "800.jpg"},
                                    {"width": 1280, "fileIdentifyingUrlPathSegment": "1280.jpg"},
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        urls = getattr(crawler, "_extract_image_urls")(content)

        self.assertEqual(urls, ["https://media.licdn.com/dms/image/v2/abc/1280.jpg"])


if __name__ == "__main__":
    unittest.main()
