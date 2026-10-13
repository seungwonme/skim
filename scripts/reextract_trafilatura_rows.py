#!/usr/bin/env python
"""#85 전에 trafilatura로 추출한 행의 목록과 굵은 글씨를 다시 추출한다 (#86).

trafilatura 2.0.0의 마크다운 출력은 목록 기호를 빼 항목을 한 문단으로 붙이고, 굵은
글씨 뒤 띄어쓰기를 지웠다(`**A.**B`, #84). 원문 페이지를 다시 받아 옛 방식과 새
방식으로 각각 추출하고, 옛 방식 결과가 저장된 본문에 글자 그대로 한 번 들어 있을 때만
그 부분을 새 결과로 바꾼다.

  - 본문 앞뒤의 스토리 본문, GN 요약, 댓글 섹션은 건드리지 않는다. 바꾸는 범위는 옛
    결과와 똑같은 부분뿐이다.
  - 옛 결과가 본문과 다르면(원문이 바뀌었거나, 추출 설정이 달랐던 시기의 행) 그대로 둔다.
  - 새 결과가 옛 결과의 글자를 하나라도 잃으면 그대로 둔다. 새 방식은 띄어쓰기와 목록
    기호만 더해야 한다.
  - producthunt는 뺀다. 추출한 주소(제품 사이트로 가는 리다이렉트)를 저장하지 않았다.
  - geeknews는 원문 주소만 받는다. news.hada.io는 부르지 않는다.
  - 피드 본문으로 추출한 행(feed-content)은 피드에 더는 없어 다시 받을 수 없다.

다시 돌려도 안전하다. 바꾼 행은 옛 결과가 본문에 없어 건너뛴다.

사용:
    uv run python scripts/reextract_trafilatura_rows.py --days 30 --dry-run --limit 20
    uv run python scripts/reextract_trafilatura_rows.py --days 30
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import trafilatura

from skim_core.db import get_connection
from skim_core.enrichment import _fetch_rendered_html, _http_fetch_html
from skim_core.trafilatura_markdown import extract_markdown

# 저장된 enrichment_method -> 그때 추출한 HTML을 받은 방법.
FETCH_BY_METHOD = {"trafilatura": "http", "playwright+trafilatura": "rendered"}

PLATFORMS = ("hackernews", "lobsters", "blogs", "ailabs", "geeknews")

# 요청량으로 막히는 호스트. geeknews 모듈만 부른다.
FORBIDDEN_HOSTS = frozenset({"news.hada.io"})

# 굵은 글씨 바로 뒤에 영문, 숫자가 붙은 자리. 한국어 조사(`**스킴**은`)는 정상이라 뺀다.
BROKEN_BOLD = re.compile(r"\*\*[^*\n]+\*\*[A-Za-z0-9]")

DEFAULT_DELAY = 0.5
BATCH_SIZE = 25
SAVE_RETRIES = 5
SAVE_RETRY_WAIT = 30

CHANGED = "바꿈"


def _extra(row) -> dict:
    try:
        value = json.loads(row["extra"] or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def link_of(row) -> str:
    """추출한 원문 주소. hackernews, geeknews 행은 url이 토론 주소다."""
    return _extra(row).get("original_url") or row["url"] or ""


def is_target(row, flagged_only: bool = False) -> bool:
    if row["platform"] not in PLATFORMS:
        return False
    if _extra(row).get("enrichment_method") not in FETCH_BY_METHOD:
        return False
    link = link_of(row)
    host = urlparse(link).hostname or ""
    if not link.startswith("http") or host in FORBIDDEN_HOSTS:
        return False
    if flagged_only:
        return bool(BROKEN_BOLD.search(row["content_markdown"] or ""))
    return True


def fetch_targets(
    conn,
    days: int,
    platforms: Optional[List[str]] = None,
    flagged_only: bool = False,
    limit: int = 0,
) -> List[Dict]:
    """최근 days일 안에 처음 저장된 대상 행. 최신순."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    wanted = [p for p in (platforms or PLATFORMS) if p in PLATFORMS]
    if not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    rows = conn.execute(
        "SELECT id, platform, url, title, content_markdown, word_count, extra "
        f"FROM posts WHERE platform IN ({marks}) AND crawled_at >= ? "
        "ORDER BY crawled_at DESC",
        (*wanted, cutoff.strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchall()
    targets = [dict(row) for row in rows if is_target(row, flagged_only)]
    return targets[:limit] if limit else targets


def old_markdown(html: str, url: str) -> Optional[str]:
    """#85 전의 추출. `_trafilatura_extract`가 부르던 인자 그대로다."""
    md = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )
    return md.strip() if md else None


def new_markdown(html: str, url: str) -> Optional[str]:
    md = extract_markdown(html, url)
    return md.strip() if md else None


def _letters(text: str) -> str:
    return re.sub(r"[\W_]+", "", text)


def keeps_all_letters(old: str, new: str) -> bool:
    """old의 글자(공백, 기호 제외)가 순서대로 new에 다 있는지."""
    remaining = iter(_letters(new))
    return all(char in remaining for char in _letters(old))


def plan_update(
    row: Dict, old: Optional[str], new: Optional[str]
) -> Tuple[str, Optional[Dict]]:
    """(상태, 바꿀 값). 바꾸지 않으면 바꿀 값은 None이다."""
    if not old or not new:
        return "추출 실패", None
    if old == new:
        return "변화 없음", None
    body = row["content_markdown"] or ""
    found = body.count(old)
    if found == 0:
        return ("이미 고침" if new in body else "옛 결과 불일치"), None
    if found > 1:
        return "위치 모호", None
    if not keeps_all_letters(old, new):
        return "글자 손실", None
    word_count = row.get("word_count")
    if word_count:
        word_count += len(new.split()) - len(old.split())
    return CHANGED, {
        "id": row["id"],
        "content_markdown": body.replace(old, new, 1),
        "word_count": word_count,
        "old": old,
        "new": new,
    }


def fetch_html(row: Dict) -> Optional[str]:
    link = link_of(row)
    if FETCH_BY_METHOD[_extra(row)["enrichment_method"]] == "rendered":
        return _fetch_rendered_html(link)
    return _http_fetch_html(link)


def reextract(
    row: Dict, fetch: Callable[[Dict], Optional[str]] = fetch_html
) -> Tuple[str, Optional[Dict]]:
    try:
        html = fetch(row)
    except Exception as exc:  # noqa: BLE001 - 한 행의 실패가 나머지를 막지 않는다
        print(f"    [!] 받기 실패 (#{row['id']}): {exc}")
        html = None
    if not html:
        return "받기 실패", None
    link = link_of(row)
    try:
        old = old_markdown(html, link)
        new = new_markdown(html, link)
    except Exception as exc:  # noqa: BLE001
        print(f"    [!] 추출 예외 (#{row['id']}): {exc}")
        return "추출 실패", None
    return plan_update(row, old, new)


def save(conn, updates: List[Dict]) -> int:
    """락에 걸리면 기다렸다 다시 쓴다. 데일리 크롤과 겹칠 수 있다."""
    if not updates:
        return 0
    params = [(u["content_markdown"], u["word_count"], u["id"]) for u in updates]
    for attempt in range(SAVE_RETRIES):
        try:
            conn.executemany(
                "UPDATE posts SET content_markdown = ?, word_count = ? WHERE id = ?",
                params,
            )
            conn.commit()
            return len(updates)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == SAVE_RETRIES - 1:
                raise
            conn.rollback()
            print(
                f"[reextract] DB 락, {SAVE_RETRY_WAIT}초 후 재시도 "
                f"({attempt + 1}/{SAVE_RETRIES - 1})",
                file=sys.stderr,
            )
            time.sleep(SAVE_RETRY_WAIT)
    return 0


def write_diff(diff_dir: Path, row: Dict, update: Dict) -> None:
    """바꿀 부분의 전후를 파일로 남긴다. 사람이 표본을 열어 보는 용도다."""
    diff_dir.mkdir(parents=True, exist_ok=True)
    (diff_dir / f"{row['id']}.md").write_text(
        f"# #{row['id']} {row['platform']} {row.get('title') or ''}\n"
        f"{link_of(row)}\n\n## before\n\n{update['old']}\n\n## after\n\n{update['new']}\n",
        encoding="utf-8",
    )


def run(
    conn,
    rows: List[Dict],
    dry_run: bool,
    delay: float = DEFAULT_DELAY,
    diff_dir: Optional[Path] = None,
    fetch: Callable[[Dict], Optional[str]] = fetch_html,
) -> Counter:
    """행마다 다시 추출해 배치 단위로 저장한다. 상태별 건수를 돌려준다."""
    statuses: Counter = Counter()
    for start in range(0, len(rows), BATCH_SIZE):
        updates: List[Dict] = []
        for row in rows[start : start + BATCH_SIZE]:
            status, update = reextract(row, fetch)
            statuses[status] += 1
            statuses[f"{row['platform']}:{status}"] += 1
            if update:
                updates.append(update)
                if diff_dir:
                    write_diff(diff_dir, row, update)
            if delay:
                time.sleep(delay)
        if not dry_run:
            save(conn, updates)
        done = min(start + BATCH_SIZE, len(rows))
        print(
            f"[reextract] {done}/{len(rows)} 처리, {CHANGED} {statuses[CHANGED]}건"
            + (" (dry-run, 저장 안 함)" if dry_run else ""),
            flush=True,
        )
    return statuses


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--days", type=int, default=30, help="처음 저장된 지 며칠 안의 행"
    )
    parser.add_argument(
        "--platform", action="append", help="플랫폼 (여러 번 지정 가능)"
    )
    parser.add_argument(
        "--flagged-only",
        action="store_true",
        help="굵은 글씨 뒤 띄어쓰기가 빠진 행만 (목록만 깨진 행은 못 고른다)",
    )
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 건수")
    parser.add_argument(
        "--dry-run", action="store_true", help="다시 추출만 하고 저장하지 않는다"
    )
    parser.add_argument("--diff-dir", type=Path, help="바꿀 부분의 전후를 쓸 폴더")
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY, help="행 사이 대기 초"
    )
    args = parser.parse_args(argv)

    try:
        conn = get_connection()
    except sqlite3.Error as exc:
        print(f"[reextract] DB를 열 수 없습니다: {exc}", file=sys.stderr)
        return 1

    try:
        rows = fetch_targets(
            conn, args.days, args.platform, args.flagged_only, args.limit
        )
        if not rows:
            print("[reextract] 대상 없음")
            return 0
        counts = Counter(row["platform"] for row in rows)
        print(
            f"[reextract] 대상 {len(rows)}건: "
            + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        )
        statuses = run(conn, rows, args.dry_run, args.delay, args.diff_dir)
        print("[reextract] 결과:")
        for key, value in sorted(statuses.items()):
            if ":" not in key:
                print(f"  {key}: {value}")
        for key, value in sorted(statuses.items()):
            if ":" in key:
                print(f"    {key}: {value}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
