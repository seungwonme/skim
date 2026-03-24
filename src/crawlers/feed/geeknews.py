"""
@file geeknews.py
@description GeekNews 크롤러 (HTML scraping + RSS)
"""

import re
from typing import Any, List

import requests
import typer
from bs4 import BeautifulSoup

from feed_config import GEEKNEWS_RSS

from ...enrichment import enrich_with_content
from ...feed_utils import fetch_feed
from ...models import Post

GEEKNEWS_URL = "https://news.hada.io/"


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

                    # 시간: topicinfo의 직접 텍스트에서 추출
                    info_text = topicinfo.get_text(" ", strip=True)

                    time_match = re.search(r"(\d+\s*[시분일주달개월년]\S*전)", info_text)
                    if time_match:
                        timestamp = time_match.group(1)

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
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content=item.get("title", ""),
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
        )
