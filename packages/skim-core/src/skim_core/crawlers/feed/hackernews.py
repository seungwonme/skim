"""
@file hackernews.py
@description Hacker News 크롤러 (Firebase API + RSS)

저장 계약: HN 행의 content_markdown은 스토리 텍스트(Ask/Show HN 본문) +
링크 원문 추출 + 상위 댓글을 합친 완성본이다. DB 소비자는 재추출하지 않는다.
"""

import re
from typing import Any, List, Optional

import requests
import typer
from bs4 import BeautifulSoup

from ...enrichment import enrich_with_content
from ...feed_config import HACKERNEWS_RSS
from ...feed_utils import fetch_feed
from ...models import Post
from ...timestamp import epoch_to_iso

HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
# 댓글 트리를 요청 1번으로 통째로 주는 Algolia item API
HN_ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items/{}"
MAX_COMMENTS = 15
MAX_COMMENT_CHARS = 1200


def _html_to_text(html: str) -> str:
    """HN 댓글/텍스트 HTML을 평문으로 변환한다 (<p>는 문단 구분)."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for p in soup.find_all("p"):
        p.insert_before("\n\n")
    return soup.get_text().strip()


def fetch_hn_discussion(story_id: str) -> Optional[dict]:
    """Algolia item API로 스토리 텍스트와 상위 댓글(1단계 대댓글 포함)을 가져온다."""
    try:
        resp = requests.get(HN_ALGOLIA_ITEM.format(story_id), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        typer.echo(f"   [!] HN 댓글 수집 실패(id={story_id}): {e}")
        return None

    comments: List[str] = []

    def walk(children: list, depth: int) -> None:
        for child in children or []:
            if len(comments) >= MAX_COMMENTS:
                return
            text = _html_to_text(child.get("text") or "")[:MAX_COMMENT_CHARS]
            if text:
                indent = "  " * depth
                author = child.get("author") or "unknown"
                comments.append(f"{indent}- **{author}**: {text}")
                if depth < 1:
                    walk(child.get("children") or [], depth + 1)

    walk(data.get("children") or [], 0)
    return {
        "story_text": _html_to_text(data.get("text") or ""),
        "comments": comments,
    }


def compose_hn_body(article_md: str, discussion: Optional[dict]) -> str:
    """스토리 텍스트 + 링크 원문 + 댓글을 하나의 정본 본문으로 합친다."""
    parts = []
    if discussion and discussion.get("story_text"):
        parts.append(discussion["story_text"])
    if article_md and article_md.strip():
        parts.append(article_md.strip())
    if discussion and discussion.get("comments"):
        parts.append("## Hacker News 댓글\n\n" + "\n".join(discussion["comments"]))
    return "\n\n---\n\n".join(parts)


class HackerNewsCrawler:
    platform = "hackernews"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 30)
        since = options.get("since")
        no_content = options.get("no_content", False)

        if since:
            items = fetch_feed(HACKERNEWS_RSS, "hackernews", since)
            items.sort(key=lambda x: x.get("published", ""), reverse=True)
            # CLI가 마지막에 posts[:count]로 자르므로, 버려질 항목을 enrichment하지 않는다.
            if options.get("count") is not None:
                items = items[: options["count"]]
        else:
            items = self._fetch_top_story_items(count)

        if not no_content:
            # 링크 원문 추출. HN item 페이지가 URL인 항목(Ask/Show HN)은
            # 추출할 원문이 없으므로 건너뛰고 Algolia 스토리 텍스트로 채운다.
            external = [
                item for item in items if "news.ycombinator.com" not in (item.get("url") or "")
            ]
            if external:
                enrich_with_content(external)

            for item in items:
                story_id = self._story_id(item)
                if not story_id:
                    continue
                discussion = fetch_hn_discussion(story_id)
                merged = compose_hn_body(item.get("content_markdown") or "", discussion)
                if merged:
                    item["content_markdown"] = merged
                    item["word_count"] = len(merged.split())

        return [self._item_to_post(item) for item in items]

    def _fetch_top_story_items(self, count: int) -> List[dict]:
        """Firebase API로 Top Stories를 RSS와 같은 item dict 형태로 가져온다."""
        typer.echo(f"Hacker News에서 상위 {count}개 스토리를 가져옵니다...")

        resp = requests.get(f"{HN_API_BASE}/topstories.json", timeout=10)
        resp.raise_for_status()
        story_ids = resp.json()[:count]

        items: List[dict] = []
        for i, story_id in enumerate(story_ids):
            try:
                item_resp = requests.get(f"{HN_API_BASE}/item/{story_id}.json", timeout=10)
                item_resp.raise_for_status()
                item = item_resp.json()

                if not item or item.get("type") != "story":
                    continue

                time_value = item.get("time")
                items.append(
                    {
                        "platform": "hackernews",
                        "author": item.get("by", "unknown"),
                        "title": item.get("title", ""),
                        "url": item.get("url")
                        or f"https://news.ycombinator.com/item?id={story_id}",
                        "published": epoch_to_iso(time_value) if time_value else "",
                        "likes": item.get("score", 0),
                        "num_comments": item.get("descendants", 0),
                        # RSS guid와 같은 포맷으로 맞춰 _story_id/_item_to_post를 공유한다.
                        "external_id": f"item?id={story_id}",
                    }
                )
                typer.echo(f"   [{i + 1}/{count}] {(item.get('title') or '')[:60]}")
            except Exception as e:
                typer.echo(f"   [{i + 1}/{count}] 스킵 (에러: {e})")

        return items

    @staticmethod
    def _story_id(item: dict) -> Optional[str]:
        haystack = f"{item.get('external_id') or ''} {item.get('url') or ''}"
        match = re.search(r"item\?id=(\d+)", haystack)
        return match.group(1) if match else None

    def _item_to_post(self, item: dict) -> Post:
        extras = {
            key: value
            for key, value in item.items()
            if key in ("enrichment_method", "enrichment_error", "image", "description") and value
        }
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content="",
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            likes=item.get("likes"),
            comments=item.get("num_comments"),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
            external_id=self._story_id(item),
            **extras,
        )
