#!/usr/bin/env python
"""GeekNews 행을 토픽 페이지로 다시 채운다: GN 요약, 댓글, 지표.

토픽 페이지는 news.hada.io가 요청량으로 막는 자리라 크롤 회차에서 못 받은 글이
생긴다. 그런 글은 `content_status="partial"`(요약 조각 + 원문)이나
`enrichment_method="failed"`(요약 조각만)로 저장된다. 2026-08-29 ~ 09-28 차단
기간에 쌓인 행도 같은 모양이다 (9월 저장분 1,082건 중 959건, #29).

대상과 우선순위. 토픽 페이지 한 번으로 셋을 다 채우므로 행당 요청은 1건이다.
  1. 본문이 partial, failed, 빈 행 -> 본문을 다시 만든다
  2. 댓글이 있는데 댓글 섹션이 없는 행 -> 댓글을 잇는다
  3. 지표가 빈 행 -> 지표만 채운다
토픽 페이지를 한 번 받은 행은 `extra.geeknews_topic = "ok"`가 붙어 대상에서 빠진다.
지워진 글(404)은 `"gone"`이 붙어 빠진다. 200인데 아무것도 못 읽은 글은
`extra.geeknews_topic_failures`를 세어 3번째에 빠진다. 이런 글을 빼지 않으면 최신순
맨 앞에 남아 매일 밤 연속 실패로 멈추고, 뒤의 행이 영영 채워지지 않는다.

크롤러와 같은 함수(fetch_topic, enrich_geeknews_item)를 부르고, 토픽 요청 한도
(한 시간 25건)도 크롤과 파일로 나눠 쓴다. news.hada.io는 30건 안팎에서 막고, 한 번
막히면 25분이 지나도 풀리지 않았다. 그래서 한도가 없으면 요청하지 않고, 한 번이라도
막히면 바로 멈춘다. 다음 실행이 남은 행을 이어서 채운다.

데일리는 크롤 뒤에 돌아 크롤이 쓰고 남은 한도만 쓴다. 쌓인 결손을 줄이려면
--wait-minutes로 한도가 빌 때를 기다리며 오래 돌린다 (한 시간에 25건).

사용:
    uv run python scripts/backfill_geeknews_topics.py --dry-run
    uv run python scripts/backfill_geeknews_topics.py --limit 100
    uv run python scripts/backfill_geeknews_topics.py --limit 200 --wait-minutes 480
"""

import argparse
import json
import sqlite3
import sys
import time
from typing import Dict, List, Optional

from skim_core.comments import append_comment_section
from skim_core.crawlers.feed.geeknews import (
    COMMENT_HEADER,
    TopicPage,
    enrich_geeknews_item,
    fetch_topic,
    saved_original_from,
    topic_budget_left,
    topic_id_from_url,
)
from skim_core.db import get_connection

# 행 사이 대기. 크롤러의 토픽 간격(3초) 위에 얹는다. 차단은 요청 수로 걸리므로
# 이건 한 번에 몰리지 않게 하는 정도다. 요청 수는 한도가 막는다.
DEFAULT_DELAY = 6.0

# 한도가 빌 때를 확인하는 주기.
BUDGET_POLL_SECONDS = 60

# 차단이 아닌 실패(네트워크 단절, 마크업 변경)가 이만큼 이어지면 멈춘다.
MAX_CONSECUTIVE_FAILURES = 5

# 200인데 아무것도 못 읽은 글은 이만큼 실패하면 대상에서 뺀다.
MAX_ROW_FAILURES = 3

COMMIT_EVERY = 25
SAVE_RETRIES = 5
SAVE_RETRY_WAIT = 30

# extra가 NULL이나 빈 문자열이면 json_extract가 깨진다.
_EXTRA = "COALESCE(NULLIF(TRIM(extra), ''), '{}')"
_NEEDS_BODY = (
    f"json_extract({_EXTRA}, '$.content_status') = 'partial' "
    f"OR json_extract({_EXTRA}, '$.enrichment_method') = 'failed' "
    "OR TRIM(COALESCE(content_markdown, '')) = ''"
)
_NEEDS_COMMENTS = (
    "COALESCE(comments, 0) > 0 "
    f"AND COALESCE(content_markdown, '') NOT LIKE '%{COMMENT_HEADER}%'"
)
_NEEDS_METRICS = "COALESCE(likes, 0) = 0"
_FAILURES = f"COALESCE(json_extract({_EXTRA}, '$.geeknews_topic_failures'), 0)"


def fetch_targets(conn, limit: int = 0) -> List[Dict]:
    """토픽 페이지를 받아 본 적 없고 채울 것이 남은 행. 우선순위, 최신순."""
    sql = f"""
        SELECT id, url, title, summary, content_markdown, likes, comments, extra,
               CASE WHEN {_NEEDS_BODY} THEN 1
                    WHEN {_NEEDS_COMMENTS} THEN 2
                    ELSE 3 END AS priority
        FROM posts
        WHERE platform = 'geeknews'
          AND url LIKE '%news.hada.io/topic?id=%'
          AND json_extract({_EXTRA}, '$.geeknews_topic') IS NULL
          AND {_FAILURES} < ?
          AND (({_NEEDS_BODY}) OR ({_NEEDS_COMMENTS}) OR ({_NEEDS_METRICS}))
        ORDER BY priority, {_FAILURES}, timestamp DESC
    """
    params: list = [MAX_ROW_FAILURES]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _load_extra(raw: Optional[str]) -> dict:
    try:
        value = json.loads(raw) if (raw or "").strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def build_update(row: Dict, topic: TopicPage) -> Dict:
    """토픽 페이지로 행 하나의 새 값을 만든다. DB에는 쓰지 않는다."""
    extra = _load_extra(row.get("extra"))
    body = row.get("content_markdown") or ""

    if row.get("priority") == 1:
        item = {
            "title": row.get("title") or "",
            "url": row["url"],
            "summary": row.get("summary") or "",
            "original_url": extra.get("original_url"),
            # 0은 미수집이라 토픽 페이지 값으로 채우게 비워 둔다.
            "likes": row.get("likes") or None,
            "comments": row.get("comments") or None,
        }
        # partial 행에는 원문이 이미 있다. 다시 추출하다 실패하면 그 원문을 잃는다.
        enrich_geeknews_item(item, topic, saved_original=saved_original_from(body))
        # 새로 만든 본문이 비면(제목만 반복하는 요약 등) 있던 본문을 지우지 않는다.
        if item["content_markdown"]:
            body = item["content_markdown"]
        extra.pop("content_status", None)
        for key in ("original_url", "enrichment_method", "description", "image"):
            if item.get(key):
                extra[key] = item[key]
        if item.get("enrichment_error"):
            extra["enrichment_error"] = item["enrichment_error"]
        else:
            extra.pop("enrichment_error", None)
    elif topic.comment_section and COMMENT_HEADER not in body:
        body = append_comment_section(body, topic.comment_section)

    extra["geeknews_topic"] = "ok"
    likes = row.get("likes") or topic.likes
    comments = row.get("comments") or topic.comments
    return {
        "id": row["id"],
        "content_markdown": body,
        "word_count": len(body.split()),
        "extra": json.dumps(extra, ensure_ascii=False),
        "likes": likes,
        "comments": comments,
    }


def mark_failure(row: Dict, outcome: str) -> Dict:
    """글 탓인 실패를 extra에 남긴다. 지워진 글은 바로, 못 읽은 글은 세어서 뺀다."""
    extra = _load_extra(row.get("extra"))
    if outcome == "gone":
        extra["geeknews_topic"] = "gone"
    else:
        extra["geeknews_topic_failures"] = int(extra.get("geeknews_topic_failures") or 0) + 1
    return {"id": row["id"], "extra": json.dumps(extra, ensure_ascii=False)}


def save(conn, updates: List[Dict], marks: Optional[List[Dict]] = None) -> int:
    """락에 걸리면 기다렸다 다시 쓴다. 데일리 크롤이나 다른 백필과 겹칠 수 있다."""
    marks = marks or []
    if not updates and not marks:
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
            conn.executemany(
                "UPDATE posts SET extra = ? WHERE id = ?",
                [(m["extra"], m["id"]) for m in marks],
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


def wait_for_budget(deadline: float) -> bool:
    """토픽 요청 한도가 빌 때까지 기다린다. deadline을 넘기면 False."""
    while topic_budget_left() <= 0:
        remaining = deadline - time.time()
        if remaining <= 0:
            return False
        time.sleep(min(BUDGET_POLL_SECONDS, remaining))
    return True


def run(conn, targets: List[Dict], delay: float, wait_minutes: float = 0) -> Dict[str, int]:
    """대상을 차례로 채운다. 한도가 없거나 막히거나 실패가 이어지면 멈춘다."""
    stats = {"filled": 0, "failed": 0, "gone": 0}
    pending: List[Dict] = []
    marks: List[Dict] = []
    streak = 0
    deadline = time.time() + wait_minutes * 60
    for i, row in enumerate(targets, 1):
        if not wait_for_budget(deadline):
            print("토픽 요청 한도를 다 썼습니다. 남은 행은 다음 실행이 채웁니다.")
            break
        topic, outcome = fetch_topic(topic_id_from_url(row["url"]))
        if outcome in ("blocked", "skipped", "budget"):
            print("GeekNews가 막았습니다. 남은 행은 다음 실행이 채웁니다.")
            break
        if topic is None:
            if outcome in ("gone", "empty"):
                marks.append(mark_failure(row, outcome))
            if outcome == "gone":
                # 지워진 글은 사이트가 정상 응답한 것이라 연속 실패로 세지 않는다.
                stats["gone"] += 1
                streak = 0
            else:
                stats["failed"] += 1
                streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                print(f"연속 {streak}건 실패로 중단합니다.")
                break
        else:
            streak = 0
            pending.append(build_update(row, topic))
            stats["filled"] += 1
        if len(pending) + len(marks) >= COMMIT_EVERY:
            save(conn, pending, marks)
            pending, marks = [], []
            print(
                f"   [{i}/{len(targets)}] 채움 {stats['filled']} / 실패 {stats['failed']}"
                f" / 삭제된 글 {stats['gone']}"
            )
        if delay:
            time.sleep(delay)
    save(conn, pending, marks)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 건수")
    parser.add_argument("--dry-run", action="store_true", help="대상만 세고 끝낸다")
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY, help="행 사이 대기 초"
    )
    parser.add_argument(
        "--wait-minutes",
        type=float,
        default=0,
        help="토픽 요청 한도가 빌 때를 기다리는 최대 분. 0이면 남은 한도만 쓴다",
    )
    args = parser.parse_args()

    try:
        conn = get_connection()
    except sqlite3.Error as exc:
        print(f"[geeknews] DB를 열 수 없습니다: {exc}", file=sys.stderr)
        return 1

    try:
        targets = fetch_targets(conn, args.limit)
        by_priority = {
            p: sum(1 for t in targets if t["priority"] == p) for p in (1, 2, 3)
        }
        print(
            f"대상 {len(targets)}건 (본문 {by_priority[1]}, 댓글 {by_priority[2]}, "
            f"지표 {by_priority[3]}), 남은 토픽 요청 한도 {topic_budget_left()}건"
        )
        if args.dry_run or not targets:
            return 0
        stats = run(conn, targets, args.delay, args.wait_minutes)
    finally:
        conn.close()
    print(
        f"완료: 채움 {stats['filled']} / 실패 {stats['failed']} / 삭제된 글 {stats['gone']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
