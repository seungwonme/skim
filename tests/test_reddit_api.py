"""Reddit API crawler 회귀 테스트."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import typer

from skim_core.crawlers.api.reddit import RedditAPICrawler


def make_response(
    *,
    text: str = "",
    json_data=None,
    content_type: str = "text/html; charset=utf-8",
    status_code: int = 200,
    url: str = "https://www.reddit.com/r/python/hot.json?limit=3",
):
    response = Mock()
    response.text = text
    response.status_code = status_code
    response.url = url
    response.headers = {"content-type": content_type}
    response.json = Mock(return_value=json_data)
    response.raise_for_status = Mock()
    return response


class RedditAPICrawlerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.session_path = Path(self.temp_dir.name) / "reddit_session.json"
        self.crawler = RedditAPICrawler(session_path=self.session_path)

    def test_extract_challenge_data_from_verification_page(self):
        html = """
        <html>
          <head>
            <title>Reddit - Please wait for verification</title>
            <script>
              document.addEventListener("DOMContentLoaded", async function() {
                var e = document.forms[0],
                    n = (e.onsubmit = function(){return true;},
                    await (async e=>e+e)("abc123"));
                e.elements.namedItem("solution").value = n;
              });
            </script>
          </head>
          <body>
            <form hidden method="GET" action="/r/python/hot/">
              <input type="hidden" name="solution" />
              <input type="hidden" name="js_challenge" value="1"/>
              <input type="hidden" name="token" value="token-123"/>
            </form>
          </body>
        </html>
        """

        self.assertEqual(
            self.crawler.extract_challenge_data(html),
            {
                "seed": "abc123",
                "solution": "abc123abc123",
                "token": "token-123",
            },
        )

    def test_parse_listing_maps_reddit_fields_to_post(self):
        listing = {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "abc123",
                            "title": "Interesting Python post",
                            "selftext": "Body text",
                            "author": "aiden",
                            "subreddit_name_prefixed": "r/Python",
                            "permalink": "/r/Python/comments/abc123/interesting_python_post/",
                            "score": 42,
                            "num_comments": 7,
                            "created_utc": 1775630000,
                            "upvote_ratio": 0.98,
                            "is_self": True,
                        }
                    }
                ],
                "after": None,
            }
        }

        posts, after = self.crawler.parse_listing(listing)

        self.assertIsNone(after)
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post.platform, "reddit")
        self.assertEqual(post.external_id, "abc123")
        self.assertEqual(post.author, "aiden")
        self.assertEqual(post.title, "Interesting Python post")
        self.assertEqual(post.content, "Body text")
        self.assertEqual(post.source, "r/Python")
        self.assertEqual(
            post.url,
            "https://www.reddit.com/r/Python/comments/abc123/interesting_python_post/",
        )
        self.assertEqual(post.likes, 42)
        self.assertEqual(post.comments, 7)
        self.assertEqual(post.upvote_ratio, 0.98)

    def test_crawl_subreddit_solves_challenge_and_returns_posts(self):
        challenge_html = """
        <html>
          <head>
            <title>Reddit - Please wait for verification</title>
            <script>
              document.addEventListener("DOMContentLoaded", async function() {
                await (async e=>e+e)("seed42");
              });
            </script>
          </head>
          <body>
            <form hidden method="GET" action="/r/python/hot/">
              <input type="hidden" name="solution" />
              <input type="hidden" name="js_challenge" value="1"/>
              <input type="hidden" name="token" value="token-42"/>
            </form>
          </body>
        </html>
        """
        listing = {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "abc123",
                            "title": "Interesting Python post",
                            "selftext": "",
                            "author": "aiden",
                            "subreddit_name_prefixed": "r/Python",
                            "permalink": "/r/Python/comments/abc123/interesting_python_post/",
                            "score": 42,
                            "num_comments": 7,
                            "created_utc": 1775630000,
                        }
                    }
                ],
                "after": None,
            }
        }

        responses = [
            make_response(text=challenge_html),
            make_response(
                url="https://www.reddit.com/r/python/hot/?solution=seed42seed42&js_challenge=1&token=token-42"
            ),
            make_response(json_data=listing, content_type="application/json; charset=UTF-8"),
        ]
        self.crawler.session.get = Mock(side_effect=responses)

        posts = self.crawler.fetch_listing(count=1, subreddit="python", sort="hot")

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].external_id, "abc123")
        request_urls = [call.args[0] for call in self.crawler.session.get.call_args_list]
        self.assertEqual(
            request_urls,
            [
                "https://www.reddit.com/r/python/hot.json?limit=1&raw_json=1",
                "https://www.reddit.com/r/python/hot.json?limit=1&raw_json=1&solution=seed42seed42&js_challenge=1&token=token-42",
                "https://www.reddit.com/r/python/hot.json?limit=1&raw_json=1",
            ],
        )

    def test_crawl_home_feed_requires_logged_in_session(self):
        with self.assertRaises(typer.Exit):
            self.crawler.fetch_listing(count=1, subreddit=None, sort="hot")
