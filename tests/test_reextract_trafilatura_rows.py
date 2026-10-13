"""#85 전에 저장된 행을 다시 추출하는 스크립트 검증 (#86).

바꾸는 범위가 옛 추출 결과와 똑같은 부분뿐인지(스토리 본문, 댓글 섹션 보존), 원문이
바뀐 행과 글자를 잃는 결과를 건너뛰는지, 재실행이 안전한지를 실제 스키마의 임시 DB로
본다. 네트워크는 fetch 인자로 막는다.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from skim_core.db import get_connection, init_db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "reextract_trafilatura_rows.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "reextract_trafilatura_rows", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reextract = _load()

PAGE = (
    "<html><body><article><h1>Title</h1>"
    "<p>Intro paragraph that is long enough to count as content for the "
    "extractor to keep it around, with a few more words to be safe.</p>"
    "<ul><li><strong>Many tasks don't need frontier intelligence.</strong>"
    " In our experiments, routing cut cost by 64%.</li>"
    "<li><strong>Router belongs in the harness.</strong>"
    " Choosing needs task context.</li></ul>"
    "</article></body></html>"
)
ORIGINAL = "https://example.com/post"
STORY = "Show HN text written by the submitter."
COMMENTS = "## Hacker News Comments\n\nStory score: 10 points\n\n- **pg**: nice"


def _hn_body(article: str) -> str:
    return "\n\n---\n\n".join([STORY, article, COMMENTS])


class _DbCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        init_db(self.tmp / "skim.db")
        self.conn = get_connection(self.tmp / "skim.db")
        self.addCleanup(self.conn.close)
        self.serial = 0

    def _insert(self, platform, url, body, extra):
        self.serial += 1
        cur = self.conn.execute(
            """INSERT INTO posts
               (platform, external_id, author, title, content, url, timestamp,
                content_markdown, word_count, extra)
               VALUES (?, ?, 'a', ?, '', ?, '2026-10-01T00:00:00+00:00', ?, ?, ?)""",
            (
                platform,
                f"id-{self.serial}",
                f"title {self.serial}",
                url,
                body,
                len(body.split()),
                json.dumps(extra),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def _body(self, row_id):
        return self.conn.execute(
            "SELECT content_markdown FROM posts WHERE id = ?", (row_id,)
        ).fetchone()[0]


class TargetTests(_DbCase):
    def test_target_selection(self):
        hn = self._insert(
            "hackernews",
            "https://news.ycombinator.com/item?id=1",
            "x",
            {"enrichment_method": "trafilatura", "original_url": ORIGINAL},
        )
        rendered = self._insert(
            "blogs", ORIGINAL, "x", {"enrichment_method": "playwright+trafilatura"}
        )
        gn = self._insert(
            "geeknews",
            "https://news.hada.io/topic?id=1",
            "x",
            {"enrichment_method": "trafilatura", "original_url": ORIGINAL},
        )
        # 원문 링크가 없으면 url이 news.hada.io라 부르면 안 된다.
        self._insert(
            "geeknews",
            "https://news.hada.io/topic?id=2",
            "x",
            {"enrichment_method": "trafilatura"},
        )
        # 추출한 리다이렉트 주소를 저장하지 않았다.
        self._insert("producthunt", ORIGINAL, "x", {"enrichment_method": "trafilatura"})
        self._insert("blogs", ORIGINAL, "x", {"enrichment_method": "feed-content"})
        self._insert("blogs", ORIGINAL, "x", {"enrichment_method": "defuddle"})

        ids = {row["id"] for row in reextract.fetch_targets(self.conn, days=30)}
        self.assertEqual(ids, {hn, rendered, gn})

    def test_flagged_only_picks_broken_bold(self):
        broken = self._insert(
            "blogs", ORIGINAL, "**A.**B", {"enrichment_method": "trafilatura"}
        )
        self._insert(
            "blogs", ORIGINAL, "**A.** B", {"enrichment_method": "trafilatura"}
        )
        # 한국어 조사는 굵은 글씨에 붙는 게 정상이다.
        self._insert(
            "blogs", ORIGINAL, "**스킴**은 좋다", {"enrichment_method": "trafilatura"}
        )
        ids = {
            row["id"]
            for row in reextract.fetch_targets(self.conn, days=30, flagged_only=True)
        }
        self.assertEqual(ids, {broken})


class PlanTests(unittest.TestCase):
    def _row(self, body):
        return {"id": 1, "content_markdown": body, "word_count": len(body.split())}

    def test_replaces_only_the_article_part(self):
        old, new = "**A.**B\n\n**C.**D", "- **A.** B\n- **C.** D"
        status, update = reextract.plan_update(self._row(_hn_body(old)), old, new)
        self.assertEqual(status, reextract.CHANGED)
        self.assertEqual(update["content_markdown"], _hn_body(new))
        self.assertEqual(update["word_count"], len(_hn_body(new).split()))

    def test_skips(self):
        old, new = "**A.**B", "**A.** B"
        cases = {
            "변화 없음": (_hn_body(old), old, old),
            "옛 결과 불일치": (_hn_body("page was edited"), old, new),
            "이미 고침": (_hn_body(new), old, new),
            "위치 모호": (f"{old}\n\n{old}", old, new),
            "글자 손실": (_hn_body(old), old, "**A.**"),
            "추출 실패": (_hn_body(old), None, new),
        }
        for expected, (body, old_md, new_md) in cases.items():
            with self.subTest(expected):
                status, update = reextract.plan_update(self._row(body), old_md, new_md)
                self.assertEqual(status, expected)
                self.assertIsNone(update)

    def test_gained_letters_are_allowed(self):
        self.assertTrue(reextract.keeps_all_letters("**A.**B", "# Title\n\n**A.** B"))
        self.assertFalse(reextract.keeps_all_letters("andhelp now", "and help"))


class RunTests(_DbCase):
    def test_real_extraction_round_trip(self):
        old = reextract.old_markdown(PAGE, ORIGINAL)
        new = reextract.new_markdown(PAGE, ORIGINAL)
        # 이 페이지가 실제로 옛 결함을 재현해야 이 테스트가 의미가 있다.
        self.assertIn("intelligence.**In", old)
        self.assertIn("- **Router belongs in the harness.** Choosing", new)
        row_id = self._insert(
            "hackernews",
            "https://news.ycombinator.com/item?id=1",
            _hn_body(old),
            {"enrichment_method": "trafilatura", "original_url": ORIGINAL},
        )
        fetched = []

        def fetch(row):
            fetched.append(reextract.link_of(row))
            return PAGE

        rows = reextract.fetch_targets(self.conn, days=30)
        diff_dir = self.tmp / "diffs"
        dry = reextract.run(
            self.conn, rows, dry_run=True, delay=0, diff_dir=diff_dir, fetch=fetch
        )
        self.assertEqual(dry[reextract.CHANGED], 1)
        self.assertEqual(self._body(row_id), _hn_body(old))
        self.assertTrue((diff_dir / f"{row_id}.md").exists())
        self.assertEqual(fetched, [ORIGINAL])

        done = reextract.run(self.conn, rows, dry_run=False, delay=0, fetch=fetch)
        self.assertEqual(done[reextract.CHANGED], 1)
        body = self._body(row_id)
        self.assertEqual(body, _hn_body(new))
        self.assertTrue(body.startswith(STORY))
        self.assertTrue(body.endswith(COMMENTS))

        again = reextract.run(
            self.conn,
            reextract.fetch_targets(self.conn, days=30),
            dry_run=False,
            delay=0,
            fetch=fetch,
        )
        self.assertEqual(again["이미 고침"], 1)
        self.assertEqual(self._body(row_id), _hn_body(new))

    def test_fetch_failure_leaves_row(self):
        row_id = self._insert(
            "blogs", ORIGINAL, "**A.**B", {"enrichment_method": "trafilatura"}
        )

        def broken(_row):
            raise OSError("boom")

        rows = reextract.fetch_targets(self.conn, days=30)
        result = reextract.run(self.conn, rows, dry_run=False, delay=0, fetch=broken)
        self.assertEqual(result["받기 실패"], 1)
        self.assertEqual(self._body(row_id), "**A.**B")


if __name__ == "__main__":
    unittest.main()
