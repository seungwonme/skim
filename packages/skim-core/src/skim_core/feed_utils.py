"""
@file feed_utils.py
@description RSS/Atom 피드 파싱 유틸리티
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import feedparser
import requests

# Backward-compat export: 다른 크롤러가 입력 측 윈도우 계산에 KST 사용.
# 저장 측은 UTC ISO 8601 로 강제 (fetch_feed `published` 필드).
KST = timezone(timedelta(hours=9))
FEED_TIMEOUT_SECONDS = 15
FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


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


def fetch_feed(url: str, source_name: str, since: datetime, quiet: bool = False) -> List[dict]:
    """RSS/Atom 피드를 가져와서 since 이후 항목만 반환"""
    try:
        response = requests.get(url, headers=FEED_HEADERS, timeout=FEED_TIMEOUT_SECONDS)
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
                "author": (
                    entry.get("author", "")
                    or (
                        entry.get("authors", [{}])[0].get("name", "")
                        if entry.get("authors")
                        else ""
                    )
                ),
                "external_id": entry.get("id", ""),
                "published": entry_dt.isoformat() if entry_dt else "",
                "summary": (
                    re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", entry.get("summary") or "")).strip()[
                        :300
                    ]
                ),
            }
        )

    if skipped_undated and not quiet:
        print(f"  [!] {source_name}: 날짜 없는 엔트리 {skipped_undated}개 제외")

    return results
