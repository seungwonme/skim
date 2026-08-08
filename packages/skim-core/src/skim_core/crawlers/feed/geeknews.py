"""
@file geeknews.py
@description GeekNews 크롤러 (HTML scraping + RSS)
"""

import re
from typing import Any, List, Optional

import requests
import typer
from bs4 import BeautifulSoup

from ...enrichment import enrich_with_content
from ...feed_config import GEEKNEWS_RSS
from ...feed_utils import fetch_feed
from ...models import Post
from ...timestamp import _REL_KO, relative_ko_to_iso

GEEKNEWS_URL = "https://news.hada.io/"
GEEKNEWS_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
_TOPIC_ID = re.compile(r"topic\?id=(\d+)")


def topic_id_from_url(url: str) -> Optional[str]:
    """GeekNews 토픽 URL에서 숫자 id를 뽑는다."""
    match = _TOPIC_ID.search(url or "")
    return match.group(1) if match else None


def _digits_to_int(raw: str) -> Optional[int]:
    digits = "".join(c for c in raw or "" if c.isdigit())
    return int(digits) if digits else None


def fetch_geeknews_metrics(topic_id: str) -> Optional[dict]:
    """토픽 페이지에서 포인트와 댓글 수를 가져온다.

    RSS는 지표를 싣지 않아 `--days` 경로로 저장한 행은 likes/comments가 비어 있었다.
    홈페이지 스크래핑 경로만 지표를 채우던 비대칭을 없앤다.
    """
    try:
        resp = requests.get(
            f"{GEEKNEWS_URL}topic?id={topic_id}",
            headers={"User-Agent": GEEKNEWS_UA},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:  # pylint: disable=broad-except
        typer.echo(f"   [!] GeekNews 지표 수집 실패(id={topic_id}): {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # 포인트는 `<span id='tp{id}'>3</span>P` 형태로만 노출된다.
    point_el = soup.select_one(f"#tp{topic_id}")
    likes = _digits_to_int(point_el.get_text(strip=True)) if point_el else None

    # 댓글 수는 링크 텍스트("댓글 3개")보다 data 속성이 안정적이다.
    comment_el = soup.select_one("[data-topic-comment-count]")
    comments = _digits_to_int(comment_el.get("data-topic-comment-count")) if comment_el else None

    if likes is None and comments is None:
        return None
    return {"likes": likes, "comments": comments}


class GeekNewsCrawler:
    platform = "geeknews"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 30)
        since = options.get("since")
        no_content = options.get("no_content", False)

        if since:
            items = fetch_feed(GEEKNEWS_RSS, "geeknews", since)
            items.sort(key=lambda x: x.get("published", ""), reverse=True)
            if not no_content:
                enrich_with_content(items)
                for item in items:
                    topic_id = topic_id_from_url(item.get("url", ""))
                    metrics = fetch_geeknews_metrics(topic_id) if topic_id else None
                    if metrics:
                        item["likes"] = metrics["likes"]
                        item["comments"] = metrics["comments"]
            return [self._item_to_post(item) for item in items]
        else:
            return self._scrape_homepage(count)

    def _scrape_homepage(self, count: int) -> List[Post]:
        """GeekNews 메인 페이지에서 게시글을 HTML 스크래핑합니다."""
        typer.echo(f"GeekNews에서 상위 {count}개 게시글을 가져옵니다...")

        resp = requests.get(
            GEEKNEWS_URL,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=10,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        topic_rows = soup.select(".topic_row")

        posts: List[Post] = []
        for i, row in enumerate(topic_rows[:count]):
            try:
                # 제목 + 링크
                title_el = row.select_one(".topictitle a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                url = title_el.get("href", "")
                if url and not url.startswith("http"):
                    url = f"https://news.hada.io{url}"

                # 요약 텍스트
                desc_el = row.select_one(".topicdesc a")
                description = desc_el.get_text(strip=True).lstrip("- ") if desc_el else ""

                # 메타 정보 (.topicinfo: 포인트, 작성자, 시간, 댓글)
                topicinfo = row.select_one(".topicinfo")
                author = ""
                timestamp = ""
                likes = 0
                comments = 0

                if topicinfo:
                    # 포인트
                    point_el = topicinfo.select_one("span")
                    if point_el:
                        try:
                            likes = int(point_el.get_text(strip=True))
                        except ValueError:
                            pass

                    # 작성자
                    user_link = topicinfo.select_one('a[href*="/user?"]')
                    if user_link:
                        author = user_link.get_text(strip=True)

                    # 시간: topicinfo의 직접 텍스트에서 추출 → UTC ISO 8601 정규화
                    info_text = topicinfo.get_text(" ", strip=True)

                    rel_match = _REL_KO.search(info_text)
                    if rel_match:
                        timestamp = relative_ko_to_iso(rel_match.group(0)) or ""

                    # 댓글
                    comment_link = topicinfo.select_one('a[href*="go=comments"]')
                    if comment_link:
                        comment_text = comment_link.get_text(strip=True)
                        digits = "".join(c for c in comment_text if c.isdigit())
                        comments = int(digits) if digits else 0

                # content: 제목 + 요약
                content = title if not description else f"{title}\n{description}"

                post = Post(
                    platform="geeknews",
                    author=author or "unknown",
                    content=content,
                    timestamp=timestamp,
                    url=url,
                    likes=likes,
                    comments=comments,
                )
                posts.append(post)
                typer.echo(f"   [{i + 1}/{count}] {title[:60]}")
            except Exception as e:
                typer.echo(f"   [{i + 1}/{count}] 스킵 (에러: {e})")

        return posts

    def _item_to_post(self, item: dict) -> Post:
        extras = {
            key: value
            for key, value in item.items()
            if key
            in (
                "original_url",
                "enrichment_method",
                "enrichment_error",
                "image",
                "description",
            )
            and value
        }
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content="",
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            likes=item.get("likes"),
            comments=item.get("comments"),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
            **extras,
        )
