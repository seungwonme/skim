"""trafilatura 2.0.0의 목록 마크다운 결함을 skim 쪽에서 메우는지 확인한다 (#84).

`<li><strong>A.</strong> B</li>`가 `**A.**B`로 저장됐다. 목록 기호와 굵은 글씨 뒤
띄어쓰기가 사라지고 다음 항목이 한 문단으로 붙었다. 문장부호 뒤에 닫히는 `**`
바로 다음에 글자가 오면 CommonMark는 굵게 처리하지 않아 `**`가 그대로 보인다.
trafilatura를 올리려면 lxml 6.1.1 이상이 필요한데, 이 맥에서는 그 바이너리가
서명 정책에 막혀 지금은 올릴 수 없다.
"""

import unittest

from skim_core.trafilatura_markdown import extract_markdown, protect_inline_spaces

INTRO = (
    "<p>Intro paragraph that is long enough to count as content for the "
    "extractor to keep it around, with a few more words to be safe.</p>"
)


def _page(body: str) -> str:
    return f"<html><body><article><h1>Title</h1>{INTRO}{body}</article></body></html>"


class ListMarkdownTests(unittest.TestCase):
    def test_item_starting_with_bold_keeps_bullet_and_space(self):
        md = extract_markdown(
            _page(
                "<ul><li><strong>Many tasks don't need frontier intelligence.</strong>"
                " In our experiments, routing cut cost by 64%.</li>"
                "<li><strong>Router belongs in the harness.</strong>"
                " Choosing needs task context.</li></ul>"
            )
        )
        self.assertIn(
            "- **Many tasks don't need frontier intelligence.** In our experiments,"
            " routing cut cost by 64%.\n"
            "- **Router belongs in the harness.** Choosing needs task context.",
            md,
        )

    def test_bold_inside_item_keeps_spaces_and_line(self):
        md = extract_markdown(
            _page(
                "<ul><li>Plain item one with <strong>bold</strong> inside.</li>"
                "<li>Plain item two.</li></ul>"
            )
        )
        self.assertIn("- Plain item one with **bold** inside.\n- Plain item two.", md)

    def test_space_between_two_inline_elements_survives(self):
        md = extract_markdown(
            _page(
                '<ul><li><strong>Download:</strong> <a href="https://x.test/d1">d1-3B'
                "</a> on the hub.</li><li>Second item.</li></ul>"
            )
        )
        # skim은 링크를 빼고 추출한다(include_links 꺼짐).
        self.assertIn("- **Download:** d1-3B on the hub.", md)

    def test_spaces_inside_inline_edges_move_outside(self):
        # Substack은 띄어쓰기를 다음 요소 안 첫 글자나 굵은 글씨 안 끝에 둔다.
        # 안에 두면 `**A: **B`가 되어 굵게 처리되지 않는다.
        md = extract_markdown(
            _page(
                "<ul><li><p><strong>Temporal dynamics: </strong>"
                "<span>What happened before.</span></p></li>"
                "<li><p><strong>Why this matters:</strong>"
                "<span> One way of looking at it.</span></p></li></ul>"
            )
        )
        self.assertIn("**Temporal dynamics:** What happened before.", md)
        self.assertIn("**Why this matters:** One way of looking at it.", md)

    def test_whitespace_only_inline_element_is_a_space(self):
        # Anthropic 글은 `and<strong> </strong>help`로 띄어쓴다.
        md = extract_markdown(
            _page(
                "<p>Support projects, and<strong> </strong>help agencies get going.</p>"
            )
        )
        self.assertIn("and help agencies", md)

    def test_ordered_list_is_numbered(self):
        md = extract_markdown(
            _page("<ol><li><em>First</em> step.</li><li>Second step.</li></ol>")
        )
        self.assertIn("1. *First* step.\n2. Second step.", md)

    def test_does_not_add_space_that_was_not_there(self):
        # 한국어 조사는 굵은 글씨에 바로 붙는다. 원문에 없던 공백을 만들지 않는다.
        md = extract_markdown(
            _page("<ul><li><strong>강조</strong>는 그대로 붙어 있어야 한다.</li></ul>")
        )
        self.assertIn("- **강조**는 그대로 붙어 있어야 한다.", md)

    def test_paragraph_output_is_unchanged(self):
        md = extract_markdown(
            _page(
                "<p>Plain <strong>bold mid</strong> sentence and <b>ends.</b> Next.</p>"
            )
        )
        self.assertIn("Plain **bold mid** sentence and **ends.** Next.", md)

    def test_marker_never_leaks(self):
        md = extract_markdown(
            _page(
                "<ul><li><strong>A.</strong> b <em>c</em> d</li></ul>"
                "<pre><code>x  =  1\n  y = <b>2</b> z</code></pre>"
            )
        )
        self.assertNotIn("␠", md)


class ProtectInlineSpacesTests(unittest.TestCase):
    def test_leaves_preformatted_text_alone(self):
        html = "<div><pre>a <b>b</b> c</pre></div>"
        self.assertNotIn("␠", protect_inline_spaces(html))

    def test_returns_input_when_html_cannot_be_parsed(self):
        self.assertEqual(protect_inline_spaces(""), "")


if __name__ == "__main__":
    unittest.main()
