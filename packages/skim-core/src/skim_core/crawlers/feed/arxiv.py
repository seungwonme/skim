"""
@file arxiv.py
@description arXiv cs.AI 논문 크롤러 (Atom API)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, List

import feedparser
import typer
from requests import RequestException

from ...enrichment import enrich_papers_with_content
from ...feed_config import ARXIV_CATEGORIES, arxiv_api_url
from ...feed_utils import (
    FEED_TIMEOUT_SECONDS,
    KST,
    is_within_range,
    make_retrying_session,
)
from ...models import Post

# export.arxiv.org는 503을 흔하게 낸다. 재시도가 없으면 그날 그 카테고리 논문이
# 영영 안 들어온다 - 카테고리당 최신 50건만 받으므로 다음 날 창이 겹쳐도
# 그 사이 제출분까지 거슬러 가지 못한다.
_SESSION = make_retrying_session()


def _fetch_category(category: str):
    """카테고리 피드를 받는다. 실패는 None으로 돌려 0건과 구분한다.

    feedparser에 URL을 직접 주면 실패해도 빈 피드를 조용히 돌려주기 때문에,
    4개 카테고리 중 하나가 죽어도 나머지가 정상 카운트를 만들어 0건 회귀 감지에
    걸리지 않는다.
    """
    try:
        resp = _SESSION.get(arxiv_api_url(category), timeout=FEED_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except RequestException as e:
        typer.echo(f"   [!] arXiv {category} 요청 실패: {e}")
        return None
    return feedparser.parse(resp.content)


class ArxivCrawler:
    platform = "arxiv"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 50)
        since = options.get("since")
        no_content = options.get("no_content", False)

        if not since:
            since = (datetime.now(KST) - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        items: List[dict] = []
        seen_urls: set[str] = set()
        for category in ARXIV_CATEGORIES:
            feed = _fetch_category(category)
            if feed is None:
                continue
            for entry in feed.entries:
                pub = entry.get("published", "")
                try:
                    entry_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
                if not is_within_range(entry_dt, since):
                    continue

                url = entry.get("link", "")
                # 논문은 여러 카테고리에 교차 등록된다. 같은 abs 링크가 두 번 들어오면
                # enrichment도 두 번 돌고 정렬 뒤 상한만 잡아먹는다.
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
                items.append(
                    {
                        "platform": "arxiv",
                        "title": re.sub(r"\s+", " ", entry.get("title", "")).strip(),
                        "url": url,
                        "author": authors,
                        "published": entry_dt.astimezone(timezone.utc).isoformat(),
                        "summary": re.sub(
                            r"\s+", " ", entry.get("summary", "")
                        ).strip()[:500],
                        "abstract": re.sub(
                            r"\s+", " ", entry.get("summary", "")
                        ).strip(),
                        "arxiv_category": category,
                    }
                )

        items.sort(key=lambda x: x.get("published", ""), reverse=True)
        items = items[:count]

        if not no_content and items:
            enrich_papers_with_content(items)

        return [self._item_to_post(item) for item in items]

    def _item_to_post(self, item: dict) -> Post:
        extras = {
            key: value
            for key, value in item.items()
            if key in ("enrichment_method", "enrichment_error", "arxiv_category")
            and value is not None
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
            **extras,
        )
