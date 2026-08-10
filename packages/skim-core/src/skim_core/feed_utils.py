"""
@file feed_utils.py
@description RSS/Atom 피드 파싱 유틸리티
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Backward-compat export: 다른 크롤러가 입력 측 윈도우 계산에 KST 사용.
# 저장 측은 UTC ISO 8601 로 강제 (fetch_feed `published` 필드).
KST = timezone(timedelta(hours=9))
FEED_TIMEOUT_SECONDS = 15
# news.hada.io는 브라우저 토큰뿐 아니라 Chrome 메이저 버전도 본다. 2026-08-09부터
# Chrome/124가 403으로 막혀 그날 지표 수집이 절반 실패했다(128 이상은 통과).
# 차단선이 다시 올라가면 이 버전을 올린다.
#
# 이 상수가 공개 소스 요청의 단일 UA다. 예전에는 enrichment, ailabs, playwright
# 컨텍스트가 각자 Chrome/124를 들고 있어서, 여기 버전을 올려도 그쪽은 계속 막혔다.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
FEED_HEADERS = {"User-Agent": USER_AGENT}


def make_retrying_session(extra_headers: Optional[dict] = None) -> requests.Session:
    """429/5xx에 지수 백오프로 재시도하는 HTTP 세션.

    단발 요청이면 503 한 번에 그 소스의 그날 수집분이 빈 리스트로 끝난다. 데일리가
    고정 창으로 돌아 다음 날 창에는 그 항목이 다시 안 들어오므로 그대로 영구 유실이다.

    세션 쿠키로 계정이 식별되는 API 크롤러(threads/x/linkedin/reddit)에는 쓰지 않는다.
    거기서 자동 재시도는 차단 신호를 무시하고 계속 두드리는 것과 같다.
    """
    session = requests.Session()
    session.headers.update(FEED_HEADERS)
    if extra_headers:
        session.headers.update(extra_headers)
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_FEED_SESSION = make_retrying_session()


def parse_entry_date(entry) -> Optional[datetime]:
    """피드 엔트리에서 datetime 객체 추출 (UTC 변환)"""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def is_within_range(entry_dt: Optional[datetime], since: datetime) -> bool:
    if not entry_dt:
        return False
    return entry_dt >= since


def fetch_feed(
    url: str, source_name: str, since: datetime, quiet: bool = False
) -> List[dict]:
    """RSS/Atom 피드를 가져와서 since 이후 항목만 반환"""
    try:
        response = _FEED_SESSION.get(url, timeout=FEED_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        if not quiet:
            print(f"  [!] {source_name}: 피드 요청 실패 - {exc}")
        return []

    feed = feedparser.parse(response.content)

    if feed.bozo and not feed.entries:
        if not quiet:
            print(f"  [!] {source_name}: 피드 파싱 실패 - {feed.bozo_exception}")
        return []

    results = []
    skipped_undated = 0
    for entry in feed.entries:
        entry_dt = parse_entry_date(entry)
        if entry_dt is None:
            # 날짜 없는 엔트리는 since 판정이 불가능해 제외한다. 조용히 사라지지 않게 집계.
            skipped_undated += 1
            continue
        if not is_within_range(entry_dt, since):
            continue

        content_html = ""
        if entry.get("content"):
            content_html = entry["content"][0].get("value", "")
        if not content_html:
            content_html = entry.get("summary", "")

        results.append(
            {
                "platform": source_name,
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "content_html": content_html,
                # author를 안 싣는 피드(OpenAI News, 1인 블로그 등)가 있다. 빈 값으로
                # 두면 읽기 쪽 "작성자"가 공백이 되므로 소스 표시명으로 채운다.
                "author": (
                    entry.get("author", "")
                    or (
                        entry.get("authors", [{}])[0].get("name", "")
                        if entry.get("authors")
                        else ""
                    )
                    or source_name.split("/")[-1]
                ),
                "external_id": entry.get("id", ""),
                "published": entry_dt.isoformat() if entry_dt else "",
                "summary": (
                    re.sub(
                        r"\s+", " ", re.sub(r"<[^>]+>", "", entry.get("summary") or "")
                    ).strip()[:300]
                ),
            }
        )

    if skipped_undated and not quiet:
        print(f"  [!] {source_name}: 날짜 없는 엔트리 {skipped_undated}개 제외")

    return results
