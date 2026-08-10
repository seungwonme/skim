"""
@file producthunt.py
@description Product Hunt 크롤러 (RSS)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import requests
import typer
from bs4 import BeautifulSoup

from ...comments import Comment, append_comment_section, render_comment_section
from ...enrichment import enrich_with_content
from ...feed_config import PRODUCTHUNT_RSS
from ...feed_utils import FEED_HEADERS, fetch_feed
from ...models import Post

MAX_COMMENTS = 15
# 개별 댓글은 `data-test="comment-{id}"`로만 안정적으로 잡힌다.
# 같은 접두사의 `comment-form`, `comments-feed`는 컨테이너라 제외해야 한다.
_COMMENT_ID = re.compile(r"^comment-\d+$")

# PH 피드 content에는 제품 외부 사이트로 가는 리다이렉트(/r/p/{id})가 들어있다.
# /products/{slug} 페이지는 JS SPA + 403이라 본문 추출이 불가하므로, 이 링크를 enrich 대상으로 쓴다.
_RP_HREF = re.compile(r'href="(https://www\.producthunt\.com/r/p/[^"]+)"')
# 피드 content_html 첫 <p>는 제품 태그라인이다 (그 뒤 <p>는 Discussion/Link 링크).
_TAGLINE = re.compile(r"<p>\s*(.*?)\s*</p>", re.DOTALL)


def fetch_comment_section(product_url: str) -> Optional[str]:
    """제품 페이지의 댓글을 본문용 마크다운 섹션으로 만든다.

    본문 enrich는 외부 제품 사이트를 보므로 PH 페이지를 따로 받아야 한다(제품당 요청 1건).
    """
    if not product_url or "producthunt.com/products/" not in product_url:
        return None
    # 파싱까지 try 안에 둔다. HTTP만 감싸면 PH의 DOM이 바뀔 때 셀렉터 예외가
    # crawl 루프로 올라가 그 회차 Product Hunt가 통째로 저장 0건이 된다.
    try:
        response = requests.get(product_url, headers=FEED_HEADERS, timeout=20)
        response.raise_for_status()
        return _parse_comment_section(response.text)
    except Exception as e:  # pylint: disable=broad-except
        typer.echo(f"   [!] Product Hunt 댓글 수집 실패({product_url}): {e}")
        return None


def _parse_comment_section(html: str) -> Optional[str]:
    """제품 페이지 HTML에서 댓글 섹션을 만든다."""
    soup = BeautifulSoup(html, "html.parser")
    collected: List[Comment] = []
    for node in soup.select("[data-test]"):
        if not _COMMENT_ID.match(node.get("data-test") or ""):
            continue
        inner = node.select_one("div.flex.flex-1")
        if not inner:
            continue

        # 자식 블록은 [작성자 헤더, 본문, 액션] 순이다. 액션은 time/Upvote로 확실히
        # 걸러지지만 헤더는 아바타가 있을 때만 표시가 나서(없으면 이름만 든 텍스트 블록)
        # 남은 블록의 첫 줄을 헤더로 본다.
        created = None
        blocks: List[str] = []
        for block in inner.find_all(recursive=False):
            text = " ".join(block.get_text(" ", strip=True).split())
            if not text:
                continue
            time_el = block.select_one("time")
            if time_el or text.startswith("Upvote"):
                if time_el:
                    created = time_el.get("datetime")
                continue
            blocks.append(text)

        if len(blocks) < 2:  # 헤더만 있고 본문이 없는 노드
            continue

        # 아바타 alt가 가장 정확하다. 헤더 텍스트는 "이름 제품명 Maker" 꼴로 섞여 온다.
        avatar = node.select_one("img[alt]")
        author = (avatar.get("alt") if avatar else "") or blocks[0]
        body = " ".join(blocks[1:]).strip()
        if body:
            collected.append(
                Comment(
                    author=author or "unknown",
                    text=body,
                    created=(created or "")[:16].replace("T", " ") or None,
                )
            )

    return render_comment_section(
        "Product Hunt Comments", collected, max_comments=MAX_COMMENTS
    )


def _redirect_url(item: dict) -> str:
    """피드 항목에서 제품 외부 사이트 리다이렉트 URL을 추출 (없으면 원 URL)."""
    match = _RP_HREF.search(item.get("content_html", ""))
    return match.group(1) if match else item.get("url", "")


def _tagline(item: dict) -> str:
    """피드 content_html 첫 <p>의 제품 태그라인.

    외부 사이트 본문 추출이 실패해도 최소한 이 태그라인은 본문으로 남긴다.
    """
    match = _TAGLINE.search(item.get("content_html", "") or "")
    if not match:
        return ""
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def _item_to_post(item: dict) -> Post:
    """피드 항목을 Post 객체로 변환"""
    extras = {
        key: value
        for key, value in item.items()
        if key in ("enrichment_method", "enrichment_error") and value is not None
    }
    return Post(
        platform="producthunt",
        author=item.get("author", ""),
        title=item.get("title", ""),
        content="",
        timestamp=item.get("published", ""),
        url=item.get("url", ""),
        summary=item.get("summary", ""),
        source=item.get("platform", ""),
        content_markdown=item.get("content_markdown", ""),
        word_count=item.get("word_count"),
        # 피드가 런치별 고유 id(tag:...,2005:Post/1219088)를 주는데 버리고 있었다.
        # 없으면 db가 URL로 병합해서, 같은 제품 페이지의 두 번째 이후 런치가
        # 아예 저장되지 않는다.
        external_id=item.get("external_id"),
        **extras,
    )


class ProductHuntCrawler:
    """Product Hunt RSS 피드 크롤러"""

    platform: str = "producthunt"

    async def crawl(self, **options: Any) -> List[Post]:
        """
        Product Hunt 피드를 수집합니다.

        Options:
            since (datetime): 이 시점 이후의 항목만 수집
            no_content (bool): 콘텐츠 enrichment 스킵
            debug (bool): 디버그 모드
        """
        since: datetime = options.get(
            "since",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        no_content: bool = options.get("no_content", False)
        debug: bool = options.get("debug", False)

        if debug:
            print("[PH] Product Hunt 피드 수집 중...")

        items = fetch_feed(PRODUCTHUNT_RSS, "producthunt", since)

        if debug:
            print(f"  -> {len(items)}개 항목")

        if not items:
            return []

        items.sort(key=lambda x: x.get("published", ""), reverse=True)
        # CLI가 마지막에 posts[:count]로 자르므로, 버려질 항목을 enrichment하지 않는다.
        if options.get("count") is not None:
            items = items[: options["count"]]

        for item in items:
            item["enrich_url"] = _redirect_url(item)
            item["tagline"] = _tagline(item)

        if not no_content:
            enrich_with_content(items)
            for item in items:
                item["content_markdown"] = append_comment_section(
                    item.get("content_markdown"),
                    fetch_comment_section(item.get("url", "")),
                )

        return [_item_to_post(item) for item in items]
