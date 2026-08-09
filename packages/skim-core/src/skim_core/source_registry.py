"""
@file source_registry.py
@description 크롤러가 "무엇을 수집할지"를 해석하는 공통 경로.

정본은 DB의 `tracked_sources`다. `skim source add`로 등록한 소스가 데일리 수집에
들어오지 않으면 등록 자체가 무의미해지므로, 멀티소스 크롤러는 전부 여기를 지난다.
레지스트리가 비었거나 DB를 못 읽을 때만 `feed_config.py`의 seed로 폴백한다.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .db import list_tracked_sources

# 피드 크롤러가 다루는 source_type. 이 값이 아니면 전용 파서가 필요한 소스라
# 피드 순회 대상에서 뺀다 (예: anthropic 인덱스 스크레이핑).
FEED_SOURCE_TYPE = "feed"


def resolve_feed_sources(
    platform: str,
    fallback: Dict[str, str],
    *,
    db_path=None,
    quiet: bool = False,
) -> List[dict]:
    """플랫폼의 수집 대상을 {name, feed_url, source_type} 목록으로 돌려준다.

    feed_url이 빈 등록 행은 건너뛰되 이름을 찍는다. 데스크톱 앱의 소스 UI는
    youtube 채널용이라 feed_url을 안 채우는데, 조용히 빠지면 "등록했는데 왜
    안 들어오지"가 된다.
    """
    rows = list_tracked_sources(platform, db_path=db_path)

    usable = [
        {
            "name": row["display_name"],
            "feed_url": row["feed_url"],
            "source_type": row.get("source_type") or FEED_SOURCE_TYPE,
        }
        for row in rows
        if row.get("feed_url")
        and (row.get("source_type") or FEED_SOURCE_TYPE) == FEED_SOURCE_TYPE
    ]

    skipped = [row["display_name"] for row in rows if not row.get("feed_url")]
    if skipped and not quiet:
        print(
            f"  [!] feed_url 없는 등록 소스 {len(skipped)}개 건너뜀: {', '.join(skipped)}"
        )
        print("      `skim source add <url>` 로 다시 등록하면 피드 주소가 채워진다.")

    if usable:
        return usable

    return [
        {"name": name, "feed_url": url, "source_type": FEED_SOURCE_TYPE}
        for name, url in fallback.items()
    ]


def seed_rows_for(platform: str, fallback: Dict[str, str]) -> List[dict]:
    """`skim source sync`가 등록할 후보 (아직 레지스트리에 없는 것만)."""
    known = {
        row.get("feed_url")
        for row in list_tracked_sources(platform, enabled_only=False)
        if row.get("feed_url")
    }
    return [
        {"name": name, "feed_url": url}
        for name, url in fallback.items()
        if url not in known
    ]


def registry_platforms() -> Dict[str, Optional[Dict[str, str]]]:
    """레지스트리로 관리하는 플랫폼과 그 seed 설정.

    reddit/threads/x/linkedin은 구독 목록이 플랫폼 계정에 있어 skim이 소유할 수
    없다. 여기 넣으면 실제와 어긋난 목록이 되므로 뺀다.
    """
    # 순환 import 방지: feed_config는 db를 안 쓰지만 호출 시점에 읽는다.
    from .feed_config import (  # pylint: disable=import-outside-toplevel
        EVERY_TO_FEEDS,
        PERSONAL_BLOGS,
    )

    return {
        "blogs": PERSONAL_BLOGS,
        "everyto": EVERY_TO_FEEDS,
    }
