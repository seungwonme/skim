"""
@file feed_utils.py
@description RSS/Atom 피드 파싱 유틸리티
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import feedparser

KST = timezone(timedelta(hours=9))


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
    feed = feedparser.parse(url)

    if feed.bozo and not feed.entries:
        if not quiet:
            print(f"  [!] {source_name}: 피드 파싱 실패 - {feed.bozo_exception}")
        return []

    results = []
    for entry in feed.entries:
        entry_dt = parse_entry_date(entry)
        if not is_within_range(entry_dt, since):
            continue

        results.append(
            {
                "platform": source_name,
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "author": (
                    entry.get("author", "")
                    or (
                        entry.get("authors", [{}])[0].get("name", "")
                        if entry.get("authors")
                        else ""
                    )
                ),
                "published": entry_dt.astimezone(KST).isoformat() if entry_dt else "",
                "summary": (
                    re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", entry.get("summary") or "")).strip()[
                        :300
                    ]
                ),
            }
        )

    return results
