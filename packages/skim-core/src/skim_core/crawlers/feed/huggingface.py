"""
@file huggingface.py
@description HuggingFace Daily Papers 크롤러 (JSON API)
"""

import html as html_lib
import json
import re
from datetime import datetime, timezone
from typing import Any, List, Optional

import typer
from requests import RequestException

from ...comments import Comment, append_comment_section, render_comment_section
from ...enrichment import enrich_papers_with_content
from ...feed_config import HUGGINGFACE_PAPERS_URL
from ...feed_utils import FEED_TIMEOUT_SECONDS, make_retrying_session
from ...models import Post

# 단발 요청이면 503 한 번에 그 회차가 통째로 0건이 된다.
# raise_for_status가 crawl 안에 있어 예외가 CLI까지 올라가기 때문이다.
_SESSION = make_retrying_session()

MAX_COMMENTS = 15


def _parse_iso(value: str) -> Any:
    """HF API의 ISO8601(Z) 문자열을 aware datetime으로. 실패하면 None.

    발행일 불명을 크롤 시각으로 채우면 시간축이 왜곡되므로 미상으로 남긴다.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# 논문 페이지는 SSR 페이로드(data-props)에 댓글을 통째로 담아 준다.
# 로그인도 토큰도 필요 없고 논문당 요청 1건이다.
_PAPER_PROPS = re.compile(r'data-target="PaperContent"[^>]*data-props="([^"]*)"')


def fetch_comment_section(paper_url: str) -> Optional[str]:
    """논문 페이지에서 댓글을 본문용 마크다운 섹션으로 만든다.

    HF 행의 본문은 arXiv HTML/PDF에서 뽑아오므로 같은 논문의 arxiv 행과 거의
    같다. HF만 갖는 것은 저자가 직접 단 코드/데모 링크, 평문 요약, 독자 반응이라
    이걸 안 담으면 HF Daily Papers를 따로 읽을 이유가 남지 않는다.
    """
    if not paper_url:
        return None
    # HTTP와 파싱을 함께 감싼다. 댓글 실패가 논문 저장을 막지 않는다.
    try:
        resp = _SESSION.get(paper_url, timeout=FEED_TIMEOUT_SECONDS)
        resp.raise_for_status()
        match = _PAPER_PROPS.search(resp.text)
        if not match:
            return None
        props = json.loads(html_lib.unescape(match.group(1)))

        collected: List[Comment] = []
        for node in props.get("comments") or []:
            if node.get("type") != "comment":
                continue
            data = node.get("data") or {}
            if data.get("hidden"):
                continue
            # latest.raw가 이미 마크다운 원문이라 HTML 파싱이 필요 없다.
            body = ((data.get("latest") or {}).get("raw") or "").strip()
            if not body:
                continue
            collected.append(
                Comment(
                    author=(node.get("author") or {}).get("name") or "unknown",
                    text=body,
                    created=(node.get("createdAt") or "")[:16].replace("T", " ")
                    or None,
                )
            )
        return render_comment_section(
            "Hugging Face Comments", collected, max_comments=MAX_COMMENTS
        )
    except (RequestException, ValueError, KeyError, TypeError) as e:
        typer.echo(f"   [!] HF 댓글 수집 실패({paper_url}): {e}")
        return None


class HuggingFaceCrawler:
    platform = "huggingface"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 50)
        no_content = options.get("no_content", False)
        since = options.get("since")

        resp = _SESSION.get(HUGGINGFACE_PAPERS_URL, timeout=FEED_TIMEOUT_SECONDS)
        resp.raise_for_status()
        papers = resp.json()

        items: List[dict] = []

        for p in papers:
            paper = p.get("paper") or {}
            paper_id = paper.get("id", "")
            authors_all = paper.get("authors") or []
            authors = ", ".join(a.get("name", "") for a in authors_all[:5])
            if len(authors_all) > 5:
                authors += " et al."

            entry_dt = _parse_iso(p.get("publishedAt", ""))
            published = (
                entry_dt.astimezone(timezone.utc).isoformat() if entry_dt else ""
            )

            # Daily Papers는 큐레이션 목록이라 publishedAt(=arXiv 발행일)이 며칠에서
            # 몇 주 전이다. 그걸로 --days 창을 자르면 오늘 올라온 논문이 통째로
            # 걸러진다 (2026-08-10 실측: 최신 목록의 publishedAt 최댓값이 5일 전이라
            # 3일 창에서 0건). HF가 목록에 올린 날짜로 거른다.
            daily_dt = _parse_iso(paper.get("submittedOnDailyAt", ""))
            window_dt = daily_dt or entry_dt
            if since and window_dt and window_dt < since:
                continue

            items.append(
                {
                    "platform": "huggingface",
                    "title": p.get("title", ""),
                    "url": f"https://huggingface.co/papers/{paper_id}"
                    if paper_id
                    else "",
                    "author": authors,
                    "published": published,
                    "summary": re.sub(r"\s+", " ", p.get("summary", "")).strip()[:500],
                    "abstract": re.sub(r"\s+", " ", p.get("summary", "")).strip(),
                    "thumbnail": p.get("thumbnail", ""),
                    "num_comments": p.get("numComments", 0),
                    "submitted_on_daily_at": daily_dt.astimezone(
                        timezone.utc
                    ).isoformat()
                    if daily_dt
                    else "",
                }
            )

        items = items[:count]

        if not no_content and items:
            enrich_papers_with_content(items)
            self._attach_comments(items)

        return [self._item_to_post(item) for item in items]

    def _attach_comments(self, items: List[dict]) -> None:
        """댓글을 본문 뒤에 잇는다. 0건인 논문은 조회하지 않는다.

        numComments가 목록 응답에 이미 있어 판정 비용이 0이다.
        """
        for item in items:
            if (item.get("num_comments") or 0) < 1:
                continue
            section = fetch_comment_section(item.get("url", ""))
            if section:
                item["content_markdown"] = append_comment_section(
                    item.get("content_markdown") or "", section
                )

    def _item_to_post(self, item: dict) -> Post:
        extras = {
            key: value
            for key, value in item.items()
            if key in ("enrichment_method", "enrichment_error", "submitted_on_daily_at")
            and value
        }
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content="",
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
            thumbnail=item.get("thumbnail", ""),
            comments=item.get("num_comments", 0),
            **extras,
        )
