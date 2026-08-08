#!/usr/bin/env python
"""content_markdown이 빈 기존 행을 원문 URL로 재추출해 채운다.

2026-07 이전 수집분에는 이후 고쳐진 enrichment 버그(피드 본문 폴백 부재,
producthunt 태그라인 누락, playwright 미설치 등)의 잔재가 남아 있다. 이 행들은
content도 비어 있어 `skim migrate`(content -> content_markdown 승격)로는 못 채운다.

크롤러와 같은 enrichment 경로를 그대로 태운다:
  - arxiv, huggingface -> enrich_papers_with_content (HTML -> PDF -> abstract)
  - 그 외              -> enrich_with_content

재실행해도 안전하다. 이미 채워진 행은 대상에서 빠진다.

사용:
    uv run python scripts/backfill_missing_content.py --dry-run
    uv run python scripts/backfill_missing_content.py --platform ailabs
    uv run python scripts/backfill_missing_content.py --limit 100
    uv run python scripts/backfill_missing_content.py            # 전체
"""

import argparse
import sqlite3
import sys
from typing import Dict, List

from skim_core.db import get_connection
from skim_core.enrichment import enrich_papers_with_content, enrich_with_content

# 논문은 HTML/PDF 전문 경로가 따로 있다.
PAPER_PLATFORMS = {"arxiv", "huggingface"}

# youtube 목록 행은 본문 없이 저장되는 것이 데이터 계약이다. 자막은 사용자가
# youtube-transcribe로 요청할 때 채운다. 여기서 일괄 전사하지 않는다.
EXCLUDED_PLATFORMS = {"youtube"}

BATCH_SIZE = 25


def fetch_targets(conn, platform: str = None, limit: int = 0) -> List[Dict]:
    """본문 정본이 빈 행. url이 없으면 재추출할 방법이 없어 제외한다."""
    sql = (
        "SELECT id, platform, source, url, title, summary FROM posts "
        "WHERE COALESCE(content_markdown, '') = '' "
        "AND COALESCE(url, '') <> '' "
        f"AND platform NOT IN ({','.join('?' for _ in EXCLUDED_PLATFORMS)})"
    )
    params: list = list(EXCLUDED_PLATFORMS)
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    sql += " ORDER BY crawled_at DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def to_item(row: Dict) -> Dict:
    """enrichment가 기대하는 dict 모양으로 옮긴다.

    enrichment는 item["platform"]으로 소스별 분기를 하는데 그 값은 DB의
    platform이 아니라 source다("ailabs/OpenAI News"). 구버전 행은 source가
    NULL이라 platform으로 폴백한다.
    """
    return {
        "platform": row.get("source") or row["platform"],
        "url": row["url"],
        "title": row.get("title") or "",
        "summary": row.get("summary") or "",
    }


def run_batch(rows: List[Dict]) -> List[Dict]:
    papers = [r for r in rows if r["platform"] in PAPER_PLATFORMS]
    others = [r for r in rows if r["platform"] not in PAPER_PLATFORMS]

    enriched: List[Dict] = []
    for group, enrich in (
        (papers, enrich_papers_with_content),
        (others, enrich_with_content),
    ):
        if not group:
            continue
        items = [to_item(r) for r in group]
        enrich(items)
        for row, item in zip(group, items):
            body = (item.get("content_markdown") or "").strip()
            if body:
                enriched.append(
                    {
                        "id": row["id"],
                        "content_markdown": body,
                        "word_count": item.get("word_count") or len(body.split()),
                    }
                )
    return enriched


def save(conn, enriched: List[Dict]) -> int:
    if not enriched:
        return 0
    conn.executemany(
        "UPDATE posts SET content_markdown = ?, word_count = ? WHERE id = ?",
        [(e["content_markdown"], e["word_count"], e["id"]) for e in enriched],
    )
    conn.commit()
    return len(enriched)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", help="특정 플랫폼만")
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 건수")
    parser.add_argument("--dry-run", action="store_true", help="대상만 세고 끝낸다")
    args = parser.parse_args()

    try:
        conn = get_connection()
    except sqlite3.Error as exc:
        print(f"[backfill] DB를 열 수 없습니다: {exc}", file=sys.stderr)
        return 1

    rows = fetch_targets(conn, args.platform, args.limit)
    if not rows:
        print("[backfill] 대상 없음")
        conn.close()
        return 0

    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["platform"]] = counts.get(row["platform"], 0) + 1
    print(
        f"[backfill] 대상 {len(rows)}건: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )

    if args.dry_run:
        conn.close()
        return 0

    filled = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        # 배치마다 커밋한다. 중간에 끊겨도 여기까지는 남고, 재실행하면 이어서 간다.
        filled += save(conn, run_batch(batch))
        print(
            f"[backfill] {min(start + BATCH_SIZE, len(rows))}/{len(rows)} 처리, 누적 {filled}건 채움"
        )

    conn.close()
    print(
        f"[backfill] 완료: {filled}/{len(rows)}건 채움 (나머지는 원문 소실/추출 불가)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
