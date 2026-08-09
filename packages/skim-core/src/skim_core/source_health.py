"""
@file source_health.py
@description 소스별 본문 추출 품질 회귀 탐지.

절대 임계(예: 200단어 미만이면 경고)는 소스 성격 차이 때문에 오탐이 난다.
producthunt는 제품 태그라인이 본문의 최저선이고 팟캐스트 페이지는 원래 짧다.
그래서 **그 소스의 평소 대비 변화**로 판정한다.

실제로 producthunt는 2026-04~06 사이 본문의 70%를 빈 채로 저장했는데 3개월간
아무 신호도 없었다. 이 모듈은 그런 회귀를 하루 만에 드러내는 게 목적이다.

피드 자체가 죽은 경우(every.to/Guides의 HTTP 500)는 여기서 못 잡는다. 그건
수집 실패라 `skim source refresh`가 tier 강등으로 잡는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .db import get_connection

# 최근 창. 일일 크롤 기준으로 회귀가 며칠 안에 드러날 만큼 짧게 잡는다.
RECENT_DAYS = 14

# 기준선 창 (최근 창 이전). 소스의 "평소"를 정의한다.
BASELINE_DAYS = 120

# 통계가 의미를 가지려면 양쪽에 최소 이만큼은 있어야 한다.
MIN_RECENT_POSTS = 5
MIN_BASELINE_POSTS = 10

# 본문 보유율이 기준선 대비 이만큼(절대 백분율) 떨어지면 회귀로 본다.
BODY_RATE_DROP = 0.30

# 기준선 대비 방식은 **처음부터 깨진 소스**를 못 잡는다. 실제로 producthunt는
# DB 시작 시점부터 본문의 70%가 비어 있어서, 되감기 검증에서 탐지 0건이 나왔다.
# 비교 대상이 없을 때를 위한 하한선. 분량이 아니라 '본문이 있냐 없냐'라서
# 소스 성격에 따른 오탐이 없다 (짧은 태그라인도 본문은 있는 것으로 센다).
LOW_BODY_RATE = 0.50

# 평균 단어 수가 기준선의 이 비율 아래로 내려가면 회귀로 본다.
WORD_COUNT_RATIO = 0.40

# 분량 검사는 **추출기를 거치는 플랫폼에만** 적용한다.
# 소셜(reddit/threads/x/linkedin)의 본문 길이는 사용자가 쓰는 대로라 표본이
# 수십 건 있어도 평균이 요동친다. 운영 DB에 걸어보니 r/nextjs, r/vibecoding 등이
# 전부 오탐으로 잡혔다. 본문 보유율(body_rate_drop)은 소셜에도 그대로 적용한다.
SOCIAL_PLATFORMS = frozenset({"threads", "linkedin", "x", "reddit"})

# 분량은 보유율보다 흔들리기 쉬워 표본을 더 요구한다.
MIN_RECENT_POSTS_FOR_WORDS = 15

# 정본은 content_markdown 하나다 (AGENTS.md 데이터 계약). doctor가 쓰는
# content/summary 폴백 COALESCE를 여기 쓰면 안 된다 - producthunt 309건이
# content_markdown은 비고 summary만 채워진 상태였는데, COALESCE가 그 실패를
# 통째로 가려서 되감기 검증에서 탐지 0건이 나왔다.
_BODY_SQL = "COALESCE(content_markdown, '')"

# youtube-history 백필 행은 임베드용 목록이라 본문이 비는 게 정상이다
# (AGENTS.md가 명시한 예외). 백필이 몰리면 보유율이 출렁여 오탐이 된다.
BODY_RATE_EXEMPT_PLATFORMS = frozenset({"youtube"})


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def scan_source_health(
    db_path: Optional[Path] = None,
    *,
    recent_days: int = RECENT_DAYS,
    baseline_days: int = BASELINE_DAYS,
) -> List[dict]:
    """소스별로 최근 창을 자기 기준선과 비교해 회귀 목록을 돌려준다."""
    recent_cut = _iso_days_ago(recent_days)
    base_cut = _iso_days_ago(recent_days + baseline_days)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT platform,
                   COALESCE(NULLIF(source, ''), platform) AS src,
                   SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) AS recent_n,
                   SUM(CASE WHEN timestamp >= ? AND {_BODY_SQL} != '' THEN 1 ELSE 0 END)
                       AS recent_ok,
                   AVG(CASE WHEN timestamp >= ? THEN word_count END) AS recent_words,
                   SUM(CASE WHEN timestamp < ? AND timestamp >= ? THEN 1 ELSE 0 END) AS base_n,
                   SUM(CASE WHEN timestamp < ? AND timestamp >= ? AND {_BODY_SQL} != ''
                            THEN 1 ELSE 0 END) AS base_ok,
                   AVG(CASE WHEN timestamp < ? AND timestamp >= ? THEN word_count END)
                       AS base_words
            FROM posts
            WHERE timestamp IS NOT NULL AND timestamp != ''
            GROUP BY platform, src
            """,
            (
                recent_cut,
                recent_cut,
                recent_cut,
                recent_cut,
                base_cut,
                recent_cut,
                base_cut,
                recent_cut,
                base_cut,
            ),
        ).fetchall()
    finally:
        conn.close()

    issues: List[dict] = []
    for row in rows:
        recent_n = row["recent_n"] or 0
        if recent_n < MIN_RECENT_POSTS:
            continue

        base_n = row["base_n"] or 0
        has_baseline = base_n >= MIN_BASELINE_POSTS
        recent_rate = (row["recent_ok"] or 0) / recent_n
        base_rate = (row["base_ok"] or 0) / base_n if base_n else 0.0
        exempt = row["platform"] in BODY_RATE_EXEMPT_PLATFORMS

        if not exempt and has_baseline and base_rate - recent_rate >= BODY_RATE_DROP:
            issues.append(
                {
                    "platform": row["platform"],
                    "source": row["src"],
                    "kind": "body_rate_drop",
                    "recent": round(recent_rate, 3),
                    "baseline": round(base_rate, 3),
                    "recent_posts": recent_n,
                    "detail": (
                        f"본문 보유율 {base_rate:.0%} -> {recent_rate:.0%} "
                        f"(최근 {recent_n}건)"
                    ),
                }
            )
            continue

        # 기준선이 없거나 처음부터 낮았던 소스를 위한 하한선.
        if not exempt and recent_rate < LOW_BODY_RATE:
            issues.append(
                {
                    "platform": row["platform"],
                    "source": row["src"],
                    "kind": "low_body_rate",
                    "recent": round(recent_rate, 3),
                    "baseline": None,
                    "recent_posts": recent_n,
                    "detail": (
                        f"본문 보유율 {recent_rate:.0%} (최근 {recent_n}건). "
                        "추출이 이 소스에서 동작하지 않는다"
                    ),
                }
            )
            continue

        if (
            not has_baseline
            or row["platform"] in SOCIAL_PLATFORMS
            or recent_n < MIN_RECENT_POSTS_FOR_WORDS
        ):
            continue

        recent_words = row["recent_words"]
        base_words = row["base_words"]
        if recent_words and base_words and recent_words < base_words * WORD_COUNT_RATIO:
            issues.append(
                {
                    "platform": row["platform"],
                    "source": row["src"],
                    "kind": "word_count_collapse",
                    "recent": round(recent_words, 1),
                    "baseline": round(base_words, 1),
                    "recent_posts": recent_n,
                    "detail": (
                        f"평균 본문 {base_words:.0f}단어 -> {recent_words:.0f}단어 "
                        f"(최근 {recent_n}건)"
                    ),
                }
            )

    issues.sort(key=lambda i: (i["platform"], i["source"]))
    return issues
