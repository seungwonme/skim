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
import re
import sqlite3
import sys
import time
from typing import Dict, List, Optional

import requests

from skim_core.crawlers.feed.hackernews import compose_hn_body, fetch_hn_discussion
from skim_core.db import get_connection
from skim_core.enrichment import enrich_papers_with_content, enrich_with_content

# 논문은 HTML/PDF 전문 경로가 따로 있다.
PAPER_PLATFORMS = {"arxiv", "huggingface"}

# hackernews 옛 행은 external_id가 해시라 story id를 모른다. 원문이 페이월이어도
# HN 토론은 남아 있고, 데이터 계약도 상위 댓글을 본문에 포함하라고 한다.
HN_PLATFORM = "hackernews"
ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"

# youtube 목록 행은 본문 없이 저장되는 것이 데이터 계약이다. 자막은 사용자가
# youtube-transcribe로 요청할 때 채운다. 여기서 일괄 전사하지 않는다.
#
# producthunt는 봇 트래픽을 429로 막는다. 지연을 줘도 누적 요청이 쌓이면 단건도
# 막히고, playwright 폴백은 54단어짜리 차단 화면만 받는다. 원래 본문이 짧은
# 태그라인이라 제목/요약으로 충분하다고 보고 대상에서 뺀다(2026-08-08 결정).
EXCLUDED_PLATFORMS = {"youtube", "producthunt"}

BATCH_SIZE = 25

# 락 재시도. 데일리 크롤이나 다른 백필과 겹치면 배치 하나가 통째로 버려진다.
SAVE_RETRIES = 5
SAVE_RETRY_WAIT = 30


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


_TITLE_SUFFIX = re.compile(r"\s*[\[(][^\])]*[\])]\s*$")


def _core_title(title: str) -> str:
    """제목 끝의 괄호 꼬리표를 떼고 비교용으로 정규화한다.

    같은 글이라도 피드와 HN의 꼬리표가 다르다
    ("... consent order (2 July 2026)" vs "... consent order [pdf]").
    """
    text = (title or "").strip()
    while True:
        stripped = _TITLE_SUFFIX.sub("", text).strip()
        if stripped == text:
            return text.lower()
        text = stripped


def _algolia_hits(query: str) -> List[Dict]:
    # urllib 기본 User-Agent로는 Algolia가 빈손을 준다. requests는 통과한다.
    try:
        resp = requests.get(
            ALGOLIA_SEARCH,
            params={"query": query[:120], "tags": "story", "hitsPerPage": 5},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except Exception:  # pylint: disable=broad-except
        return []


def hn_story_id(title: str, url: str) -> Optional[str]:
    """제목이나 URL로 HN story를 찾아 id를 돌려준다.

    옛 hackernews 행의 external_id는 hnrss 해시라 story id가 들어 있지 않다.
    엉뚱한 스토리의 댓글을 붙이면 본문이 오염되므로, 원문 URL이 같거나 꼬리표를
    뗀 제목이 정확히 맞을 때만 채택한다.
    """
    normalized_url = (url or "").rstrip("/")
    core = _core_title(title)

    # Algolia는 모든 토큰이 맞아야 준다. 원제목을 그대로 넣으면 피드가 붙인
    # 꼬리표("(2 July 2026)") 때문에 0건이 나오므로 꼬리표를 뗀 쪽을 먼저 던진다.
    for query in (core, title):
        for hit in _algolia_hits(query):
            if normalized_url and (hit.get("url") or "").rstrip("/") == normalized_url:
                return hit.get("objectID")
            if core and _core_title(hit.get("title") or "") == core:
                return hit.get("objectID")
    return None


def enrich_hn_rows(rows: List[Dict], delay: float) -> List[Dict]:
    """hackernews 행은 원문 추출과 별개로 HN 토론을 본문에 합친다.

    원문이 페이월이라 추출이 안 되어도 토론은 남는다. 크롤 경로가 만드는 본문과
    같은 모양(compose_hn_body)으로 맞춘다.
    """
    enriched: List[Dict] = []
    for row in rows:
        item = to_item(row)
        enrich_with_content([item])
        article = (item.get("content_markdown") or "").strip()

        story_id = hn_story_id(row.get("title") or "", row.get("url") or "")
        discussion = fetch_hn_discussion(story_id) if story_id else None

        body = compose_hn_body(article, discussion).strip()
        if body:
            enriched.append(
                {
                    "id": row["id"],
                    "content_markdown": body,
                    "word_count": len(body.split()),
                }
            )
        if delay:
            time.sleep(delay)
    return enriched


def run_batch(rows: List[Dict], delay: float = 0.0) -> List[Dict]:
    papers = [r for r in rows if r["platform"] in PAPER_PLATFORMS]
    hn_rows = [r for r in rows if r["platform"] == HN_PLATFORM]
    others = [
        r for r in rows if r["platform"] not in PAPER_PLATFORMS and r["platform"] != HN_PLATFORM
    ]

    enriched: List[Dict] = enrich_hn_rows(hn_rows, delay) if hn_rows else []
    for group, enrich in (
        (papers, enrich_papers_with_content),
        (others, enrich_with_content),
    ):
        if not group:
            continue
        items = [to_item(r) for r in group]
        if delay:
            # 한 호스트에 몰아치면 차단당한다. producthunt 323건이 첫 실행에서
            # 통째로 빈손이었던 이유가 이것이었다(같은 URL을 한 건씩 부르면 추출된다).
            for item in items:
                enrich([item])
                time.sleep(delay)
        else:
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
    """락에 걸리면 기다렸다 다시 쓴다.

    추출은 배치당 수 분이 든다. 저장 한 번 실패로 그걸 통째로 버리면 손해가 크다
    (실제로 병행 스크립트가 쥔 쓰기 락 때문에 두 번 날아갔다).
    """
    if not enriched:
        return 0

    params = [(e["content_markdown"], e["word_count"], e["id"]) for e in enriched]
    for attempt in range(SAVE_RETRIES):
        try:
            conn.executemany(
                "UPDATE posts SET content_markdown = ?, word_count = ? WHERE id = ?",
                params,
            )
            conn.commit()
            return len(enriched)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", help="특정 플랫폼만")
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 건수")
    parser.add_argument("--dry-run", action="store_true", help="대상만 세고 끝낸다")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="항목 사이 대기 초. 한 호스트에 몰린 대상(producthunt 등)은 1.5 권장",
    )
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
        filled += save(conn, run_batch(batch, args.delay))
        print(
            f"[backfill] {min(start + BATCH_SIZE, len(rows))}/{len(rows)} 처리, 누적 {filled}건 채움"
        )

    conn.close()
    print(f"[backfill] 완료: {filled}/{len(rows)}건 채움 (나머지는 원문 소실/추출 불가)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
