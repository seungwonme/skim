#!/usr/bin/env python
"""GitHub 릴리스 노트와 GitHub PDF 링크의 본문을 다시 받는다 (#46).

릴리스 페이지를 페이지째 추출하던 동안 저장된 행은 화면 문구(로그인 안내, 저장소
머리말, 커밋 서명 안내)가 노트를 감싸고 있거나, 짧은 노트가 60단어 게이트에 걸려
빈 본문이다. GitHub PDF 링크(blob 보기 페이지)는 화면 문구만 저장됐다.

크롤러와 같은 enrichment 경로(enrich_with_content)를 그대로 태운다.
  - blogs의 릴리스 행 -> 페이지의 노트 영역. 피드에는 최근 10건만 남아 있다.
  - 애그리게이터 행 -> 댓글 섹션이 없는 행만 다룬다. 본문을 통째로 바꾸므로
    댓글이 붙은 행을 건드리면 댓글이 사라진다.
      * 릴리스 링크 -> 노트 영역
      * PDF 링크이고 본문이 보기 페이지를 추출한 것(화면 문구가 남는다)이거나
        쓸 수 없음 -> raw 주소의 PDF
geeknews는 본문 형식(GN 요약, 원문, 댓글)이 달라 대상에서 뺀다.

다시 받지 못한 행은 그대로 둔다. 단 PDF가 저장소에서 지워진 행은 본문이 보기
페이지의 화면 문구뿐이라 비운다. 재실행해도 안전하다.
바꾼 행은 enrichment_method가 github-release, feed-content, pdf가 되어 대상에서 빠진다.

사용:
    uv run python scripts/backfill_github_releases.py --dry-run
    uv run python scripts/backfill_github_releases.py
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from typing import Dict, List, Optional

from skim_core.db import get_connection
from skim_core.enrichment import (
    _AGGREGATOR_PLATFORMS,
    _is_content_usable,
    enrich_with_content,
    has_github_chrome,
    is_github_pdf_url,
    is_github_release_url,
)

# 노트 영역이나 PDF로 이미 바꾼 행. 다시 받지 않는다.
DONE_METHODS = frozenset({"github-release", "feed-content", "pdf"})

# 애그리게이터 본문 뒤에 붙는 댓글 섹션 (`## Hacker News Comments` 등).
COMMENT_SECTION = re.compile(r"^## .*(?:Comments|Replies)\s*$", re.MULTILINE)

# github.com에 연달아 요청한다. 한 번에 몰리지 않게 사이를 둔다.
DEFAULT_DELAY = 1.0

# 배치마다 커밋한다. 중간에 끊겨도 여기까지는 남고, 재실행하면 이어서 간다.
BATCH_SIZE = 25

# 락 재시도. 데일리 크롤이나 다른 백필과 겹치면 배치 하나가 통째로 버려진다.
SAVE_RETRIES = 5
SAVE_RETRY_WAIT = 30


def _extra(row) -> dict:
    try:
        value = json.loads(row["extra"] or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def link_of(row) -> str:
    """원문 링크. hackernews 행은 url이 토론 주소이고 원문이 extra.original_url에 있다."""
    return _extra(row).get("original_url") or row["url"] or ""


def is_target(row) -> bool:
    """다시 받을 행인지. 근거는 모듈 docstring."""
    if _extra(row).get("enrichment_method") in DONE_METHODS:
        return False
    link = link_of(row)
    if row["platform"] == "blogs":
        return is_github_release_url(link)
    body = row["content_markdown"] or ""
    if row["platform"] not in _AGGREGATOR_PLATFORMS or COMMENT_SECTION.search(body):
        return False
    if is_github_release_url(link):
        return True
    if not is_github_pdf_url(link):
        return False
    # 보기 페이지에는 PDF 본문이 없다. 화면 문구는 형태가 여럿이라(마크다운 링크,
    # 메뉴 줄 나열) 단어 수 게이트만으로는 못 거른다. 흔적이 있으면 다시 받는다.
    return has_github_chrome(body) or not _is_content_usable(
        {"content_markdown": body}, row["title"] or "", min_words=3
    )


def fetch_targets(conn, limit: int = 0) -> List[Dict]:
    """GitHub 링크가 있는 행 중 다시 받을 행. 최신순."""
    rows = conn.execute(
        "SELECT id, platform, source, url, title, content_markdown, extra FROM posts "
        "WHERE url LIKE 'https://github.com/%' OR extra LIKE '%https://github.com/%' "
        "ORDER BY timestamp DESC"
    ).fetchall()
    targets = [dict(row) for row in rows if is_target(row)]
    return targets[:limit] if limit else targets


def to_item(row: Dict) -> Dict:
    """enrichment가 기대하는 모양으로 옮긴다.

    enrichment는 item["platform"]으로 경로를 고른다. blogs는 그 값이 source
    ("blogs/LangChain Releases")이고, 애그리게이터는 DB의 platform이다.
    hackernews 행의 source("hackernews/show")를 넘기면 애그리게이터 경로를 못 탄다.
    """
    platform = row["platform"]
    return {
        "platform": (row.get("source") or platform)
        if platform == "blogs"
        else platform,
        "url": link_of(row),
        "title": row.get("title") or "",
    }


def updated_row(row: Dict, item: Dict) -> Optional[Dict]:
    """다시 받은 본문으로 바꿀 값. 바꿀 게 없으면 None (행은 그대로 둔다)."""
    body = (item.get("content_markdown") or "").strip()
    extra = _extra(row)
    if body:
        extra["enrichment_method"] = item.get("enrichment_method")
        extra.pop("enrichment_error", None)
    elif is_github_pdf_url(link_of(row)) and has_github_chrome(
        row["content_markdown"] or ""
    ):
        # PDF가 저장소에서 지워졌다. 남은 본문은 보기 페이지의 화면 문구뿐이라,
        # 두면 소비자가 GitHub 메뉴를 논문 본문으로 읽는다.
        extra["enrichment_method"] = "failed"
        extra["enrichment_error"] = (
            item.get("enrichment_error") or "github pdf not found"
        )
    else:
        return None
    return {
        "id": row["id"],
        "content_markdown": body,
        "word_count": item.get("word_count") or len(body.split()),
        "extra": json.dumps(extra, ensure_ascii=False, default=str),
    }


def save(conn, updates: List[Dict]) -> int:
    """락에 걸리면 기다렸다 다시 쓴다."""
    if not updates:
        return 0
    params = [
        (u["content_markdown"], u["word_count"], u["extra"], u["id"]) for u in updates
    ]
    for attempt in range(SAVE_RETRIES):
        try:
            conn.executemany(
                "UPDATE posts SET content_markdown = ?, word_count = ?, extra = ? "
                "WHERE id = ?",
                params,
            )
            conn.commit()
            return len(updates)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == SAVE_RETRIES - 1:
                raise
            conn.rollback()
            print(
                f"[backfill] DB 락, {SAVE_RETRY_WAIT}초 후 재시도 "
                f"({attempt + 1}/{SAVE_RETRIES - 1})",
                file=sys.stderr,
            )
            time.sleep(SAVE_RETRY_WAIT)
    return 0


def backfill(conn, rows: List[Dict], delay: float = DEFAULT_DELAY) -> int:
    """행마다 본문을 다시 받아 배치 단위로 저장한다. 바꾼 행 수를 돌려준다."""
    filled = 0
    for start in range(0, len(rows), BATCH_SIZE):
        updates: List[Dict] = []
        for row in rows[start : start + BATCH_SIZE]:
            item = to_item(row)
            enrich_with_content([item])
            update = updated_row(row, item)
            if update:
                updates.append(update)
            if delay:
                time.sleep(delay)
        filled += save(conn, updates)
        print(
            f"[backfill] {min(start + BATCH_SIZE, len(rows))}/{len(rows)} 처리, "
            f"누적 {filled}건 바꿈"
        )
    return filled


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 건수")
    parser.add_argument("--dry-run", action="store_true", help="대상만 세고 끝낸다")
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY, help="행 사이 대기 초"
    )
    args = parser.parse_args(argv)

    try:
        conn = get_connection()
    except sqlite3.Error as exc:
        print(f"[backfill] DB를 열 수 없습니다: {exc}", file=sys.stderr)
        return 1

    try:
        rows = fetch_targets(conn, args.limit)
        if not rows:
            print("[backfill] 대상 없음")
            return 0
        counts: Dict[str, int] = {}
        for row in rows:
            key = row.get("source") or row["platform"]
            counts[key] = counts.get(key, 0) + 1
        print(
            f"[backfill] 대상 {len(rows)}건: "
            + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        )
        if args.dry_run:
            return 0
        filled = backfill(conn, rows, args.delay)
        print(
            f"[backfill] 완료: {filled}/{len(rows)}건 바꿈 "
            "(나머지는 노트 없음, 지워진 파일, 추출 불가)"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
