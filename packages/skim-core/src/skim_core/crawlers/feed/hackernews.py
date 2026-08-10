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
from ...feed_config import HACKERNEWS_FEED_LIMITS, HACKERNEWS_FEEDS
from ...feed_utils import fetch_feed
from ...models import Post
from ...timestamp import epoch_to_iso

HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
# 댓글 트리를 요청 1번으로 통째로 주는 Algolia item API
HN_ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items/{}"
HN_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
MAX_COMMENTS = 15
MAX_COMMENT_CHARS = 1200

# 제목 끝의 꼬리표. 같은 글이라도 피드와 HN이 다르게 붙인다
# ("... consent order (2 July 2026)" vs "... consent order [pdf]").
_TITLE_SUFFIX = re.compile(r"\s*[\[(][^\])]*[\])]\s*$")


def _core_title(title: str) -> str:
    """제목 끝의 괄호 꼬리표를 떼고 비교용으로 정규화한다."""
    text = (title or "").strip()
    while True:
        stripped = _TITLE_SUFFIX.sub("", text).strip()
        if stripped == text:
            return text.lower()
        text = stripped


def _algolia_hits(query: str) -> List[dict]:
    try:
        resp = requests.get(
            HN_ALGOLIA_SEARCH,
            params={"query": query[:120], "tags": "story", "hitsPerPage": 5},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except Exception:  # pylint: disable=broad-except
        return []


def search_story_id(title: str, url: str) -> Optional[str]:
    """제목이나 원문 URL로 HN story id를 찾는다.

    hnrss 경로로 저장한 옛 행은 external_id가 guid 해시라 story id가 없다.
    엉뚱한 스토리를 물면 댓글도 지표도 오염되므로, 원문 URL이 같거나 꼬리표를 뗀
    제목이 정확히 맞을 때만 채택한다.
    """
    normalized_url = (url or "").rstrip("/")
    core = _core_title(title)

    # Algolia는 모든 토큰이 맞아야 준다. 원제목을 그대로 넣으면 피드가 붙인
    # 꼬리표 때문에 0건이 나오므로 꼬리표를 뗀 쪽을 먼저 던진다.
    for query in (core, title):
        for hit in _algolia_hits(query):
            if normalized_url and (hit.get("url") or "").rstrip("/") == normalized_url:
                return hit.get("objectID")
            if core and _core_title(hit.get("title") or "") == core:
                return hit.get("objectID")
    return None


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
    # 파싱까지 try 안에 둔다. HTTP만 감싸면 Algolia 응답 구조가 바뀔 때 walk()의
    # 예외가 crawl 루프로 올라가 그 회차 HN이 통째로 저장 0건이 된다.
    try:
        resp = requests.get(HN_ALGOLIA_ITEM.format(story_id), timeout=15)
        resp.raise_for_status()
        data = resp.json()

        comments: List[str] = []

        def walk(children: list, depth: int) -> None:
            for child in children or []:
                if len(comments) >= MAX_COMMENTS:
                    return
                text = _html_to_text(child.get("text") or "")[:MAX_COMMENT_CHARS]
                if text:
                    indent = "  " * depth
                    author = child.get("author") or "unknown"
                    # HN은 댓글 점수를 공개하지 않으므로 작성시각까지가 가용 메타데이터다.
                    created = (child.get("created_at") or "")[:16].replace("T", " ")
                    meta = f" ({created} UTC)" if created else ""
                    comments.append(f"{indent}- **{author}**{meta}: {text}")
                    if depth < 1:
                        walk(child.get("children") or [], depth + 1)

        walk(data.get("children") or [], 0)
        return {
            "story_text": _html_to_text(data.get("text") or ""),
            "comments": comments,
            "points": data.get("points"),
        }
    except Exception as e:
        typer.echo(f"   [!] HN 댓글 수집 실패(id={story_id}): {e}")
        return None


_FEED_POINTS = re.compile(r"Points:\s*(\d+)")
_FEED_COMMENTS = re.compile(r"#\s*Comments:\s*(\d+)")


def metrics_from_feed(content_html: str) -> Optional[dict]:
    """RSS description에서 지표를 뽑는다. 추가 요청이 필요 없다.

    hnrss.org는 각 항목 본문에 `Points: 63`과 `# Comments: 81`을 이미 싣는다.
    이걸 버리고 item API를 따로 부르면 글 하나당 요청이 한 번씩 더 나간다.
    """
    points = _FEED_POINTS.search(content_html or "")
    if not points:
        return None
    comments = _FEED_COMMENTS.search(content_html or "")
    return {
        "likes": int(points.group(1)),
        "comments": int(comments.group(1)) if comments else None,
    }


def fetch_hn_metrics(story_id: str) -> Optional[dict]:
    """Firebase item API로 점수와 댓글 수를 가져온다.

    피드에 지표가 없을 때의 폴백이고, 과거분 백필 스크립트가 쓰는 경로이기도 하다.
    Algolia item API는 최상위에 `points`만 주고 댓글 수를 주지 않으므로,
    댓글 수까지 필요하면 Firebase의 `descendants`를 봐야 한다.
    """
    try:
        resp = requests.get(f"{HN_API_BASE}/item/{story_id}.json", timeout=10)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as e:  # pylint: disable=broad-except
        typer.echo(f"   [!] HN 지표 수집 실패(id={story_id}): {e}")
        return None
    return {"likes": data.get("score"), "comments": data.get("descendants")}


def compose_hn_body(article_md: str, discussion: Optional[dict]) -> str:
    """스토리 텍스트 + 링크 원문 + 댓글을 하나의 정본 본문으로 합친다."""
    parts = []
    if discussion and discussion.get("story_text"):
        parts.append(discussion["story_text"])
    if article_md and article_md.strip():
        parts.append(article_md.strip())
    if discussion and discussion.get("comments"):
        section = "## Hacker News Comments\n\n"
        if discussion.get("points") is not None:
            section += f"Story score: {discussion['points']} points\n\n"
        section += "\n".join(discussion["comments"])
        parts.append(section)
    return "\n\n---\n\n".join(parts)


class HackerNewsCrawler:
    platform = "hackernews"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 30)
        since = options.get("since")
        no_content = options.get("no_content", False)

        if since:
            # newest는 30점 문턱이 걸려 있고 show/ask는 없다. Ask/Show HN은 링크가 아니라
            # 본문이 알맹이라 점수 문턱에 걸리면 대부분 사라진다.
            items = []
            seen_links: set[str] = set()
            for name, feed_url in HACKERNEWS_FEEDS.items():
                fetched = fetch_feed(feed_url, name, since)
                # 피드 한 장이 창을 다 못 덮으면 그 뒤 글은 어느 경로로도 안 잡힌다.
                # 조용히 넘어가면 "그날 HN에 이만큼밖에 없었다"로 읽히므로 남긴다.
                limit = HACKERNEWS_FEED_LIMITS.get(name, 0)
                if limit and len(fetched) >= limit:
                    typer.echo(
                        f"   [!] {name}: 피드 상한 {limit}건에 닿았습니다. "
                        "창 안의 더 오래된 글은 수집되지 않습니다."
                    )
                for item in fetched:
                    link = item.get("url") or ""
                    # 점수가 오른 Show HN은 newest에도 올라와 같은 글이 두 번 온다.
                    if link and link in seen_links:
                        continue
                    seen_links.add(link)
                    items.append(item)
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
                item
                for item in items
                if "news.ycombinator.com" not in (item.get("url") or "")
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
                # 피드가 이미 실어 보낸 Points/# Comments를 먼저 쓴다. 추가 요청이 없다.
                # 거기서 못 뽑을 때만 item API로 폴백한다.
                # Top Stories 경로가 Firebase에서 이미 채운 값은 덮지 않는다.
                if item.get("likes") is None:
                    metrics = metrics_from_feed(
                        item.get("content_html", "")
                    ) or fetch_hn_metrics(story_id)
                    if metrics:
                        item["likes"] = metrics["likes"]
                        item["num_comments"] = metrics["comments"]

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
                item_resp = requests.get(
                    f"{HN_API_BASE}/item/{story_id}.json", timeout=10
                )
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
                        "url": (
                            item.get("url")
                            or f"https://news.ycombinator.com/item?id={story_id}"
                        ),
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
        story_id = self._story_id(item)
        url = item.get("url", "")
        # 정본 링크는 HN 토론 페이지. 링크 글의 원문 URL은 extra.original_url로 보존한다.
        if story_id:
            hn_url = f"https://news.ycombinator.com/item?id={story_id}"
            if url and url != hn_url:
                item["original_url"] = url
            url = hn_url
        extras = {
            key: value
            for key, value in item.items()
            if key
            in (
                "enrichment_method",
                "enrichment_error",
                "image",
                "description",
                "original_url",
            )
            and value
        }
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content="",
            timestamp=item.get("published", ""),
            url=url,
            likes=item.get("likes"),
            comments=item.get("num_comments"),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
            external_id=story_id,
            **extras,
        )
