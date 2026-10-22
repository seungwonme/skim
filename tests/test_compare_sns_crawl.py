"""SNS 크롤러 비교 스크립트의 집계를 확인한다 (#105).

크롤과 Chrome은 띄우지 않는다. 요청 묶기, 글 요약, 작업공간 가드만 본다.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "compare_sns_crawl.py"


def _load():
    spec = importlib.util.spec_from_file_location("compare_sns_crawl", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare = _load()


def request(url, kind="xhr", post_data=None, headers=None):
    return SimpleNamespace(
        url=url, resource_type=kind, post_data=post_data, headers=headers or {}
    )


class RequestKeyTests(unittest.TestCase):
    def test_graphql_id_is_folded_and_query_name_kept(self):
        key = compare.request_key(
            "https://x.com/i/api/graphql/Ab12Cd34Ef56Gh78Ij9/TweetDetail?variables=1",
            "xhr",
        )
        self.assertEqual(key, "xhr x.com/i/api/graphql/*/TweetDetail")

    def test_long_word_without_digits_is_not_an_id(self):
        key = compare.request_key(
            "https://x.com/i/api/graphql/Ab12Cd34Ef56Gh78Ij9/SidebarUserRecommendations",
            "xhr",
        )
        self.assertTrue(key.endswith("/SidebarUserRecommendations"))

    def test_numeric_status_id_is_folded(self):
        key = compare.request_key(
            "https://x.com/i/status/2112168974154948691", "document"
        )
        self.assertEqual(key, "document x.com/i/status/*")

    def test_query_name_is_appended(self):
        key = compare.request_key(
            "https://www.threads.com/api/graphql", "xhr", "BarcelonaFeedQuery"
        )
        self.assertEqual(key, "xhr www.threads.com/api/graphql BarcelonaFeedQuery")


class RequestTallyTests(unittest.TestCase):
    def test_counts_documents_and_fetches_but_not_assets(self):
        tally = compare.RequestTally(lambda post_data, headers: headers.get("name"))
        tally.browser(request("https://x.com/home", "document"))
        tally.browser(request("https://abs.twimg.com/app.js", "script"))
        tally.browser(request("https://x.com/i/api/graphql/a/Q", headers={"name": "N"}))
        tally.browser(request("https://x.com/i/api/graphql/a/Q", headers={"name": "N"}))
        tally.http("https://www.reddit.com/api/me.json")

        self.assertEqual(tally.counts["xhr x.com/i/api/graphql/a/Q N"], 2)
        self.assertEqual(tally.totals(), {"document": 1, "xhr": 2, "http": 1})

    def test_unreadable_body_is_counted_without_a_name(self):
        def broken(post_data, headers):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "binary")

        tally = compare.RequestTally(broken)
        tally.browser(request("https://www.threads.com/api/graphql", "fetch"))

        self.assertEqual(tally.totals(), {"fetch": 1})


class SummaryTests(unittest.TestCase):
    def test_counts_duplicates_sections_and_empty_bodies(self):
        posts = [
            {
                "external_id": "1",
                "content": "a",
                "comments": 4,
                "content_markdown": "a\n\n## X Replies\n\n- @b: hi",
            },
            {"external_id": "1", "content": "a", "comments": 4},
            {"external_id": "2", "content": " ", "comments": 0},
            {
                "external_id": "3",
                "content": "c",
                "comments": 0,
                "content_status": "media-only",
            },
        ]

        summary = compare.summarize(posts)

        self.assertEqual(summary["글"], 4)
        self.assertEqual(summary["고유 id"], 3)
        self.assertEqual(summary["댓글 1개 이상"], 2)
        self.assertEqual(summary["그중 댓글 섹션"], 1)
        self.assertEqual(summary["빈 본문"], 1)
        self.assertEqual(summary["content_status"], "- 3, media-only 1")

    def test_reads_saved_crawl_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "20261020_000641.json"
            path.write_text(
                json.dumps({"metadata": {}, "posts": [{"external_id": "1"}]}),
                encoding="utf-8",
            )

            self.assertEqual(compare.load_posts(path), [{"external_id": "1"}])
            self.assertEqual(compare.latest_json(Path(tmp)), path)


class GuardTests(unittest.TestCase):
    def test_refuses_the_checkout_as_workspace(self):
        # 저장소를 작업공간으로 주면 운영 DB에 쓰게 된다. skim을 들여오기 전에 멈춘다.
        self.assertEqual(compare.main(["x", "--workspace", str(ROOT)]), 2)

    def test_baseline_needs_a_single_platform(self):
        with self.assertRaises(SystemExit):
            compare.parse_args(["x", "threads", "--baseline", "a.json"])


if __name__ == "__main__":
    unittest.main()
