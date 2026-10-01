#!/usr/bin/env python
"""본문이 RSS 요약 조각뿐인 GeekNews 행에 원문을 붙인다 (#33).

토픽 페이지가 막혔던 2026-08~09에 저장된 GeekNews 행은 본문이 RSS 요약 조각뿐이고
(`enrichment_method="failed"`) 원문 링크도 없다. 2026-10-01 기준 936건이다. 토픽
페이지는 20시간 창에 25건이라 이걸 채울 여력이 없다. 목록(`/newest`)에는 제목
링크로 원문이 있고 토픽 한도를 쓰지 않는다.

  1. 원문 링크를 이미 아는 행은 목록 없이 원문만 다시 받는다.
  2. 나머지는 목록을 거슬러 읽는다. 한 장을 읽을 때마다 그 장에서 찾은 행의 원문을
     받아 저장한다. 중간에 막혀도 그때까지 채운 행은 남는다.
  3. 원문 링크가 없는 자체 글(Show GN, Ask GN)은 토픽 페이지로만 채울 수 있다.
     `extra.listing_checked = "self"`를 남겨 다시 찾지 않는다. 토픽 백필이 먼저 받는다.
  4. 읽은 목록 한 장의 id 범위 안에 있는데 목록에 없는 글은 지워졌거나 숨겨진 글이다.
     `"missing"`을 남긴다.

본문은 크롤러와 같은 함수(enrich_geeknews_item)로 만든다. RSS 요약 조각 뒤에 원문을
붙이고 `content_status="partial"`로 둔다. GN 요약과 댓글은 토픽 백필이 채운다.
원문을 못 받은 행은 본문을 그대로 두고 `extra.original_failures`를 세어 2번째에 뺀다.

news.hada.io 요청은 geeknews.ListingScan만 보낸다. 창 안에 차단 기록이 있으면
시작하지 않고, 막히면 바로 멈추고 차단을 기록한다. 몇 장까지 거슬러 갈 수 있는지와
목록에도 한도가 있는지는 아직 모른다. 막혀도 다음 데일리 전에 풀릴 시간이 있도록
데일리가 끝난 직후에 돌린다.

사용:
    uv run python scripts/recover_geeknews_originals.py --dry-run
    uv run python scripts/recover_geeknews_originals.py
    uv run python scripts/recover_geeknews_originals.py --start-page 40
"""

import argparse
import json
import sqlite3
import sys
import time
from typing import Dict, List, Optional, Tuple

from skim_core.crawlers.feed.geeknews import (
    ListingScan,
    enrich_geeknews_item,
    topic_id_from_url,
)
from skim_core.db import get_connection

# 원문을 못 받은 행은 이만큼 실패하면 대상에서 뺀다. 401, 403으로 막는 사이트는 다시
# 받아도 같고, 빼지 않으면 실행마다 맨 앞에서 시간을 쓴다.
MAX_ROW_FAILURES = 2

# 2026-10-01 기준 대상은 토픽 id 31320~34521이다. 최신 글과 id가 3,200개쯤 떨어져
# 목록 160장 안팎이다. 상한일 뿐, 가장 오래된 대상보다 아래로 내려가면 멈춘다.
DEFAULT_MAX_PAGES = 200

SAVE_RETRIES = 5
SAVE_RETRY_WAIT = 30

# extra가 NULL이나 빈 문자열이면 json_extract가 깨진다.
_EXTRA = "COALESCE(NULLIF(TRIM(extra), ''), '{}')"
_FAILURES = f"COALESCE(json_extract({_EXTRA}, '$.original_failures'), 0)"


def fetch_targets(conn) -> List[Dict]:
    """본문이 RSS 요약 조각뿐이거나 빈 GeekNews 행. 최신순.

    본문이 요약과 같은 행만 고른다. GN 요약을 받은 행을 이 경로로 다시 만들면 GN
    요약이 RSS 요약 조각으로 바뀐다.
    """
    rows = conn.execute(
        f"""
        SELECT id, url, title, summary, content_markdown, likes, comments, extra
        FROM posts
        WHERE platform = 'geeknews'
          AND url LIKE '%news.hada.io/topic?id=%'
          AND json_extract({_EXTRA}, '$.enrichment_method') = 'failed'
          AND (TRIM(COALESCE(content_markdown, '')) = ''
               OR TRIM(content_markdown) = TRIM(COALESCE(summary, '')))
          AND json_extract({_EXTRA}, '$.listing_checked') IS NULL
          AND {_FAILURES} < ?
        ORDER BY timestamp DESC
        """,
        (MAX_ROW_FAILURES,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_extra(raw: Optional[str]) -> dict:
    try:
        value = json.loads(raw) if (raw or "").strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _update(row: Dict, body: str, extra: dict, listing: Optional[Dict]) -> Dict:
    """행 하나의 새 값. 지표는 비어 있을 때만 목록 값으로 채운다."""
    listing = listing or {}
    return {
        "id": row["id"],
        "content_markdown": body,
        "word_count": len(body.split()),
        "extra": json.dumps(extra, ensure_ascii=False),
        "likes": row.get("likes") or listing.get("likes"),
        "comments": row.get("comments") or listing.get("comments"),
    }


def build_update(row: Dict, original_url: str, listing: Optional[Dict] = None) -> Dict:
    """원문을 받아 붙인 새 값. 원문을 못 받으면 본문은 그대로 두고 실패를 센다."""
    extra = _load_extra(row.get("extra"))
    item = {
        "title": row.get("title") or "",
        "url": row["url"],
        "summary": row.get("summary") or "",
        "original_url": original_url,
    }
    enrich_geeknews_item(item, None)
    extra["original_url"] = original_url
    extra["enrichment_method"] = item.get("enrichment_method")
    if item.get("enrichment_error"):
        extra["enrichment_error"] = item["enrichment_error"]
    else:
        extra.pop("enrichment_error", None)
    body = row.get("content_markdown") or ""
    if item.get("enrichment_method") == "failed":
        extra["original_failures"] = int(extra.get("original_failures") or 0) + 1
    else:
        body = item["content_markdown"]
        extra["content_status"] = "partial"
        extra.pop("original_failures", None)
        for key in ("description", "image"):
            if item.get(key):
                extra[key] = item[key]
    return _update(row, body, extra, listing)


def mark_checked(row: Dict, reason: str, listing: Optional[Dict] = None) -> Dict:
    """원문 링크를 목록에서 더 찾지 않을 행. reason은 "self" 또는 "missing"."""
    extra = _load_extra(row.get("extra"))
    extra["listing_checked"] = reason
    return _update(row, row.get("content_markdown") or "", extra, listing)


def save(conn, updates: List[Dict]) -> int:
    """락에 걸리면 기다렸다 다시 쓴다. 데일리 크롤이나 다른 백필과 겹칠 수 있다."""
    if not updates:
        return 0
    params = [
        (
            u["content_markdown"],
            u["word_count"],
            u["extra"],
            u["likes"],
            u["comments"],
            u["id"],
        )
        for u in updates
    ]
    for attempt in range(SAVE_RETRIES):
        try:
            conn.executemany(
                "UPDATE posts SET content_markdown = ?, word_count = ?, extra = ?, "
                "likes = ?, comments = ? WHERE id = ?",
                params,
            )
            conn.commit()
            return len(updates)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == SAVE_RETRIES - 1:
                raise
            conn.rollback()
            print(f"[geeknews] DB 락, {SAVE_RETRY_WAIT}초 후 재시도", file=sys.stderr)
            time.sleep(SAVE_RETRY_WAIT)
    return 0


def _tally(stats: Dict[str, int], update: Dict) -> None:
    extra = json.loads(update["extra"])
    if extra.get("listing_checked"):
        stats[extra["listing_checked"]] += 1
    elif extra.get("enrichment_method") == "failed":
        stats["failed"] += 1
    else:
        stats["filled"] += 1


def recover(conn, targets: List[Dict], scan: ListingScan) -> Dict[str, int]:
    """원문 링크를 아는 행부터 채우고, 나머지는 목록을 거슬러 읽으며 채운다."""
    stats = {"filled": 0, "failed": 0, "self": 0, "missing": 0, "left": 0}
    remaining: Dict[str, Dict] = {}
    for row in targets:
        original_url = _load_extra(row.get("extra")).get("original_url")
        if original_url:
            update = build_update(row, original_url)
            save(conn, [update])
            _tally(stats, update)
        elif topic_id_from_url(row["url"]):
            remaining[topic_id_from_url(row["url"])] = row

    if remaining:
        floor = min(int(topic_id) for topic_id in remaining)
        spans: List[Tuple[int, int]] = []
        for page, rows in scan.pages():
            updates = []
            for topic_id, entry in rows.items():
                row = remaining.pop(topic_id, None)
                if row is None:
                    continue
                if entry.get("original_url"):
                    updates.append(build_update(row, entry["original_url"], entry))
                else:
                    updates.append(mark_checked(row, "self", entry))
            save(conn, updates)
            for update in updates:
                _tally(stats, update)
            ids = [int(topic_id) for topic_id in rows]
            spans.append((min(ids), max(ids)))
            print(
                f"[geeknews] 목록 {page}쪽: 대상 {len(updates)}건, 누적 채움 "
                f"{stats['filled']} / 원문 실패 {stats['failed']} / 자체 글 "
                f"{stats['self']}, 남은 대상 {len(remaining)}"
            )
            if not remaining or min(ids) < floor:
                scan.stop = "done"
                break
        # 쪽 경계에 걸친 글은 새 글이 밀어 넣어 두 쪽 사이로 빠질 수 있다. 한 쪽의 id
        # 범위 안에 있는데 없을 때만 지워진 글로 본다.
        gone = [
            row
            for topic_id, row in remaining.items()
            if any(low < int(topic_id) < high for low, high in spans)
        ]
        save(conn, [mark_checked(row, "missing") for row in gone])
        stats["missing"] += len(gone)
        stats["left"] = len(remaining) - len(gone)
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="대상만 세고 끝낸다")
    parser.add_argument("--start-page", type=int, default=1, help="목록 시작 쪽")
    parser.add_argument(
        "--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="읽을 목록 장 수 상한"
    )
    args = parser.parse_args(argv)

    try:
        conn = get_connection()
    except sqlite3.Error as exc:
        print(f"[geeknews] DB를 열 수 없습니다: {exc}", file=sys.stderr)
        return 1

    try:
        targets = fetch_targets(conn)
        known = sum(
            1 for t in targets if _load_extra(t.get("extra")).get("original_url")
        )
        print(
            f"[geeknews] 대상 {len(targets)}건 "
            f"(원문 링크를 아는 행 {known}, 목록에서 찾을 행 {len(targets) - known})"
        )
        if args.dry_run or not targets:
            return 0
        scan = ListingScan(start_page=args.start_page, max_pages=args.max_pages)
        stats = recover(conn, targets, scan)
    finally:
        conn.close()
    print(
        f"[geeknews] 완료: 채움 {stats['filled']} / 원문 실패 {stats['failed']} / "
        f"자체 글 {stats['self']} / 목록에 없음 {stats['missing']} / "
        f"남은 대상 {stats['left']}"
    )
    if scan.last_page:
        print(
            f"[geeknews] 목록 {scan.start_page}~{scan.last_page}쪽을 읽었습니다 "
            f"(멈춘 이유: {scan.stop or 'done'})."
        )
        if stats["left"]:
            print(f"이어서 돌리려면 --start-page {scan.last_page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
