"""skim research JSON 응답 직렬화.

권위 스키마 (7 필드, Phase 1/2 공통):
  topic, tokens, date_range, sources_requested, posts, stats, warnings
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from skim_core.research.types import SearchStats

ALLOWED_POST_FIELDS = (
    "platform",
    "source",
    "external_id",
    "author",
    "title",
    "url",
    "timestamp",
    "content",
    "content_markdown",
    "summary",
    "word_count",
    "likes",
    "comments",
    "reposts",
    "views",
    "extra",
    "matched_fields",
    "fetched_this_run",
)


def _normalize_extra(value: Any) -> tuple[Any, bool]:
    """`extra` 컬럼 (JSON TEXT) 을 object 로 복원.

    Returns:
        (parsed, ok) — ok 가 False 이면 원문 그대로 반환되고 경고 대상.
    """
    if value is None or value == "":
        return None, True
    if isinstance(value, (dict, list)):
        return value, True
    if isinstance(value, str):
        try:
            return json.loads(value), True
        except json.JSONDecodeError:
            return value, False
    return value, True


def _serialize_post(row: dict, warnings: list[str]) -> dict:
    """sqlite3.Row 기반 dict 를 권위 스키마 dict 로 정규화."""
    extra_value, extra_ok = _normalize_extra(row.get("extra"))
    if not extra_ok:
        warnings.append(
            f"extra json parse failed for {row.get('platform')}/{row.get('external_id')!s}"
        )

    out: dict[str, Any] = {}
    for field_name in ALLOWED_POST_FIELDS:
        if field_name == "extra":
            out["extra"] = extra_value
        elif field_name == "matched_fields":
            out["matched_fields"] = row.get("matched_fields", [])
        elif field_name == "fetched_this_run":
            out["fetched_this_run"] = bool(row.get("fetched_this_run", False))
        else:
            out[field_name] = row.get(field_name)
    return out


BODY_FIELDS = ("content", "content_markdown", "summary")


def shape_posts(
    response: dict,
    *,
    fields: Any = None,
    max_chars: Any = None,
) -> dict:
    """응답의 posts를 필요한 필드와 길이로 줄인다.

    `--emit json`이 항상 content_markdown 전문을 실어서 실측 16.6MB가 나왔다
    (`--limit 5`로도 1.4MB). 이 응답을 그대로 읽는 AI는 컨텍스트가 터진다.
    중간 단계가 없어서 대안은 3줄짜리 summary뿐이었다.

    자른 사실은 `truncated`와 `content_markdown_chars`로 남긴다. 조용히 자르면
    받는 쪽이 "본문이 원래 이만큼"이라고 믿는다.
    """
    posts = response.get("posts") or []
    if max_chars is not None and max_chars >= 0:
        for post in posts:
            original = len(post.get("content_markdown") or "")
            truncated = False
            for name in BODY_FIELDS:
                value = post.get(name)
                if isinstance(value, str) and len(value) > max_chars:
                    post[name] = value[:max_chars]
                    truncated = True
            if truncated:
                post["truncated"] = True
                post["content_markdown_chars"] = original

    if fields:
        keep = list(fields)
        response["posts"] = [
            {name: post[name] for name in keep if name in post} for post in posts
        ]
    return response


def build_response(
    *,
    topic: str,
    tokens: list[str],
    date_range: dict[str, str],
    sources_requested: list[str],
    posts: list[dict],
    search_stats: SearchStats,
    days_requested: int,
    window_expanded: int = 0,
    days_per_platform: dict[str, int] | None = None,
    newly_fetched: int = 0,
    warnings: list[str] | None = None,
) -> dict:
    """7 필드 권위 스키마 응답 빌드."""
    warning_list = list(warnings or [])
    serialized_posts = [_serialize_post(p, warning_list) for p in posts]
    by_platform: dict[str, int] = {}
    for p in serialized_posts:
        plat = p.get("platform") or "unknown"
        by_platform[plat] = by_platform.get(plat, 0) + 1

    return {
        "topic": topic,
        "tokens": tokens,
        "date_range": date_range,
        "sources_requested": sources_requested,
        "posts": serialized_posts,
        "stats": {
            "total": len(serialized_posts),
            "by_platform": by_platform,
            "rows_scanned": search_stats.rows_scanned,
            "rows_returned": search_stats.rows_returned,
            "latency_ms": search_stats.latency_ms,
            "short_tokens": search_stats.short_tokens,
            "days_requested": days_requested,
            "window_expanded": window_expanded,
            "days_per_platform": days_per_platform or {},
            "newly_fetched": newly_fetched,
        },
        "warnings": warning_list,
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
