"""크롤러가 수집한 댓글을 본문 마크다운 섹션으로 합성한다.

`AGENTS.md`의 데이터 계약상 토론은 `content_markdown`에 함께 담겨야 한다.
플랫폼마다 응답 구조는 다르지만 저장 형태는 같아야 하므로, 각 크롤러는
자기 응답을 `Comment`로 정규화한 뒤 `render_comment_section()`에 넘긴다.

섹션 라벨은 계약대로 항상 영어이고, 가용한 메타데이터(작성자, 점수, 작성시각)를
텍스트에 함께 표기한다. hackernews가 이미 쓰던 출력 형태를 그대로 따른다.
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional

DEFAULT_MAX_COMMENTS = 15
DEFAULT_MAX_CHARS = 1200


@dataclass
class Comment:
    """플랫폼 중립 댓글 1건."""

    author: str
    text: str
    score: Optional[int] = None
    created: Optional[str] = None
    depth: int = 0


def _clean(text: str, max_chars: int) -> str:
    """줄바꿈을 접어 목록 항목 하나로 만든다. 들여쓰기가 깨지는 것을 막는다."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) > max_chars:
        collapsed = collapsed[:max_chars].rstrip() + "..."
    return collapsed


def _meta_suffix(comment: Comment, score_unit: str) -> str:
    parts: List[str] = []
    if comment.score is not None:
        unit = score_unit if abs(comment.score) == 1 else f"{score_unit}s"
        parts.append(f"{comment.score} {unit}")
    if comment.created:
        parts.append(comment.created)
    return f" ({', '.join(parts)})" if parts else ""


def render_comment_section(
    label: str,
    comments: Iterable[Comment],
    *,
    note: Optional[str] = None,
    max_comments: Optional[int] = DEFAULT_MAX_COMMENTS,
    max_chars: int = DEFAULT_MAX_CHARS,
    score_unit: str = "point",
) -> Optional[str]:
    """댓글 목록을 `## {label}` 섹션으로 만든다. 유효한 댓글이 없으면 None.

    `score_unit`은 플랫폼이 점수를 부르는 이름이다(HN/Reddit은 point, X/YouTube는 like).
    `max_comments=None`이면 받은 만큼 전부 싣는다.
    """
    lines: List[str] = []
    for comment in comments:
        if max_comments is not None and len(lines) >= max_comments:
            break
        text = _clean(comment.text, max_chars)
        if not text:
            continue
        indent = "  " * max(0, comment.depth)
        author = comment.author or "unknown"
        lines.append(
            f"{indent}- **{author}**{_meta_suffix(comment, score_unit)}: {text}"
        )

    if not lines:
        return None

    section = f"## {label}\n\n"
    if note:
        section += f"{note}\n\n"
    return section + "\n".join(lines)


def append_comment_section(
    body: Optional[str], section: Optional[str]
) -> Optional[str]:
    """본문 뒤에 댓글 섹션을 잇는다. 구분자는 기존 본문 합성과 같은 `---`."""
    if not section:
        return body
    if not body or not body.strip():
        return section
    return f"{body.rstrip()}\n\n---\n\n{section}"
