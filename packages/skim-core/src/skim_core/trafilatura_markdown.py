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

목록 밖에도 결함이 하나 더 있다 (#89). trafilatura는 문단을 순회하는 도중 자식이 있는
서식 요소를 만나면 그 자식 태그를 지우는데, 순회 중에 트리가 바뀌어 순회가 끊기고 문단의
나머지가 사라진다(`<em><strong>A</strong></em>. 뒷문장`). 공백뿐인 꼬리는 이 경로를 비켜
갔지만 표시 문자를 넣은 꼬리는 글자라 더 자주 탄다. 그래서 표시를 넣기 전에 서식 요소
안의 서식 태그를 미리 푼다. 문서를 읽는 단계에도 본문을 잃는 결함이 둘 있어 `_load`가
피한다.

trafilatura를 올리면 이 모듈이 필요 없을 수 있다. 다만 2.1.0부터 lxml 6.1.1 이상이
필요하고, 2026-10-13 이 맥에서는 새로 받은 lxml 바이너리가 서명 정책에 막혀 지워졌다.
올릴 때는 `tests/test_trafilatura_markdown.py`가 그대로 통과하는지 먼저 본다.
"""

import re
from html import unescape
from typing import Any, List, Optional

import lxml.html
from lxml import etree
from lxml.etree import _Element

try:
    import trafilatura  # pylint: disable=import-error
    from trafilatura.utils import load_html, normalize_unicode, sanitize
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
# 하위 목록 들여쓰기 표시. trafilatura의 sanitize가 줄 앞 공백을 지워서 표시로 넘긴다.
INDENT_MARK = "␉"
_INDENT_AT_LINE_START = re.compile(rf"(?m)^{INDENT_MARK}+")
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
# DOCTYPE 선언. 첫 `>`까지만 본다.
_DOCTYPE = re.compile(r"^\s*<!DOCTYPE[^>]*>", re.IGNORECASE)
# head에 둘 수 있는 요소
_HEAD_TAGS = frozenset(
    {"base", "link", "meta", "noscript", "script", "style", "template", "title"}
)
# 공백이 의미를 갖는 영역. 표시를 넣으면 코드 정렬이 바뀐다.
_PRESERVE_TAGS = frozenset({"pre", "script", "style", "textarea"})

# 안에 다른 요소가 들어 있으면 trafilatura가 문단을 망가뜨리는 서식 태그 (#89).
# 문단을 도는 도중 안쪽 태그를 지워 순회가 어긋난다. 문단 나머지를 잃거나
# (`<em><strong>`, 서식 안의 `<br>`), 줄이 엉뚱하게 갈리거나(`<b>X <u>Y</u></b>`),
# 글자 순서가 바뀐다(`<code>X <i>Y</i></code>`).
_FORMATTING_TAGS = frozenset(
    {
        "b",
        "code",
        "del",
        "em",
        "i",
        "kbd",
        "s",
        "samp",
        "strike",
        "strong",
        "sub",
        "sup",
        "tt",
        "u",
        "var",
    }
)
# 서식 안에서 태그를 푸는 요소. 링크와 span은 안에 있어도 멀쩡하다. 링크는 본문과
# 메뉴를 가르는 링크 밀도 판단에 쓰이므로 남긴다. time은 안에 들면 글자가 사라진다.
_UNWRAP_INSIDE = _FORMATTING_TAGS | {"q", "time"}
# 서식 안에 있어도 되는 요소. 이 밖의 요소(블록, 이미지 묶음 등)가 있으면 손대지 않는다.
_ALLOWED_INSIDE = _INLINE_TAGS | _UNWRAP_INSIDE | {"br", "font", "img"}
# trafilatura가 숨김으로 보고 지우는 요소. 그 안의 태그를 풀면 숨긴 글자가 섞인다.
_HIDDEN_STYLE = re.compile(r"display:\s*none|visibility:\s*hidden", re.IGNORECASE)
_HIDDEN_CLASS = re.compile(r"hidden|\bhide|sr-only", re.IGNORECASE)


def _is_inline(element: Any) -> bool:
    return isinstance(element.tag, str) and element.tag in _INLINE_TAGS


def _looks_hidden(element: Any) -> bool:
    return (
        bool(_HIDDEN_STYLE.search(element.get("style") or ""))
        or bool(_HIDDEN_CLASS.search(element.get("class") or ""))
        or element.get("aria-hidden") == "true"
        or element.get("hidden") is not None
    )


def _can_flatten(element: Any) -> bool:
    return all(
        isinstance(child.tag, str)
        and child.tag in _ALLOWED_INSIDE
        and not _looks_hidden(child)
        for child in element.iterdescendants()
    )


def _split_at_breaks(element: Any) -> None:
    """`<b>A<br>B</b>`를 `<b>A</b><br><b>B</b>`로 나눈다.

    서식 안의 `<br>`도 문단 나머지를 잃게 한다. 텍스트와 `<br>`만 남은 서식만 나눈다.
    링크가 남아 있으면 나눌 때 링크를 옮겨야 해서 그대로 둔다.
    """
    if not any(child.tag == "br" for child in element) or any(
        child.tag != "br" for child in element
    ):
        return
    parent = element.getparent()
    pieces = [element.text] + [br.tail for br in element]
    nodes: List[Any] = []
    for index, piece in enumerate(pieces):
        if index:
            nodes.append(element.makeelement("br", {}))
        if piece and piece.strip():
            clone = element.makeelement(element.tag, dict(element.attrib))
            clone.text = piece
            nodes.append(clone)
    nodes[-1].tail = element.tail
    position = parent.index(element)
    parent.remove(element)
    for offset, node in enumerate(nodes):
        parent.insert(position + offset, node)


def _flatten_formatting(root: Any) -> None:
    """서식 요소 안의 서식 태그를 미리 푼다. trafilatura도 지우는 태그라 서식은 같다.

    `<em><strong>A</strong></em>`처럼 안쪽 서식 하나가 전부면 바깥 서식을 푼다.
    안쪽 요소는 목록 뒤쪽에 남아 있어 차례가 오면 따로 본다.
    """
    for element in list(root.iter(*_FORMATTING_TAGS)):
        if (
            len(element) == 0
            or element.getparent() is None
            or not _can_flatten(element)
            or any(a.tag in _PRESERVE_TAGS for a in element.iterancestors())
        ):
            continue
        only = element[0] if len(element) == 1 else None
        if (
            only is not None
            and only.tag in _FORMATTING_TAGS
            and not (element.text or "").strip()
            and not (only.tail or "").strip()
        ):
            element.drop_tag()
            continue
        inner = {c.tag for c in element.iterdescendants() if c.tag in _UNWRAP_INSIDE}
        if inner:
            etree.strip_tags(element, *inner)
        _split_at_breaks(element)


def _keep_breaks(root: Any) -> None:
    """바로 뒤에 요소가 오는 `<br>`에 표시를 꼬리로 단다.

    trafilatura는 글자도 꼬리도 없는 `<br>`을 버린다. `A<br><b>B</b>`가 `A**B**`로
    붙고, `<br><br>`의 앞쪽 줄바꿈이 사라진다. 표시는 줄 첫머리에서 지워진다.
    """
    for br in root.iter("br"):
        if (
            not (br.tail or "").strip()
            and br.getnext() is not None
            and not any(a.tag in _PRESERVE_TAGS for a in br.iterancestors())
        ):
            br.tail = SPACE_MARK


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


def _load(html: str) -> Optional[Any]:
    """trafilatura가 읽는 방식으로 읽되, 본문을 잃는 두 결함을 피한다 (#89).

    - trafilatura의 `load_html`은 첫 줄의 DOCTYPE을 `^< ?! ?DOCTYPE.+?/ ?>`로 지운다.
      한 줄로 압축된 문서에서는 첫 `/>`나 `/ >`까지 본문째 지워진다. danluu.com은
      `<a href=/x/ >`로 써서 글 도입부가 사라졌다. DOCTYPE은 여기서 첫 `>`까지만 지운다.
    - libxml2는 `<body>` 태그 없이 `<header>`, `<main>` 같은 요소가 오면 본문 전체를
      head에 넣는다. #85는 이 트리를 넘겨 danluu.com 글이 16457자에서 1560자로 줄었다.
      head에 둘 수 없는 첫 요소부터 body로 옮긴다.
    """
    if not html:
        return None
    try:
        root = load_html(_DOCTYPE.sub("", html, count=1))
    except (TypeError, ValueError):
        return None
    if root is not None:
        _move_body_out_of_head(root)
    return root


def _move_body_out_of_head(root: Any) -> None:
    if root.tag != "html" or root.find("body") is not None:
        return
    head = root.find("head")
    if head is None:
        return
    children = list(head)
    start = next(
        (
            index
            for index, child in enumerate(children)
            if isinstance(child.tag, str) and child.tag not in _HEAD_TAGS
        ),
        None,
    )
    if start is None:
        return
    body = root.makeelement("body", {})
    for child in children[start:]:
        body.append(child)
    root.append(body)


def _prepare(root: Any) -> None:
    """trafilatura에 넘기기 전에 트리를 고친다.

    서식 요소 안의 서식 태그를 풀고, 뒤에 요소가 오는 `<br>`을 지킨 뒤(#89), 인라인
    요소 경계의 공백을 `SPACE_MARK`로 바꾼다(#84). 원문에 있던 공백만 표시한다.
    `<strong>강조</strong>는`처럼 붙어 있던 경계는 그대로다. 요소 안쪽 가장자리의
    공백(`<strong>A: </strong>B`, `and<strong> </strong>help`)은 요소 바깥으로 옮긴다.
    안에 두면 `**A: **B`가 되어 굵게 처리되지 않는다.
    """
    _flatten_formatting(root)
    _keep_breaks(root)
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


def protect_inline_spaces(html: str) -> str:
    """`_prepare`를 거친 HTML을 돌려준다. 읽지 못하면 입력을 그대로 돌려준다."""
    root = _load(html)
    if root is None:
        return html
    _prepare(root)
    return lxml.html.tostring(root, encoding="unicode")


def restore_spaces(markdown: str) -> str:
    """`SPACE_MARK`를 공백으로 되돌린다. 줄 첫머리와 끝의 표시는 지운다.

    줄 첫머리의 `INDENT_MARK`는 같은 수의 공백이 된다.
    """
    markdown = _INDENT_AT_LINE_START.sub(lambda m: " " * len(m.group()), markdown)
    markdown = markdown.replace(INDENT_MARK, "")
    markdown = _MARK_AT_LINE_START.sub(r"\1", markdown)
    markdown = _MARK_AT_LINE_END.sub("", markdown)
    return _MARK_INSIDE.sub(" ", markdown)


def _list_marker(item: _Element) -> str:
    parent = item.getparent()
    if parent is not None and parent.get("rend") == "ol":
        position = sum(1 for s in item.itersiblings(preceding=True) if s.tag == "item")
        return f"{position + 1}. "
    return "- "


def _ends_line(out: List[str]) -> bool:
    for chunk in reversed(out):
        if chunk:
            return chunk.endswith("\n")
    return True


def _process_item(item: _Element, out: List[str]) -> None:
    """목록 항목. 기호를 항상 앞에 붙이고 줄바꿈을 항목 끝에 둔다.

    하위 목록은 줄을 바꿔 상위 항목 기호 폭만큼 들여 쓴다. 줄을 안 바꾸면 첫 하위
    항목이 상위 항목 줄에 붙는다(`- Files- Visiting Files`, #93). 하위 목록 뒤에는
    빈 줄을 넣지 않는다.
    """
    width = sum(len(_list_marker(a)) for a in item.iterancestors("item"))
    out.append(INDENT_MARK * width + _list_marker(item))
    if item.text:
        out.append(item.text)
    for child in item:
        if child.tag != "list":
            _process_element(child, out)
            continue
        if not _ends_line(out):
            out.append("\n")
        if child.text and child.text.strip():
            out.append(child.text)
        for sub in child:
            _process_element(sub, out)
        if child.tail:
            out.append(child.tail)
    if not _ends_line(out):
        out.append("\n")
    if item.tail:
        out.append(item.tail)


def _process_element(element: _Element, out: List[str]) -> None:
    """trafilatura.xml.process_element(2.0.0)와 같되 목록 항목만 다르게 쓴다."""
    if element.tag == "item":
        _process_item(element, out)
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
    root = _load(html)
    if root is None:
        return None
    _prepare(root)
    options: dict = {"config": config} if config is not None else {}
    # 고친 트리를 그대로 넘긴다. 문자열로 바꿔 넘기면 trafilatura가 다시 읽는다.
    document = trafilatura.bare_extraction(
        root,
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
