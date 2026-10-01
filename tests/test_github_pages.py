"""GitHub 페이지 본문 추출 검증 (#46).

릴리스 페이지를 페이지째 추출하면 로그인 안내, 저장소 머리말 같은 화면 문구가
노트를 감싸고(Claude Code, LangChain 릴리스 72건), 패치 릴리스의 짧은 노트는 60단어
게이트에 걸려 빈 본문이 됐다(9건). 화면 문구도 60단어를 넘겨 게이트를 통과했고,
GitHub PDF 링크 4건은 그 때문에 PDF 폴백까지 못 가고 화면 문구만 저장됐다.
"""

import unittest
from unittest.mock import patch

from skim_core.enrichment import (
    _is_content_usable,
    _pdf_fallback,
    enrich_with_content,
    extract_github_release_notes,
    github_raw_url,
    is_github_release_url,
)

RELEASE_URL = (
    "https://github.com/langchain-ai/langchain/releases/tag/langchain-core%3D%3D1.6.6"
)
NOTES_HTML = (
    "<p>Changes since langchain-core==1.6.5</p>"
    "<p>release(core): 1.6.6 (#40906)<br>"
    "fix(anthropic): support Claude Sonnet 5.5 compatibility (#40882)<br>"
    "docs(core): fix docstring examples that don't run as copied (#40815)</p>"
)
TAB_CHROME = (
    "You signed in with another tab or window. Reload to refresh your session. "
    "You signed out in another tab or window. Reload to refresh your session. "
    "You switched accounts on another tab or window. Reload to refresh your session."
    "\n\nDismiss alert"
)
REPO_HEADER_CHROME = (
    "/ **[langchain](https://github.com/langchain-ai/langchain)** Public\n\n"
    "- [Notifications](https://github.com/login?return_to=%2Flangchain-ai%2Flangchain)\n"
    "\tYou must be signed in to change notification settings\n"
    "- [Fork 24.7k](https://github.com/login?return_to=%2Flangchain-ai%2Flangchain)\n"
    "- [Star 147k](https://github.com/login?return_to=%2Flangchain-ai%2Flangchain)"
)
RELEASE_PAGE = (
    "<html><body>"
    f"<div class='flash'>{TAB_CHROME}</div>"
    "<div class='repohead'>langchain-ai / langchain Public Notifications Fork Star</div>"
    "<p>This commit was created on GitHub.com and signed with GitHub's verified "
    "signature.</p>"
    f"<div data-test-selector='body-content' class='markdown-body my-3'>{NOTES_HTML}"
    "</div></body></html>"
)


def _usable(body: str, min_words: int) -> bool:
    return _is_content_usable({"content_markdown": body}, "title", min_words=min_words)


class GithubChromeGateTests(unittest.TestCase):
    def test_tab_chrome_alone_is_not_a_body(self):
        self.assertFalse(_usable(TAB_CHROME, 3))

    def test_repo_header_alone_is_not_a_body(self):
        # 머리말을 빼면 글머리표만 남는다. 기호만 남은 토큰은 단어로 세지 않는다.
        self.assertFalse(_usable(REPO_HEADER_CHROME, 3))

    def test_chrome_around_a_real_body_stays_usable(self):
        body = " ".join(f"word{i}" for i in range(70))

        self.assertTrue(_usable(f"{TAB_CHROME}\n\n{body}", 60))

    def test_chrome_does_not_count_toward_the_word_gate(self):
        notes = " ".join(f"note{i}" for i in range(30))

        self.assertFalse(
            _usable(f"{TAB_CHROME}\n\n{REPO_HEADER_CHROME}\n\n{notes}", 60)
        )


class GithubUrlTests(unittest.TestCase):
    def test_release_url(self):
        self.assertTrue(is_github_release_url(RELEASE_URL))
        self.assertFalse(
            is_github_release_url("https://github.com/langchain-ai/langchain/releases")
        )
        self.assertFalse(is_github_release_url("https://example.com/releases/tag/v1"))

    def test_blob_url_maps_to_raw(self):
        self.assertEqual(
            github_raw_url(
                "https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf"
            ),
            "https://github.com/deepseek-ai/DeepSpec/raw/main/DSpark_paper.pdf",
        )
        self.assertIsNone(github_raw_url("https://github.com/deepseek-ai/DeepSpec"))
        self.assertIsNone(github_raw_url("https://example.com/blob/main/a.pdf"))


class ReleaseNotesTests(unittest.TestCase):
    def test_notes_are_read_from_the_markdown_body_only(self):
        with patch(
            "skim_core.enrichment._http_fetch_html", return_value=RELEASE_PAGE
        ) as fetch:
            data = extract_github_release_notes(RELEASE_URL)

        fetch.assert_called_once_with(RELEASE_URL)
        body = data["content_markdown"]
        self.assertIn("Changes since langchain-core==1.6.5", body)
        self.assertIn("support Claude Sonnet 5.5 compatibility", body)
        self.assertNotIn("another tab or window", body)
        self.assertNotIn("verified signature", body)
        self.assertNotIn("Notifications", body)

    def test_page_without_notes_returns_none(self):
        with patch(
            "skim_core.enrichment._http_fetch_html",
            return_value=f"<html><body><p>{TAB_CHROME}</p></body></html>",
        ):
            self.assertIsNone(extract_github_release_notes(RELEASE_URL))

    def test_subscribed_release_feed_body_is_used_even_when_short(self):
        # 저장소의 릴리스 피드를 구독하면 피드 본문이 노트 그 자체다. 페이지를 열지 않는다.
        item = {
            "platform": "blogs/LangChain Releases",
            "title": "langchain-core==1.6.6",
            "url": RELEASE_URL,
            "content_html": NOTES_HTML,
        }

        with patch("skim_core.enrichment._http_fetch_html") as fetch:
            enrich_with_content([item])

        fetch.assert_not_called()
        self.assertIn("Changes since langchain-core==1.6.5", item["content_markdown"])
        self.assertLess(item["word_count"], 60)
        self.assertEqual(item["enrichment_method"], "feed-content")
        self.assertNotIn("enrichment_error", item)

    def test_aggregator_feed_body_is_not_taken_as_notes(self):
        # 애그리게이터의 피드 본문은 그 사이트의 설명이다. 노트는 페이지에서 받는다.
        item = {
            "platform": "hackernews",
            "title": "Show HN: a release",
            "url": RELEASE_URL,
            "content_html": "<a href='https://news.ycombinator.com/item?id=1'>Comments</a>",
        }

        with patch("skim_core.enrichment._http_fetch_html", return_value=RELEASE_PAGE):
            enrich_with_content([item])

        self.assertIn("Changes since langchain-core==1.6.5", item["content_markdown"])
        self.assertNotIn("Comments", item["content_markdown"])
        self.assertEqual(item["enrichment_method"], "github-release")

    def test_release_without_notes_is_marked_failed(self):
        item = {
            "platform": "blogs/LangChain Releases",
            "title": "langchain-core==1.6.6",
            "url": RELEASE_URL,
        }

        with (
            patch("skim_core.enrichment._http_fetch_html", return_value=None),
            patch("skim_core.enrichment.extract_article_content") as ladder,
        ):
            enrich_with_content([item])

        # 페이지째 추출로 넘어가면 화면 문구를 받아 온다. 노트가 없으면 빈 본문이 맞다.
        ladder.assert_not_called()
        self.assertEqual(item["content_markdown"], "")
        self.assertEqual(item["enrichment_method"], "failed")


class GithubPdfTests(unittest.TestCase):
    def test_pdf_fallback_downloads_the_raw_file(self):
        with patch(
            "skim_core.enrichment.extract_pdf_text",
            return_value={"content_markdown": "pdf", "word_count": 1},
        ) as pdf:
            _pdf_fallback(
                "https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf"
            )

        self.assertEqual(
            pdf.call_args.args[0],
            "https://github.com/MoonshotAI/Kimi-K3/raw/main/k3_tech_report.pdf",
        )

    def test_chrome_only_viewer_page_falls_through_to_the_pdf(self):
        url = "https://github.com/deepseek-ai/DeepSpec/blob/main/DSpark_paper.pdf"
        item = {"platform": "hackernews", "title": "DeepSpec [pdf]", "url": url}
        paper = " ".join(f"paper{i}" for i in range(150))

        with (
            patch(
                "skim_core.enrichment.extract_article_content",
                return_value=({"content_markdown": TAB_CHROME}, "trafilatura", None),
            ),
            patch(
                "skim_core.enrichment.extract_pdf_text",
                return_value={"content_markdown": paper, "word_count": 150},
            ) as pdf,
        ):
            enrich_with_content([item])

        pdf.assert_called_once()
        self.assertEqual(item["enrichment_method"], "pdf")
        self.assertEqual(item["content_markdown"], paper)


if __name__ == "__main__":
    unittest.main()
