#!/usr/bin/env python
"""likes/comments가 빈 hackernews·geeknews 행을 되긁어 채운다.

RSS 경로(`--days`)가 지표를 싣지 않아 2026-08-08 이전 수집분은 likes/comments가
전부 0으로 저장됐다. 크롤러 쪽은 고쳤지만(fetch_hn_metrics / fetch_geeknews_metrics)
그건 앞으로 수집분에만 적용된다. 이 스크립트가 과거분을 채운다.

크롤러와 같은 함수를 그대로 부른다. 파싱 규칙이 갈라지지 않게 하기 위해서다.

id를 못 뽑는 행은 건너뛴다. hackernews의 external_id는 story id가 아니라 RSS guid
해시라서, 원문 URL만 있고 HN 토론 URL이 없는 행은 story id를 복원할 수 없다.

재실행해도 안전하다. 이미 채워진 행은 대상에서 빠진다.

사용:
    uv run python scripts/backfill_feed_metrics.py --dry-run
    uv run python scripts/backfill_feed_metrics.py --platform geeknews --limit 50
    uv run python scripts/backfill_feed_metrics.py            # 전체
"""

import argparse
import re
import sys
import time
from typing import Dict, List, Optional

from skim_core.crawlers.feed.geeknews import fetch_geeknews_metrics, topic_id_from_url
from skim_core.crawlers.feed.hackernews import fetch_hn_metrics
from skim_core.db import get_connection

# GeekNews는 개인이 운영하는 사이트다. 3천 건을 몰아치지 않는다.
# 0.5초로 돌렸다가 2026-08-08에 403으로 차단당했다. 그 뒤로 여유를 크게 뒀다.
DELAY = {"hackernews": 0.1, "geeknews": 3.0}

# 차단당한 뒤에도 계속 두들기면 차단이 길어진다. 연속 실패가 이만큼 쌓이면 멈춘다.
# 위 사고에서 차단 후 2,057건을 더 요청했다. 그 재발을 막는 것이 이 상수의 목적이다.
MAX_CONSECUTIVE_FAILURES = 5

SUPPORTED = ("hackernews", "geeknews")


def story_id_from_url(url: str) -> Optional[str]:
    """HN 토론 URL에서 story id를 뽑는다. 원문 URL만 있으면 복원 못 한다."""
    match = re.search(r"item\?id=(\d+)", url or "")
    return match.group(1) if match else None


def fetch_targets(conn, platform: Optional[str], limit: int) -> List[Dict]:
    """지표가 빈 행. 0과 NULL 둘 다 미수집으로 본다.

    HN은 최소 1점, GeekNews는 최소 1P라서 0점인 글은 존재하지 않는다.
    """
    platforms = [platform] if platform else list(SUPPORTED)
    sql = (
        "SELECT id, platform, url FROM posts "
        f"WHERE platform IN ({','.join('?' for _ in platforms)}) "
        "AND COALESCE(likes, 0) = 0 AND COALESCE(url, '') <> '' "
        "ORDER BY timestamp DESC"
    )
    params: list = list(platforms)
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def metrics_for(row: Dict) -> Optional[dict]:
    if row["platform"] == "hackernews":
        story_id = story_id_from_url(row["url"])
        return fetch_hn_metrics(story_id) if story_id else None
    topic_id = topic_id_from_url(row["url"])
    return fetch_geeknews_metrics(topic_id) if topic_id else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=SUPPORTED)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_connection()
    targets = fetch_targets(conn, args.platform, args.limit)
    if not targets:
        print("채울 행이 없습니다.")
        return 0

    skipped = [r for r in targets if not metrics_id_available(r)]
    print(f"대상 {len(targets)}건 (id 복원 불가로 건너뜀 {len(skipped)}건)")
    if args.dry_run:
        for row in targets[:5]:
            print(f"  {row['platform']} {row['url'][:70]}")
        return 0

    filled = failed = streak = 0
    for i, row in enumerate(targets, 1):
        if not metrics_id_available(row):
            continue
        metrics = metrics_for(row)
        if metrics and metrics.get("likes") is not None:
            conn.execute(
                "UPDATE posts SET likes = ?, comments = ? WHERE id = ?",
                (metrics["likes"], metrics.get("comments"), row["id"]),
            )
            filled += 1
            streak = 0
        else:
            failed += 1
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                conn.commit()
                print(
                    f"연속 {streak}건 실패로 중단합니다 ({row['platform']}). "
                    "차단당했을 수 있으니 시간을 두고 재실행하세요."
                )
                break
        if filled and filled % 25 == 0:
            conn.commit()
            print(f"   [{i}/{len(targets)}] 채움 {filled} / 실패 {failed}")
        time.sleep(DELAY[row["platform"]])

    conn.commit()
    print(f"완료: 채움 {filled} / 실패 {failed} / 건너뜀 {len(skipped)}")
    return 0


def metrics_id_available(row: Dict) -> bool:
    if row["platform"] == "hackernews":
        return story_id_from_url(row["url"]) is not None
    return topic_id_from_url(row["url"]) is not None


if __name__ == "__main__":
    sys.exit(main())
