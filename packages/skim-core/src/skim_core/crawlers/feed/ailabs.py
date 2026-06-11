"""
@file ailabs.py
@description AI 빅테크 블로그/뉴스 멀티소스 크롤러 (RSS + HTML 스크래핑)

정확도 우선. 각 post 링크를 실제로 방문해 `<meta property="article:published_time">`
기반으로 정확한 발행일을 확보하고, 인덱스 페이지 DOM 구조 변경에 대한 방어선을 둔다.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...enrichment import enrich_with_content
from ...feed_config import AI_LABS_SOURCES
from ...feed_utils import fetch_feed, is_within_range
from ...models import Post

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20

_DATE_ANTHROPIC = re.compile(r"\b([A-Z][a-z]{2,9}) (\d{1,2}), (20\d\d)\b")


def _make_session() -> requests.Session:
    """재사용 가능한 retry 붙은 HTTP 세션."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_SESSION = _make_session()


def _fetch_html(url: str) -> Optional[str]:
    try:
        resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"  [!] HTTP fetch 실패 ({url[:60]}...): {e}")
        return None
    if resp.status_code != 200:
        print(f"  [!] HTTP {resp.status_code} ({url[:60]}...)")
        return None
    return resp.text


def _parse_date_text(text: str) -> Optional[datetime]:
    """'Apr 17, 2026' 또는 'April 17, 2026'을 UTC datetime으로 변환."""
    if not text:
        return None
    m = _DATE_ANTHROPIC.search(text)
    if not m:
        return None
    month, day, year = m.group(1), m.group(2), m.group(3)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(f"{month} {day}, {year}", fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_iso8601(text: str) -> Optional[datetime]:
    """ISO 8601 / RFC 3339 문자열을 UTC datetime으로 변환."""
    if not text:
        return None
    candidate = text.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fetch_html_rendered(url: str, timeout_ms: int = 30000) -> Optional[str]:
    """Playwright로 JS 렌더링 후 최종 HTML 반환.

    `networkidle`은 analytics/tracking 핑으로 끝나지 않는 페이지(OpenAI 등)에서
    타임아웃되므로 `load` + 짧은 hydration 대기로 대체한다.
    """
    try:
        # pylint: disable=import-outside-toplevel
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                page.goto(url, wait_until="load", timeout=timeout_ms)
                page.wait_for_timeout(1500)
                return page.content()
            finally:
                browser.close()
    except Exception as e:  # pylint: disable=broad-except
        print(f"  [!] playwright 렌더 실패 ({url[:60]}...): {e}")
        return None


def _parse_meta_from_html(html: str) -> Dict[str, Optional[str]]:
    """HTML에서 published_time, title, author 메타 태그를 추출."""
    soup = BeautifulSoup(html, "html.parser")

    def _meta(*keys: str) -> Optional[str]:
        for key in keys:
            el = soup.find("meta", attrs={"property": key})
            if el and el.get("content"):
                return el["content"].strip()
            el = soup.find("meta", attrs={"name": key})
            if el and el.get("content"):
                return el["content"].strip()
        return None

    published = _meta(
        "article:published_time",
        "og:article:published_time",
        "article:published",
        "pubdate",
        "date",
    )
    if not published:
        time_el = soup.find("time", attrs={"datetime": True})
        if time_el:
            published = time_el.get("datetime")

    if not published:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text() or ""
            if "datePublished" not in raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            candidates: List[Dict[str, Any]] = []
            if isinstance(payload, list):
                candidates.extend(p for p in payload if isinstance(p, dict))
            elif isinstance(payload, dict):
                candidates.append(payload)
                graph = payload.get("@graph")
                if isinstance(graph, list):
                    candidates.extend(p for p in graph if isinstance(p, dict))
            for node in candidates:
                date = node.get("datePublished")
                if date:
                    published = date
                    break
            if published:
                break

    title = _meta("og:title", "twitter:title") or (
        soup.title.get_text(strip=True) if soup.title else None
    )
    author = _meta("article:author", "author")

    return {"published": published, "title": title, "author": author}


@lru_cache(maxsize=8)
def _fetch_sitemap_lastmod_map(sitemap_url: str) -> Dict[str, str]:
    """sitemap.xml을 받아 URL → lastmod ISO 문자열 dict로 반환."""
    html = _fetch_html(sitemap_url)
    if not html:
        return {}
    mapping: Dict[str, str] = {}
    for m in re.finditer(
        r"<url>\s*<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>",
        html,
    ):
        mapping[m.group(1).strip()] = m.group(2).strip()
    return mapping


_SITEMAP_URLS = {
    "www.anthropic.com": "https://www.anthropic.com/sitemap.xml",
    "www.langchain.com": "https://www.langchain.com/sitemap.xml",
    "openai.com": "https://openai.com/sitemap.xml",
}


def _sitemap_published(url: str) -> Optional[str]:
    """사이트맵에서 lastmod을 조회. 발행일의 근사치로 사용."""
    host = urlparse(url).netloc
    sitemap_url = _SITEMAP_URLS.get(host)
    if not sitemap_url:
        return None
    mapping = _fetch_sitemap_lastmod_map(sitemap_url)
    return mapping.get(url)


@lru_cache(maxsize=512)
def _fetch_article_metadata(url: str) -> Dict[str, Optional[str]]:
    """
    article 페이지에서 published_time, title, author 메타데이터를 추출한다.
    1) 빠른 HTTP fetch 후 meta 태그 파싱
    2) published 못 찾으면 Playwright로 JS 렌더링 후 재파싱
    lru_cache로 동일 URL 재요청을 방지한다.
    """
    empty: Dict[str, Optional[str]] = {"published": None, "title": None, "author": None}

    html = _fetch_html(url)
    baseline = _parse_meta_from_html(html) if html else dict(empty)

    # 1) HTTP-fetched article meta (ISO published_time / ld+json) 즉시 사용
    if baseline.get("published"):
        return baseline

    # 2) 사이트맵 `<lastmod>`. 지원 소스(Anthropic/LangChain/OpenAI)는 sitemap이
    #    실질적으로 첫 게시일 근사치이고, HTTP 한 번이면 끝나 비용이 작다.
    #    `_resolve_entry_datetime`에서 anchor_text_date가 있으면 anchor가 우선한다.
    sitemap_date = _sitemap_published(url)
    if sitemap_date:
        baseline["published"] = sitemap_date
        return baseline

    # 3) 마지막 수단: Playwright 렌더링 (expensive). sitemap 커버리지 밖 URL 전용.
    rendered = _fetch_html_rendered(url)
    if rendered:
        rendered_meta = _parse_meta_from_html(rendered)
        if rendered_meta.get("published"):
            return {
                "published": rendered_meta["published"],
                "title": rendered_meta.get("title") or baseline.get("title"),
                "author": rendered_meta.get("author") or baseline.get("author"),
            }
        if rendered_meta.get("title") and not baseline.get("title"):
            baseline["title"] = rendered_meta["title"]
        if rendered_meta.get("author") and not baseline.get("author"):
            baseline["author"] = rendered_meta["author"]
    return baseline


def _resolve_entry_datetime(
    anchor_text_date: Optional[datetime],
    url: str,
) -> Optional[datetime]:
    """발행일 신뢰도 우선순위.

    1) article meta의 ISO published_time (HTTP 또는 Playwright) — 가장 정확
    2) 인덱스 페이지 anchor text 날짜 — 소스가 큐레이션한 날짜
    3) sitemap `<lastmod>` — 수정 시각일 수 있어 최후 수단
    """
    meta = _fetch_article_metadata(url)
    published_raw = meta.get("published") or ""
    iso_dt = _parse_iso8601(published_raw)
    is_sitemap_only = (
        bool(_sitemap_published(url)) and iso_dt and _sitemap_published(url) == published_raw
    )

    # anchor 텍스트가 있고 article meta가 sitemap lastmod 밖에 없으면 anchor 우선
    if anchor_text_date and is_sitemap_only:
        return anchor_text_date

    if iso_dt:
        return iso_dt
    if anchor_text_date:
        return anchor_text_date
    return _parse_date_text(published_raw)


def _external_id_from_url(url: str) -> str:
    """URL을 플랫폼 고유 ID로 변환. 경로를 유지해 쿼리스트링 변동을 흡수한다."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return parsed.netloc
    return f"{parsed.netloc}{path}"


# === 소스별 인덱스 파서 ===================================================


def _parse_anthropic_index(source: dict, html: str) -> List[Dict[str, Any]]:
    """Anthropic /news·/research·/engineering 인덱스에서 (url, anchor_date_text) 수집.

    동일 post가 Featured(날짜 없음)와 PublicationList(날짜 있음)에 중복 렌더링되므로
    각 URL에 대해 '날짜가 있는 anchor'를 우선 채택한다.
    """
    soup = BeautifulSoup(html, "html.parser")
    section_path = urlparse(source["url"]).path.rstrip("/") + "/"

    collected: Dict[str, Dict[str, Any]] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not href.startswith(section_path):
            continue

        anchor_text = anchor.get_text(" ", strip=True)
        anchor_date = _parse_date_text(anchor_text)

        title_hint = None
        for tag in ("h1", "h2", "h3", "h4"):
            el = anchor.find(tag)
            if el:
                t = el.get_text(strip=True)
                if t:
                    title_hint = t
                    break
        if not title_hint:
            title_el = anchor.select_one('[class*="title"]')
            if title_el:
                title_hint = title_el.get_text(strip=True)

        existing = collected.get(href)
        if existing is None:
            collected[href] = {
                "href": href,
                "absolute_url": (
                    href if href.startswith("http") else f"https://www.anthropic.com{href}"
                ),
                "anchor_date": anchor_date,
                "title_hint": title_hint,
                "default_author": "Anthropic",
            }
            continue

        # 이미 본 URL이면: 날짜가 비어있던 항목에 날짜를 채우고, 더 구체적인 title로 보강
        if existing.get("anchor_date") is None and anchor_date is not None:
            existing["anchor_date"] = anchor_date
        if not existing.get("title_hint") and title_hint:
            existing["title_hint"] = title_hint

    return list(collected.values())


def _parse_langchain_index(source: dict, html: str) -> List[Dict[str, Any]]:
    """LangChain /blog 인덱스에서 (url, anchor_date_text, title_hint) 수집."""
    del source
    soup = BeautifulSoup(html, "html.parser")

    seen: set = set()
    entries: List[Dict[str, Any]] = []
    for container in soup.select(".w-dyn-item"):
        anchor = container.find(
            "a",
            href=lambda h: (
                h and h.startswith("/blog/") and "/blog/category/" not in h and h != "/blog/"
            ),
        )
        h2 = container.find("h2")
        if not anchor or not h2:
            continue
        href = anchor["href"]
        if href in seen:
            continue
        seen.add(href)

        anchor_text = container.get_text(" ", strip=True)
        entries.append(
            {
                "href": href,
                "absolute_url": f"https://www.langchain.com{href}",
                "anchor_date": _parse_date_text(anchor_text),
                "title_hint": h2.get_text(strip=True),
                "default_author": "LangChain",
            }
        )
    return entries


# === 소스 타입별 수집 ======================================================


def _collect_from_rss(source: dict, since: datetime, limit: Optional[int] = None) -> List[dict]:
    """RSS 소스(OpenAI 등)."""
    results = fetch_feed(source["url"], f"ailabs/{source['name']}", since)
    return results[:limit] if limit else results


def _collect_from_html(
    source: dict,
    since: datetime,
    index_parser,
    *,
    limit: Optional[int] = None,
    fast_metadata: bool = False,
) -> List[dict]:
    """HTML 스크래핑 + article metadata fallback 공통 경로."""
    html = _fetch_html(source["url"])
    if not html:
        return []

    results: List[dict] = []
    for entry in index_parser(source, html):
        url = entry["absolute_url"]
        anchor_date = entry.get("anchor_date")
        if anchor_date and not is_within_range(anchor_date, since):
            continue

        meta: Dict[str, Optional[str]] = {}
        if fast_metadata and anchor_date:
            entry_dt = anchor_date
        else:
            entry_dt = _resolve_entry_datetime(anchor_date, url)
        if not is_within_range(entry_dt, since):
            continue

        if not fast_metadata:
            meta = _fetch_article_metadata(url)
        title = (meta.get("title") or entry.get("title_hint") or "").strip()
        if not title:
            continue
        author = (meta.get("author") or entry.get("default_author") or "").strip()

        results.append(
            {
                "platform": f"ailabs/{source['name']}",
                "title": title,
                "url": url,
                "author": author,
                "published": entry_dt.astimezone(timezone.utc).isoformat() if entry_dt else "",
                "summary": "",
                "external_id": _external_id_from_url(url),
            }
        )
        if limit and len(results) >= limit:
            break
    return results


def _dispatch(
    source: dict,
    since: datetime,
    *,
    limit: Optional[int] = None,
    fast_metadata: bool = False,
) -> Iterable[dict]:
    t = source.get("type")
    if t == "rss":
        results = _collect_from_rss(source, since, limit=limit)
        for item in results:
            if item.get("url") and not item.get("external_id"):
                item["external_id"] = _external_id_from_url(item["url"])
        return results
    if t == "anthropic":
        return _collect_from_html(
            source,
            since,
            _parse_anthropic_index,
            limit=limit,
            fast_metadata=fast_metadata,
        )
    if t == "langchain":
        return _collect_from_html(
            source,
            since,
            _parse_langchain_index,
            limit=limit,
            fast_metadata=fast_metadata,
        )
    return []


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
        if key in ("enrichment_method", "enrichment_error", "description", "image", "original_url")
        and value is not None
    }
    return Post(
        platform="ailabs",
        author=item.get("author", ""),
        title=item.get("title", ""),
        content=item.get("title", ""),
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
    """AI 빅테크 뉴스 통합 크롤러 (OpenAI / Anthropic / LangChain)."""

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
                        fast_metadata=no_content,
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

        if not all_items:
            return []

        all_items.sort(key=lambda x: x.get("published", ""), reverse=True)

        if not no_content:
            enrich_with_content(all_items)

        return [_item_to_post(item) for item in all_items]
