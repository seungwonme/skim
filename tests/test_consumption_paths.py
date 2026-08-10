"""소비 경로 회귀 테스트: 출력 절단, 본문 포함 bundle, 마크다운 export, OPML 왕복."""

import tempfile
import unittest
from pathlib import Path

import typer

import skim_cli.cli as main
from skim_core.db import init_db, save_posts
from skim_core.models import Post
from skim_core.research.serializer import shape_posts


def _response(bodies):
    return {
        "topic": "t",
        "posts": [
            {
                "platform": "blogs",
                "title": f"post {i}",
                "url": f"https://example.com/{i}",
                "timestamp": "2026-08-10T00:00:00+00:00",
                "content": body,
                "content_markdown": body,
                "summary": body[:20],
                "author": "a",
            }
            for i, body in enumerate(bodies)
        ],
        "stats": {"total": len(bodies)},
        "warnings": [],
    }


class ShapePostsTests(unittest.TestCase):
    def test_max_chars_truncates_every_body_field(self):
        response = shape_posts(_response(["x" * 5000]), max_chars=100)
        post = response["posts"][0]

        self.assertEqual(len(post["content_markdown"]), 100)
        self.assertEqual(len(post["content"]), 100)

    def test_truncation_is_recorded_not_silent(self):
        # 조용히 자르면 받는 쪽이 "본문이 원래 이만큼"이라고 믿는다.
        post = shape_posts(_response(["x" * 5000]), max_chars=100)["posts"][0]

        self.assertTrue(post["truncated"])
        self.assertEqual(post["content_markdown_chars"], 5000)

    def test_short_body_is_left_alone(self):
        post = shape_posts(_response(["short"]), max_chars=100)["posts"][0]

        self.assertEqual(post["content_markdown"], "short")
        self.assertNotIn("truncated", post)

    def test_fields_keeps_only_the_requested_keys(self):
        response = shape_posts(_response(["body"]), fields=["platform", "title", "url"])

        self.assertEqual(sorted(response["posts"][0]), ["platform", "title", "url"])

    def test_no_options_leaves_the_response_untouched(self):
        response = shape_posts(_response(["x" * 5000]))

        self.assertEqual(len(response["posts"][0]["content_markdown"]), 5000)

    def test_unknown_field_is_rejected_with_exit_2(self):
        with self.assertRaises(typer.Exit) as ctx:
            main._parse_fields("platform,not_a_field")
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_known_fields_parse(self):
        self.assertEqual(
            main._parse_fields("platform, title ,url"), ["platform", "title", "url"]
        )


class RecentPostsTests(unittest.TestCase):
    """topic 없는 bundle이 빈 posts를 주던 자리. 소비자가 sqlite3로 우회하고 있었다."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "skim.db"
        init_db(self.db_path)
        save_posts(
            [
                Post(
                    platform="blogs",
                    author="a",
                    content="",
                    content_markdown="full body here",
                    title="T",
                    timestamp="2026-08-10T00:00:00+00:00",
                    url="https://example.com/1",
                    external_id="1",
                )
            ],
            "blogs",
            db_path=self.db_path,
        )

    def test_recent_posts_carry_the_body(self):
        rows = main._recent_posts(self.db_path, 7, None, 10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content_markdown"], "full body here")

    def test_platform_filter_applies(self):
        self.assertEqual(main._recent_posts(self.db_path, 7, ["reddit"], 10), [])


class MarkdownExportTests(unittest.TestCase):
    def test_frontmatter_escapes_quotes_in_titles(self):
        text = main._post_to_markdown(
            {
                "title": 'He said "hi"',
                "platform": "blogs",
                "url": "https://example.com",
                "timestamp": "2026-08-10T00:00:00+00:00",
                "content_markdown": "body",
                "word_count": 1,
            }
        )
        # 따옴표를 그대로 쓰면 YAML frontmatter가 깨져 Obsidian이 못 읽는다.
        self.assertIn('title: "He said \\"hi\\""', text)
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("\nbody\n", text)

    def test_filename_is_unique_per_index(self):
        post = {"title": "same", "platform": "blogs", "timestamp": "2026-08-10T00:00"}
        self.assertNotEqual(main._md_filename(post, 0), main._md_filename(post, 1))

    def test_undated_post_still_gets_a_name(self):
        self.assertTrue(
            main._md_filename({"title": "t", "platform": "blogs"}, 0).startswith(
                "undated-"
            )
        )


OPML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>My feeds</title></head>
  <body>
    <outline text="tech">
      <outline type="rss" text="A blog" xmlUrl="https://a.example/feed" htmlUrl="https://a.example/" />
      <outline type="rss" title="B blog" xmlUrl="https://b.example/rss" />
    </outline>
    <outline text="folder with no feed" />
  </body>
</opml>
"""


class OpmlTests(unittest.TestCase):
    def test_reads_nested_outlines_and_skips_folders(self):
        entries = main.parse_opml(OPML_SAMPLE)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["name"], "A blog")
        self.assertEqual(entries[0]["feed_url"], "https://a.example/feed")
        self.assertEqual(entries[0]["site_url"], "https://a.example/")
        # htmlUrl이 없으면 피드 주소로 대신한다.
        self.assertEqual(entries[1]["site_url"], "https://b.example/rss")

    def test_doctype_is_rejected_before_parsing(self):
        # 사용자 파일이라 신뢰 경계다. expat은 내부 엔티티를 그대로 펼쳐
        # billion-laughs로 메모리를 태울 수 있다.
        evil = (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE opml [<!ENTITY a "xxxxxxxxxx">]>\n'
            '<opml><body><outline xmlUrl="http://e.com/f"/></body></opml>'
        )
        with self.assertRaises(ValueError):
            main.parse_opml(evil)

    def test_round_trip_preserves_every_feed(self):
        rows = [
            {
                "platform": "blogs",
                "display_name": 'Quote " name',
                "canonical_id": "https://a.example/",
                "feed_url": "https://a.example/feed",
            },
            {
                "platform": "youtube",
                "display_name": "Chan",
                "canonical_id": "@chan",
                "feed_url": "https://yt.example/feed",
            },
        ]

        opml = main._sources_opml(rows)
        back = main.parse_opml(opml)

        self.assertEqual(
            sorted(e["feed_url"] for e in back),
            ["https://a.example/feed", "https://yt.example/feed"],
        )
        # 따옴표가 든 이름이 XML 속성을 깨지 않고 그대로 돌아와야 한다.
        self.assertIn('Quote " name', [e["name"] for e in back])


class BundleSummaryTests(unittest.TestCase):
    def test_group_by_platform_lists_each_bucket(self):
        response = _response(["a", "b"])
        response["posts"][1]["platform"] = "reddit"

        summary = main._bundle_summary(response, Path("/tmp/inv.tsv"), "platform")

        self.assertIn("### blogs (1)", summary)
        self.assertIn("### reddit (1)", summary)
        self.assertIn("[post 0](https://example.com/0)", summary)

    def test_group_by_is_optional(self):
        summary = main._bundle_summary(_response(["a"]), Path("/tmp/inv.tsv"))
        self.assertNotIn("Grouped by", summary)


if __name__ == "__main__":
    unittest.main()
