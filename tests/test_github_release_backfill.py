"""GitHub 릴리스와 GitHub PDF 행 백필 검증 (#46).

대상 선정(댓글이 붙은 행과 geeknews는 건드리지 않는다), 행 갱신, 재실행 안전성을
실제 스키마의 임시 DB로 본다. 네트워크는 enrichment의 경계에서 막는다.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skim_core.db import get_connection, init_db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "backfill_github_releases.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "backfill_github_releases", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load()

RELEASE = (
    "https://github.com/langchain-ai/langchain/releases/tag/langchain-core%3D%3D1.6.6"
)
PDF = "https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf"
CHROME = (
    "You signed in with another tab or window. Reload to refresh your session. "
    "You signed out in another tab or window. Reload to refresh your session.\n\n"
    "Dismiss alert"
)
RELEASE_PAGE = (
    f"<html><body><p>{CHROME}</p><div class='markdown-body my-3'>"
    "<p>Changes since langchain-core==1.6.5</p>"
    "<p>release(core): 1.6.6 (#40906)<br>"
    "fix(anthropic): support Claude Sonnet 5.5 compatibility (#40882)</p>"
    "</div></body></html>"
)
PAPER = " ".join(f"paper{i}" for i in range(150))


class _DbCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "skim.db"
        init_db(self.db)
        self.conn = get_connection(self.db)
        self.addCleanup(self.conn.close)
        self.serial = 0

    def _insert(self, platform, url, body, extra=None, source=None):
        self.serial += 1
        cur = self.conn.execute(
            """INSERT INTO posts
               (platform, source, external_id, author, title, content, url,
                timestamp, content_markdown, word_count, extra)
               VALUES (?, ?, ?, 'a', ?, '', ?, ?, ?, ?, ?)""",
            (
                platform,
                source,
                f"id-{self.serial}",
                f"title {self.serial}",
                url,
                f"2026-09-{self.serial:02d}T00:00:00+00:00",
                body,
                len(body.split()),
                json.dumps(extra or {}),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def _row(self, row_id):
        return self.conn.execute(
            "SELECT content_markdown, word_count, extra FROM posts WHERE id = ?",
            (row_id,),
        ).fetchone()

    def _target_ids(self):
        return {t["id"] for t in backfill.fetch_targets(self.conn)}


class TargetTests(_DbCase):
    def test_target_selection(self):
        lc = "blogs/LangChain Releases"
        chrome_notes = self._insert(
            "blogs",
            RELEASE,
            f"{CHROME}\n\nnotes",
            {"enrichment_method": "defuddle"},
            lc,
        )
        empty = self._insert(
            "blogs",
            RELEASE,
            "",
            {"enrichment_method": "failed", "enrichment_error": "content not usable"},
            lc,
        )
        self._insert(
            "blogs", RELEASE, "notes", {"enrichment_method": "github-release"}, lc
        )
        self._insert("blogs", "https://example.com/post", "body", {}, "blogs/Example")
        hn_release = self._insert(
            "hackernews",
            "https://news.ycombinator.com/item?id=1",
            CHROME,
            {"original_url": RELEASE},
            "hackernews/show",
        )
        self._insert(
            "hackernews",
            "https://news.ycombinator.com/item?id=2",
            f"{CHROME}\n\n## Hacker News Comments\n\n- **pg**: nice",
            {"original_url": RELEASE},
        )
        hn_pdf = self._insert("hackernews", PDF, CHROME)
        self._insert("hackernews", PDF, PAPER, {"enrichment_method": "trafilatura"})
        self._insert(
            "geeknews", "https://news.hada.io/topic?id=1", CHROME, {"original_url": PDF}
        )

        self.assertEqual(self._target_ids(), {chrome_notes, empty, hn_release, hn_pdf})

    def test_hn_rows_take_the_aggregator_path(self):
        row = {
            "platform": "hackernews",
            "source": "hackernews/show",
            "url": "https://news.ycombinator.com/item?id=1",
            "title": "Show HN",
            "extra": json.dumps({"original_url": RELEASE}),
        }

        self.assertEqual(
            backfill.to_item(row),
            {"platform": "hackernews", "url": RELEASE, "title": "Show HN"},
        )


class BackfillTests(_DbCase):
    def _run(self):
        rows = backfill.fetch_targets(self.conn)
        with (
            patch("skim_core.enrichment._http_fetch_html", return_value=RELEASE_PAGE),
            patch(
                "skim_core.enrichment.extract_article_content",
                return_value=({"content_markdown": CHROME}, "trafilatura", None),
            ),
            patch(
                "skim_core.enrichment.extract_pdf_text",
                return_value={"content_markdown": PAPER, "word_count": 150},
            ) as pdf,
        ):
            filled = backfill.backfill(self.conn, rows, delay=0)
        return filled, pdf

    def test_release_rows_get_the_notes_only(self):
        row_id = self._insert(
            "blogs",
            RELEASE,
            "",
            {"enrichment_method": "failed", "enrichment_error": "content not usable"},
            "blogs/LangChain Releases",
        )

        filled, _ = self._run()

        self.assertEqual(filled, 1)
        row = self._row(row_id)
        self.assertIn("Changes since langchain-core==1.6.5", row["content_markdown"])
        self.assertNotIn("another tab or window", row["content_markdown"])
        self.assertEqual(row["word_count"], len(row["content_markdown"].split()))
        self.assertEqual(
            json.loads(row["extra"]), {"enrichment_method": "github-release"}
        )

    def test_pdf_row_gets_the_raw_file_and_keeps_its_extra(self):
        row_id = self._insert("hackernews", PDF, CHROME, {"points": 10})

        filled, pdf = self._run()

        self.assertEqual(filled, 1)
        self.assertEqual(
            pdf.call_args.args[0],
            "https://github.com/MoonshotAI/Kimi-K3/raw/main/k3_tech_report.pdf",
        )
        row = self._row(row_id)
        self.assertEqual(row["content_markdown"], PAPER)
        self.assertEqual(
            json.loads(row["extra"]), {"points": 10, "enrichment_method": "pdf"}
        )

    def test_rows_that_cannot_be_refetched_stay_as_they_were(self):
        row_id = self._insert("hackernews", PDF, CHROME)
        rows = backfill.fetch_targets(self.conn)

        with (
            patch(
                "skim_core.enrichment.extract_article_content",
                return_value=({"content_markdown": CHROME}, "trafilatura", None),
            ),
            patch("skim_core.enrichment.extract_pdf_text", return_value=None),
        ):
            filled = backfill.backfill(self.conn, rows, delay=0)

        self.assertEqual(filled, 0)
        self.assertEqual(self._row(row_id)["content_markdown"], CHROME)

    def test_rerun_finds_nothing_left(self):
        self._insert(
            "blogs", RELEASE, CHROME, {"enrichment_method": "defuddle"}, "blogs/LC"
        )
        self._insert("hackernews", PDF, CHROME)

        self._run()

        self.assertEqual(self._target_ids(), set())

    def test_dry_run_writes_nothing(self):
        row_id = self._insert(
            "blogs", RELEASE, CHROME, {"enrichment_method": "defuddle"}, "blogs/LC"
        )

        with (
            patch.object(
                backfill, "get_connection", side_effect=lambda: get_connection(self.db)
            ),
            patch("skim_core.enrichment._http_fetch_html") as fetch,
        ):
            code = backfill.main(["--dry-run"])

        self.assertEqual(code, 0)
        fetch.assert_not_called()
        self.assertEqual(self._row(row_id)["content_markdown"], CHROME)


if __name__ == "__main__":
    unittest.main()
