"""trafilatura로 본문을 찾고, 그 결과 트리를 skim이 마크다운으로 바꾼다.

trafilatura 2.0.0의 마크다운 출력은 목록에서 두 군데가 깨진다 (#84).

1. 트리를 만들 때 목록 안 인라인 요소의 꼬리를 `trim()`한다. `<li><strong>A.</strong>
   B</li>`의 띄어쓰기가 사라져 `**A.**B`가 되고, CommonMark는 문장부호 뒤에 닫히는
   `**` 바로 다음에 글자가 오면 굵게 처리하지 않아 `**`가 그대로 보인다.
2. 마크다운으로 바꿀 때 항목 첫 텍스트가 있어야 `- `를 붙이고, 줄바꿈을 항목 끝이
   아니라 첫 텍스트 뒤에 넣는다. 굵은 글씨로 시작하는 항목은 기호가 빠지고, 인라인
   요소가 든 항목은 다음 항목과 한 문단으로 붙는다.

1은 trafilatura에 넘기기 전에 인라인 경계의 공백을 `SPACE_MARK`로 바꿔 두고 끝에서
되돌려 막는다. 이 문자는 공백이 아니라 `trim()`이 지우지 않는다. 2는 trafilatura의
`process_element`를 따르되 항목만 다르게 쓰는 변환기로 막는다. 본문을 고르는 판단은
그대로 trafilatura가 한다.

trafilatura를 올리면 이 모듈이 필요 없을 수 있다. 다만 2.1.0부터 lxml 6.1.1 이상이
필요하고, 2026-10-13 이 맥에서는 새로 받은 lxml 바이너리가 서명 정책에 막혀 지워졌다.
올릴 때는 `tests/test_trafilatura_markdown.py`가 그대로 통과하는지 먼저 본다.
"""

import re
from html import unescape
from typing import Any, List, Optional

import lxml.html
from lxml.etree import ParserError, _Element

try:
    import trafilatura  # pylint: disable=import-error
    from trafilatura.utils import normalize_unicode, sanitize
    from trafilatura.xml import (
        MAX_TABLE_WIDTH,
        NEWLINE_ELEMS,
        SPECIAL_FORMATTING,
        replace_element_text,
    )
except ImportError:  # pragma: no cover - trafilatura는 skim-core 의존성이다
    trafilatura = None  # type: ignore[assignment]

# 공백 대신 넣어 두는 표시. 공백 문자가 아니어서 trim()을 통과하고, 인쇄 가능한 문자라
# trafilatura의 제어 문자 제거에도 걸리지 않는다. 원문에 이 문자가 있으면 공백이 된다.
SPACE_MARK = "␠"
_MARK_RUN = rf"[ {SPACE_MARK}]*{SPACE_MARK}[ {SPACE_MARK}]*"
# 줄 첫머리(목록 기호 뒤 포함)와 줄 끝의 표시는 지운다. 끝에 남은 공백 둘은 줄바꿈이 된다.
_MARK_AT_LINE_START = re.compile(rf"(?m)^([ \t]*(?:[-*+] |\d+\. )?){_MARK_RUN}")
_MARK_AT_LINE_END = re.compile(rf"(?m){_MARK_RUN}$")
_MARK_INSIDE = re.compile(_MARK_RUN)

_INLINE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "cite",
        "code",
        "del",
        "em",
        "i",
        "ins",
        "kbd",
        "mark",
        "q",
        "s",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
    }
)
# 공백이 의미를 갖는 영역. 표시를 넣으면 코드 정렬이 바뀐다.
_PRESERVE_TAGS = frozenset({"pre", "script", "style", "textarea"})


def _is_inline(element: Any) -> bool:
    return isinstance(element.tag, str) and element.tag in _INLINE_TAGS


def _mark_trailing(text: Optional[str]) -> Optional[str]:
    if text and text[-1].isspace() and text.strip():
        return text.rstrip() + SPACE_MARK
    return text


def _mark_before(element: Any) -> None:
    """요소 바로 앞 텍스트(앞 형제의 꼬리, 없으면 부모의 텍스트) 끝에 표시를 붙인다."""
    previous = element.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or "").rstrip() + SPACE_MARK
    elif (parent := element.getparent()) is not None:
        parent.text = (parent.text or "").rstrip() + SPACE_MARK


def _mark_after(element: Any) -> None:
    element.tail = SPACE_MARK + (element.tail or "").lstrip()


def protect_inline_spaces(html: str) -> str:
    """인라인 요소 경계의 공백을 `SPACE_MARK`로 바꾼 HTML을 돌려준다.

    원문에 있던 공백만 표시한다. `<strong>강조</strong>는`처럼 붙어 있던 경계는 그대로다.
    요소 안쪽 가장자리의 공백(`<strong>A: </strong>B`, `and<strong> </strong>help`)은
    요소 바깥으로 옮긴다. 안에 두면 `**A: **B`가 되어 굵게 처리되지 않는다.
    파싱하지 못하면 입력을 그대로 돌려준다.
    """
    try:
        root = lxml.html.fromstring(html)
    except (ParserError, ValueError):
        return html
    for element in root.iter():
        if not _is_inline(element) or any(
            ancestor.tag in _PRESERVE_TAGS for ancestor in element.iterancestors()
        ):
            continue
        # 안쪽 앞 공백 -> 요소 앞
        if element.text and element.text[0].isspace():
            element.text = element.text.lstrip() or None
            _mark_before(element)
        # 안쪽 뒤 공백 -> 요소 뒤
        if len(element) == 0:
            if element.text and element.text[-1].isspace():
                element.text = element.text.rstrip()
                _mark_after(element)
        elif (last := element[-1]).tail and last.tail[-1].isspace():
            last.tail = last.tail.rstrip() or None
            _mark_after(element)
        # 꼬리의 앞 공백. 공백뿐인 꼬리도 다음 형제가 인라인이면 두 요소 사이의
        # 띄어쓰기다(`<b>Download:</b> <a>d1</a>`).
        tail = element.tail
        if tail and tail[0].isspace():
            sibling = element.getnext()
            if tail.strip() or (sibling is not None and _is_inline(sibling)):
                element.tail = SPACE_MARK + tail.lstrip()
        # 바로 앞 텍스트의 끝 공백
        previous = element.getprevious()
        if previous is not None:
            previous.tail = _mark_trailing(previous.tail)
        elif (parent := element.getparent()) is not None:
            parent.text = _mark_trailing(parent.text)
    return lxml.html.tostring(root, encoding="unicode")


def restore_spaces(markdown: str) -> str:
    """`SPACE_MARK`를 공백으로 되돌린다. 줄 첫머리와 끝의 표시는 지운다."""
    markdown = _MARK_AT_LINE_START.sub(r"\1", markdown)
    markdown = _MARK_AT_LINE_END.sub("", markdown)
    return _MARK_INSIDE.sub(" ", markdown)


def _list_marker(item: _Element) -> str:
    parent = item.getparent()
    if parent is not None and parent.get("rend") == "ol":
        position = sum(1 for s in item.itersiblings(preceding=True) if s.tag == "item")
        return f"{position + 1}. "
    return "- "


def _process_element(element: _Element, out: List[str]) -> None:
    """trafilatura.xml.process_element(2.0.0)와 같되 목록 항목만 다르게 쓴다.

    항목은 기호를 항상 앞에 붙이고 줄바꿈을 항목 끝에 둔다.
    """
    if element.tag == "item":
        out.append(_list_marker(element))
        if element.text:
            out.append(element.text)
        for child in element:
            _process_element(child, out)
        out.append("\n")
        if element.tail:
            out.append(element.tail)
        return

    if element.text:
        out.append(replace_element_text(element, True))
    for child in element:
        _process_element(child, out)

    if not element.text and not element.tail:
        if element.tag == "graphic":
            text = f"{element.get('title', '')} {element.get('alt', '')}".strip()
            out.append(f"![{text}]({element.get('src', '')})")
        elif element.tag in NEWLINE_ELEMS:
            if element.tag == "row":
                cell_count = len(element.xpath(".//cell"))
                span_info = element.get("colspan") or element.get("span")
                if not span_info or not span_info.isdigit():
                    max_span = 1
                else:
                    max_span = min(int(span_info), MAX_TABLE_WIDTH)
                if cell_count < max_span:
                    out.append(f"{'|' * (max_span - cell_count)}\n")
                if element.xpath("./cell[@role='head']"):
                    out.append(f"\n|{'---|' * max_span}\n")
            else:
                out.append("\n")
        elif element.tag != "cell":
            return

    if element.tag in NEWLINE_ELEMS and not element.xpath("ancestor::cell"):
        out.append("\n␤\n" if element.tag != "row" else "\n")
    elif element.tag == "cell":
        out.append(" | ")
    elif element.tag not in SPECIAL_FORMATTING:
        out.append(" ")

    if element.tail:
        out.append(element.tail)


def extract_markdown(html: str, url: str = "", config: Any = None) -> Optional[str]:
    """`trafilatura.extract(output_format="markdown")`와 같은 본문을 목록을 고쳐 돌려준다."""
    if trafilatura is None:
        return None
    options: dict = {"config": config} if config is not None else {}
    document = trafilatura.bare_extraction(
        protect_inline_spaces(html),
        url=url or None,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_formatting=True,
        no_fallback=False,
        **options,
    )
    body = getattr(document, "body", None)
    if body is None:
        return None
    out: List[str] = []
    _process_element(body, out)
    markdown = normalize_unicode(unescape(sanitize("".join(out)) or ""))
    return restore_spaces(markdown).strip() or None
