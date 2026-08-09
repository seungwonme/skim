"""
@file source_probe.py
@description 새 소스 URL의 피드 유무와 본문 추출 난이도를 진단한다 (읽기 전용).

등록은 하지 않는다. "이 링크를 소스로 받을 수 있나, 받으면 어느 등급인가"만 답한다.
등급(tier)은 사람이 선언하는 값이 아니라 여기서 관측한 값이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import feedparser
import requests

from .enrichment import extract_article_content
from .feed_utils import FEED_HEADERS, parse_entry_date

PROBE_TIMEOUT = 15

# 사이트 경로에 붙여보는 후보. kakao.vc는 표준 <link rel=alternate>가 없고
# /feed·/rss.xml도 404였지만 {경로}/rss(= /blog/rss)만 유효했다.
PATH_FEED_SUFFIXES = ("/rss", "/feed", "/rss.xml", "/feed.xml", "/atom.xml")

# 도메인 루트에 붙여보는 후보. tech.kakao.com은 /feed/에서 걸렸다.
ROOT_FEED_PATHS = (
    "/feed/",
    "/feed",
    "/rss",
    "/rss.xml",
    "/atom.xml",
    "/feed.xml",
    "/index.xml",
)

# 이보다 짧으면 본문이 아니라 내비게이션을 긁었다고 본다.
MIN_BODY_WORDS = 200

# 이보다 적으면 과거 글 백필이 불가능하다.
MIN_FEED_ITEMS = 20


@dataclass
class ProbeResult:
    """한 URL에 대한 진단 결과."""

    url: str
    feed_url: Optional[str] = None
    feed_title: str = ""
    site_url: str = ""
    discovery: str = ""
    items: int = 0
    undated: int = 0
    oldest: str = ""
    newest: str = ""
    has_author: bool = False
    feed_body_words: int = 0
    sample_url: str = ""
    sample_words: int = 0
    sample_method: str = ""
    sitemap_url: str = ""
    tier: str = "scrape"
    warnings: List[str] = field(default_factory=list)
    error: str = ""


def _get(url: str) -> Optional[requests.Response]:
    """진단용 GET. 실패는 예외 대신 None으로 돌려 후보 순회를 계속한다."""
    try:
        resp = requests.get(
            url, headers=FEED_HEADERS, timeout=PROBE_TIMEOUT, allow_redirects=True
        )
    except requests.RequestException:
        return None
    return resp if resp.status_code == 200 else None


def _parse_if_feed(content: bytes):
    """엔트리가 하나라도 있으면 피드로 인정한다. content-type은 믿지 않는다."""
    if not content:
        return None
    parsed = feedparser.parse(content)
    return parsed if parsed.entries else None


def _link_tag_candidates(html: str, base_url: str) -> List[str]:
    """<link rel="alternate" type="application/rss+xml"> 에서 피드 주소를 뽑는다."""
    # BeautifulSoup 대신 feedparser가 이미 의존하는 최소 파싱으로 충분하다.
    from bs4 import BeautifulSoup  # pylint: disable=import-outside-toplevel

    soup = BeautifulSoup(html, "html.parser")
    found: List[str] = []
    for tag in soup.find_all("link", attrs={"rel": True, "href": True}):
        rels = tag.get("rel")
        rels = rels if isinstance(rels, list) else [rels]
        if "alternate" not in [str(r).lower() for r in rels]:
            continue
        type_attr = (tag.get("type") or "").lower()
        if "rss" in type_attr or "atom" in type_attr or "xml" in type_attr:
            found.append(urljoin(base_url, tag["href"]))
    return found


def _candidate_urls(url: str) -> List[Tuple[str, str]]:
    """(후보 URL, 발견 경로 라벨) 목록. 경로 스코프를 루트보다 먼저 시도한다."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    candidates: List[Tuple[str, str]] = []
    if path:
        for suffix in PATH_FEED_SUFFIXES:
            candidates.append((f"{origin}{path}{suffix}", f"path:{path}{suffix}"))
    for root_path in ROOT_FEED_PATHS:
        candidates.append((f"{origin}{root_path}", f"root:{root_path}"))

    seen: set = set()
    uniq: List[Tuple[str, str]] = []
    for candidate, label in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        uniq.append((candidate, label))
    return uniq


def discover_feed(url: str) -> Tuple[Optional[str], str, object]:
    """(피드 URL, 발견 방법, 파싱된 피드)를 반환. 못 찾으면 (None, "", None)."""
    resp = _get(url)

    # 0) 준 URL 자체가 피드일 수 있다.
    if resp is not None:
        parsed = _parse_if_feed(resp.content)
        if parsed is not None:
            return resp.url, "self", parsed

    # 1) HTML의 <link rel=alternate>
    if resp is not None and "html" in (resp.headers.get("content-type") or "").lower():
        for candidate in _link_tag_candidates(resp.text, resp.url):
            probe = _get(candidate)
            if probe is None:
                continue
            parsed = _parse_if_feed(probe.content)
            if parsed is not None:
                return probe.url, "link-tag", parsed

    # 2) 관용 경로 순회
    for candidate, label in _candidate_urls(url):
        probe = _get(candidate)
        if probe is None:
            continue
        parsed = _parse_if_feed(probe.content)
        if parsed is not None:
            return probe.url, label, parsed

    return None, "", None


def find_sitemap(url: str) -> str:
    """피드가 없을 때 발행일 공급원 후보로 sitemap을 찾는다."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    candidates = [f"{origin}{path}/sitemap.xml"] if path else []
    candidates.append(f"{origin}/sitemap.xml")
    for candidate in candidates:
        resp = _get(candidate)
        if resp is not None and b"<urlset" in resp.content[:2000]:
            return resp.url
    return ""


def _entry_body_words(entry) -> int:
    """피드가 본문을 실어 보내는지(content:encoded 등) 단어 수로 확인."""
    raw = ""
    if entry.get("content"):
        raw = entry["content"][0].get("value", "") or ""
    if not raw:
        raw = entry.get("summary", "") or ""
    if not raw:
        return 0
    from bs4 import BeautifulSoup  # pylint: disable=import-outside-toplevel

    return len(BeautifulSoup(raw, "html.parser").get_text(" ", strip=True).split())


def _normalize_site_url(url: str) -> str:
    """소스 식별자로 쓸 수 있게 쿼리와 끝 슬래시를 떨어낸다."""
    parsed = urlparse(url.strip())
    if not parsed.netloc:
        return url.strip().rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def _tier_from_method(method: str) -> str:
    """관측된 추출 경로를 등급으로 옮긴다. playwright는 비용이 한 자릿수 배 차이난다."""
    if "playwright" in method:
        return "rss+render"
    return "rss+enrich"


def probe_source(url: str, sample: bool = True) -> ProbeResult:
    """URL 하나를 진단한다. 네트워크만 읽고 아무것도 저장하지 않는다."""
    result = ProbeResult(url=url)

    feed_url, discovery, feed = discover_feed(url)
    if feed is None:
        result.sitemap_url = find_sitemap(url)
        result.tier = "scrape"
        result.warnings.append(
            "RSS/Atom 피드 없음. 인덱스 파싱 크롤러를 직접 만들어야 한다"
        )
        if not result.sitemap_url:
            result.warnings.append("sitemap도 없음. 발행일 확보 수단이 마땅치 않다")
        return result

    result.feed_url = feed_url
    result.discovery = discovery

    channel = getattr(feed, "feed", {})
    result.feed_title = (channel.get("title") or "").strip()
    # 채널 <link>는 소스의 정체(사이트 홈)라 피드 주소가 바뀌어도 유지된다.
    # every.to처럼 한 도메인에 칼럼이 여럿이면 칼럼별로 다른 값이 온다.
    result.site_url = _normalize_site_url(channel.get("link") or url)

    entries = list(feed.entries)
    result.items = len(entries)

    dated = []
    for entry in entries:
        entry_dt = parse_entry_date(entry)
        if entry_dt is None:
            result.undated += 1
        else:
            dated.append(entry_dt)
    if dated:
        result.oldest = min(dated).date().isoformat()
        result.newest = max(dated).date().isoformat()

    result.has_author = any(
        (entry.get("author") or entry.get("dc_creator") or "").strip()
        for entry in entries
    )
    result.feed_body_words = max(
        (_entry_body_words(entry) for entry in entries), default=0
    )

    newest_entry = max(
        (e for e in entries if parse_entry_date(e)), key=parse_entry_date, default=None
    )
    newest_entry = (
        newest_entry if newest_entry is not None else (entries[0] if entries else None)
    )

    if result.feed_body_words >= MIN_BODY_WORDS:
        result.tier = "rss"
    elif sample and newest_entry is not None and newest_entry.get("link"):
        result.sample_url = newest_entry["link"]
        data, method, _ = extract_article_content(
            result.sample_url, newest_entry.get("title", "") or ""
        )
        result.sample_words = (data or {}).get("word_count", 0) or 0
        result.sample_method = method
        result.tier = "rss+enrich" if method == "failed" else _tier_from_method(method)
    else:
        result.tier = "rss+enrich"

    _add_warnings(result)
    return result


def _add_warnings(result: ProbeResult) -> None:
    """등급만으로는 안 보이는 운영상 제약을 경고로 남긴다."""
    if result.items < MIN_FEED_ITEMS:
        result.warnings.append(f"백필 제한: 피드가 {result.items}건만 제공")
    if result.undated:
        result.warnings.append(
            f"발행일 없는 항목 {result.undated}건. since 필터에서 조용히 제외된다"
        )
    if not result.has_author:
        result.warnings.append("피드에 author 없음. 소스명으로 대체된다")
    if result.sample_method == "failed":
        result.warnings.append("본문 추출 실패")
    elif result.sample_url and result.sample_words < MIN_BODY_WORDS:
        result.warnings.append(
            f"본문 빈약({result.sample_words}단어). 내비게이션만 긁혔을 수 있다"
        )
    if result.tier == "rss+render":
        result.warnings.append(
            "playwright 렌더 필요. 느리고 타이밍에 따라 본문이 빌 수 있다"
        )


def format_probe_result(result: ProbeResult) -> str:
    """CLI 출력용 여러 줄 문자열."""
    lines = [f"{result.url}"]
    if result.error:
        lines.append(f"  error   {result.error}")
        return "\n".join(lines)

    if result.feed_url:
        lines.append(f"  feed    {result.feed_url}  ({result.discovery})")
        span = f"{result.oldest}~{result.newest}" if result.oldest else "날짜 없음"
        lines.append(f"  items   {result.items}건  {span}")
        body = (
            f"피드에 본문 있음({result.feed_body_words}단어)"
            if result.feed_body_words >= MIN_BODY_WORDS
            else "피드는 링크만"
        )
        lines.append(f"  body    {body}")
        if result.sample_url:
            lines.append(
                f"  sample  {result.sample_words}단어  method={result.sample_method}"
            )
    else:
        lines.append("  feed    없음")
        if result.sitemap_url:
            lines.append(f"  sitemap {result.sitemap_url}")

    lines.append(f"  -> tier={result.tier}")
    for warning in result.warnings:
        lines.append(f"     경고: {warning}")
    return "\n".join(lines)
