"""
@file ailabs.py
@description AI 빅테크 블로그 RSS 멀티소스 크롤러

소스는 `feed_config.AI_LABS_SOURCES`에 있고 전부 RSS다. Anthropic, OpenAI는 별도
미러가 받아서 넣지 않는다 (#82). 그 두 소스를 위해 두던 인덱스 HTML 파싱, 사이트맵,
Playwright 발행일 조회 경로도 함께 지웠다.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional
from urllib.parse import urlparse

from ...enrichment import enrich_with_content
from ...feed_config import AI_LABS_SOURCES
from ...feed_utils import fetch_feed, finish_feed_items
from ...models import Post


def _external_id_from_url(url: str) -> str:
    """URL을 플랫폼 고유 ID로 변환. 경로를 유지해 쿼리스트링 변동을 흡수한다."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return parsed.netloc
    return f"{parsed.netloc}{path}"


# === 수집 ======================================================


def _collect_from_rss(
    source: dict, since: datetime, limit: Optional[int] = None
) -> List[dict]:
    """RSS 소스."""
    results = fetch_feed(source["url"], f"ailabs/{source['name']}", since)
    return results[:limit] if limit else results


def _dispatch(
    source: dict,
    since: datetime,
    *,
    limit: Optional[int] = None,
) -> Iterable[dict]:
    if source.get("type") != "rss":
        return []
    results = _collect_from_rss(source, since, limit=limit)
    for item in results:
        # 피드 guid가 아니라 URL로 고정한다. 소스가 RSS/HTML 경로를 오가면
        # id 체계가 바뀌어 같은 글이 두 행으로 갈라진다 (실측 182행 중복).
        if item.get("url"):
            item["external_id"] = _external_id_from_url(item["url"])
    return results


def _dedupe_by_url(items: List[dict]) -> List[dict]:
    """URL 기준 중복 제거. 여러 소스에 같은 글이 실릴 때 enrichment 중복 방지."""
    seen: set = set()
    uniq: List[dict] = []
    for item in items:
        url = item.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        uniq.append(item)
    return uniq


def _item_to_post(item: dict) -> Post:
    extras = {
        key: value
        for key, value in item.items()
        if key
        in (
            "enrichment_method",
            "enrichment_error",
            "description",
            "image",
            "original_url",
        )
        and value is not None
    }
    return Post(
        platform="ailabs",
        author=item.get("author", ""),
        title=item.get("title", ""),
        content="",
        timestamp=item.get("published", ""),
        url=item.get("url", ""),
        summary=item.get("summary", ""),
        source=item.get("platform", ""),
        external_id=item.get("external_id"),
        content_markdown=item.get("content_markdown", ""),
        word_count=item.get("word_count"),
        **extras,
    )


class AILabsCrawler:
    """AI 빅테크 블로그 통합 크롤러 (LangChain, DeepMind, Google Research 등)."""

    platform: str = "ailabs"

    async def crawl(self, **options: Any) -> List[Post]:
        """
        Options:
            since (datetime): 이 시점 이후의 글만 수집
            no_content (bool): enrichment 스킵
            debug (bool): 디버그 출력

        sync_playwright()를 asyncio 이벤트 루프 안에서 호출하면 실패하므로
        동기 크롤링 로직을 별도 스레드에서 실행한다.
        """
        return await asyncio.to_thread(self._crawl_sync, options)

    def _crawl_sync(self, options: dict) -> List[Post]:
        since: datetime = options.get(
            "since",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        no_content: bool = options.get("no_content", False)
        debug: bool = options.get("debug", False)
        count: Optional[int] = options.get("count")

        if debug:
            print(f"[AILabs] {len(AI_LABS_SOURCES)}개 소스 수집 중...")

        all_items: List[dict] = []
        for source in AI_LABS_SOURCES:
            try:
                results = list(
                    _dispatch(
                        source,
                        since,
                        limit=count,
                    )
                )
            except Exception as e:  # pylint: disable=broad-except
                print(f"  [!] {source['name']} 실패: {e}")
                continue
            if debug:
                print(f"  -> {source['name']}: {len(results)}개")
            all_items.extend(results)

        all_items = _dedupe_by_url(all_items)

        if debug:
            print(f"  -> 중복제거 후 {len(all_items)}개 글")

        # 소스별 limit(=count)은 과수집 방지용이고, count는 전역 상한이다.
        # 소스 N개면 최대 N*count개가 모이므로 여기서 한 번 더 자른다.
        return finish_feed_items(
            all_items,
            _item_to_post,
            enrich=enrich_with_content,
            count=options.get("count"),
            no_content=no_content,
        )
