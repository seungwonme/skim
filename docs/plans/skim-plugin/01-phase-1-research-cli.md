# Phase 1 — `skim research` CLI 기본 구현

## 목표

`uv run skim research "<topic>" --days 7 --emit json` 명령으로 **이미 DB에 쌓인 posts**를 topic/날짜/플랫폼 기준 필터해 구조화 JSON으로 반환한다. (auto refresh·크롤은 Phase 2)

## 산출물

- `packages/skim-core/src/skim_core/research/__init__.py`
- `packages/skim-core/src/skim_core/research/types.py` — `SearchStats` dataclass (Phase 2 가 import, 5차 리뷰 P2-10)
- `packages/skim-core/src/skim_core/research/search.py` — topic 검색
- `packages/skim-core/src/skim_core/research/serializer.py` — JSON 포맷
- `packages/skim-cli/src/skim_cli/cli.py`에 `research` 서브커맨드 추가
- `tests/test_research_search.py`
- `tests/test_research_cli.py`

## CLI 스펙

```bash
uv run skim research "topic" [OPTIONS]

Options:
  --days INTEGER              최근 N일 (default: 7)
  --sources TEXT              쉼표로 구분된 플랫폼 (default: all)
                              예: reddit,x,hackernews
  --limit INTEGER             플랫폼별 최대 반환 수 (default: 50)
  --emit [json|jsonl|summary] 출력 포맷 (default: json)
  --refresh [auto|never|force]  Phase 2에서 구현. 현재는 never 고정
```

## 검색 로직 (단순 LIKE)

**실제 posts 스키마 기준** (db.py:23-44, models.py:53-67):
- 본문 후보: `title` (optional), `content` (NOT NULL), `content_markdown` (optional, enrichment 결과), `summary` (optional)
- `content` 가 NOT NULL 이므로 반드시 검색 대상에 포함
- `content_markdown` 만 검색하면 enrichment 안 된 row 전부 놓침 (codex review #1)

```python
from skim_core.research.types import SearchStats


def search_posts(
    topic: str,
    since_utc_iso: str,
    sources: list[str] | None,
    limit: int,
) -> tuple[list[dict], SearchStats]:
    """
    Args:
        topic:         사용자 topic (공백 구분 토큰화)
        since_utc_iso: UTC 기준 ISO 8601 문자열. Phase 0 정규화 완료 후이므로 모든 row 도 UTC.
                       naive fallback 제거 — 파싱 안 되는 row 는 Phase 0 마이그레이션 실패로 간주.
        sources:       플랫폼 화이트리스트
        limit:         플랫폼별 최대 반환 수

    Returns:
        (rows, stats) — rows 는 matched_fields 포함 dict, stats 는 rows_scanned/latency_ms.

    LIKE 이스케이프 (2차 리뷰 #2-9):
      - 토큰의 %, _, \ 은 backslash 로 escape
      - 쿼리에 `ESCAPE '\'` 명시
    """
    tokens = [_escape_like(t.lower()) for t in topic.split() if t.strip()]
    # 짧은 토큰 경고 — substring false positive 위험 (2차 리뷰 #2-10)
    short_tokens = [t for t in tokens if len(t) <= 2]

    where_clauses = []
    params: list = []
    for token in tokens:
        where_clauses.append(
            "(LOWER(COALESCE(title, '')) LIKE ? ESCAPE '\\' "
            " OR LOWER(COALESCE(content, '')) LIKE ? ESCAPE '\\' "
            " OR LOWER(COALESCE(content_markdown, '')) LIKE ? ESCAPE '\\' "
            " OR LOWER(COALESCE(summary, '')) LIKE ? ESCAPE '\\')"
        )
        like = f"%{token}%"
        params.extend([like, like, like, like])
    where_clauses.append("timestamp >= ?")
    params.append(since_utc_iso)
    if sources:
        placeholders = ",".join("?" * len(sources))
        where_clauses.append(f"platform IN ({placeholders})")
        params.extend(sources)

    t0 = time.perf_counter()
    raw_rows = conn.execute(
        f"SELECT * FROM posts WHERE {' AND '.join(where_clauses)} "
        f"ORDER BY timestamp DESC LIMIT ?",
        params + [limit * len(sources or ['*']) * 3 or 500],
    ).fetchall()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # 각 row 에 matched_fields 계산 (2차 리뷰 #2-11)
    rows = [_attach_matched_fields(r, tokens) for r in raw_rows]
    # Python 후처리 플랫폼별 cap
    rows = _apply_per_platform_limit(rows, limit)

    stats = SearchStats(
        rows_scanned=len(raw_rows),
        rows_returned=len(rows),
        latency_ms=elapsed_ms,
        short_tokens=short_tokens,
    )
    return rows, stats


def _escape_like(token: str) -> str:
    """LIKE wildcard 이스케이프. \\ 먼저 치환 (순서 중요)."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _attach_matched_fields(row: sqlite3.Row, tokens: list[str]) -> dict:
    """row 에 matched_fields 리스트 추가. 모든 토큰이 매칭된 필드만 수록."""
    data = dict(row)
    fields = ["title", "content", "content_markdown", "summary"]
    matched = []
    for field in fields:
        text = (data.get(field) or "").lower()
        if text and all(tok.replace("\\%", "%").replace("\\_", "_") in text for tok in tokens):
            matched.append(field)
    data["matched_fields"] = matched
    return data
```

### Timestamp 취급 (2차 리뷰 #2-1, #2-2 대응)

- Phase 0 에서 전 크롤러가 **UTC ISO 8601** 로 저장하도록 정규화 완료. 기존 row 는 마이그레이션 스크립트로 변환
- `since_utc_iso` 계산: `(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()`. KST 기반 계산 금지 (mixed-offset silent data loss 방지)
- SQL 비교는 UTC 내 문자열 사전순이면 안전
- 파이썬에서 datetime 필요 시 `datetime.fromisoformat(row["timestamp"])`
- 파싱 실패 row 는 warning `"unparseable_timestamp: <platform> <id>"` 를 `warnings[]` 에 추가. Phase 0 마이그레이션 실패로 간주하고 재실행 안내

### 플랫폼별 limit

- 1차: `ORDER BY timestamp DESC LIMIT <hard_cap>` (기본 `hard_cap = limit × len(sources) × 3` 또는 500)
- 2차: Python 후처리에서 플랫폼별 counter 로 cap 적용
- 광범위 topic 에서 비효율이라는 codex review #3 지적은 v1 한계로 수용. 심해지면 SQL window partition 으로 재구현

## JSON 스키마

**실제 `Post` 모델 필드와 1:1 매핑** (codex review #4 대응). 가상 필드(`engagement.score`, top-level `metadata`) 제거.

```json
{
  "topic": "nvidia earnings",
  "tokens": ["nvidia", "earnings"],
  "date_range": {
    "from": "2026-03-19T00:00:00+00:00",
    "to":   "2026-04-18T00:00:00+00:00"
  },
  "sources_requested": ["all"],
  "stats": {
    "total": 47,
    "by_platform": {"reddit": 12, "hackernews": 8, "x": 15, "youtube": 7, "arxiv": 5},
    "rows_scanned": 1247,
    "rows_returned": 47,
    "latency_ms": 38,
    "short_tokens": [],
    "window_expanded": 0,
    "days_per_platform": {"hackernews": 7, "reddit": 7, "x": 7, "youtube": 7, "arxiv": 7},
    "newly_fetched": 0
  },
  "posts": [
    {
      "platform": "reddit",
      "source": "r/investing",
      "external_id": "abc123",
      "author": "u/someone",
      "title": "NVIDIA Q4 earnings thread",
      "url": "https://reddit.com/r/investing/...",
      "timestamp": "2026-04-15T05:22:00+00:00",
      "content": "본문 원문",
      "content_markdown": null,
      "summary": null,
      "word_count": 842,
      "likes": 142,
      "comments": 37,
      "reposts": null,
      "views": null,
      "extra": {"subreddit": "investing", "flair": "Discussion"},
      "matched_fields": ["title", "content"],
      "fetched_this_run": false
    }
  ],
  "warnings": []
}
```

**단일 권위 스키마 규약 (4차 리뷰 P2-5, 7차 리뷰 P2-10)**: 최상위는 `topic`, `tokens`, `date_range`, `sources_requested`, `posts`, `stats`, `warnings` 7 필드로 고정. Phase 2 의 `newly_fetched`, `window_expanded`, `days_per_platform` 과 Phase 1 측정 지표 (`rows_scanned`, `latency_ms`, `short_tokens`) 는 전부 `stats` 아래 nested. 별도 top-level `search_stats` 금지.

**Timezone 규약 (7차 리뷰 P2-10)**: 예시의 `date_range.from/to` 와 `posts[].timestamp` 는 모두 **UTC (`+00:00`)**. Phase 0 timestamp 정규화 이후 저장/비교 모두 UTC canonical. KST 오프셋(`+09:00`) 을 예시에 넣지 말 것 — 계획 내 규약 자체가 깨진다.

### `SearchStats` 정의 (5차 리뷰 P2-10)

```python
# packages/skim-core/src/skim_core/research/types.py
from dataclasses import dataclass, field

@dataclass
class SearchStats:
    rows_scanned: int = 0
    rows_returned: int = 0
    latency_ms: int = 0
    short_tokens: list[str] = field(default_factory=list)
```

Phase 1 `search_posts` 반환 tuple 의 두 번째 요소 타입. Phase 2 `refresh.py` 가 동일 모듈에서 import.

### 필드 규칙

- `content` 는 항상 존재 (DB NOT NULL). `content_markdown`·`summary`·`title` 은 null 가능
- `likes/comments/reposts/views` 는 **flat integer** (Post 모델과 동일). `engagement` 같은 nested object 만들지 않음
- `extra` 는 DB에 JSON TEXT 로 저장됨 → CLI 출력 시 파싱해서 object 로 내보냄. 파싱 실패하면 원문 문자열 그대로
- `timestamp` 는 UTC ISO 8601 문자열 그대로 통과 (Phase 0 정규화 후)
- `matched_fields` (2차 리뷰 #2-11): `title|content|content_markdown|summary` 중 전체 토큰이 매칭된 필드 목록. Claude 가 relevance ranking 할 때 신호로 사용
- `fetched_this_run` (2차 리뷰 #2-12): Phase 2 refresh 로 방금 수집된 post 면 `true`. Phase 1 단독 실행 시 항상 `false`

## Edge Cases

- `topic` 비어있음 → exit 2 + 사용법 출력
- 토큰 0개(전부 공백/stopword) → **empty posts + warning `"no searchable tokens in topic"`** 반환, 최근 posts 덤프 금지
- 토큰 중 2글자 이하 존재 → warning `"short tokens (<=2 chars): ['ai']. substring false positives likely."` 추가 (2차 리뷰 #2-10)
- `--sources` 에 REGISTRY에 없는 플랫폼 → exit 2 + 지원 목록 출력
- DB 비어있음 → 빈 결과 + `"posts table empty"` warning
- LIKE 결과가 `--limit` 초과 → 플랫폼별로 최신순 컷
- `title == NULL` 또는 `content_markdown == NULL` row → `COALESCE(..., '')` 로 안전 검색, 다른 필드에 토큰 있으면 히트
- `extra` 가 유효하지 않은 JSON → 문자열 그대로 JSON 출력, 파싱 실패 warning 추가
- `timestamp` 가 Phase 0 정규화 후에도 ISO 8601 아닌 row → warning `"unparseable_timestamp: <platform> <external_id>"` 추가. Phase 0 재실행 안내

## 측정 인프라 (2차 리뷰 #2-15, #2-16 대응)

Phase 1 부터 성능·품질 지표를 **처음부터** 계측. 연기 금지.

### stderr 로그

매 search 호출마다 다음을 `[skim research stats]` prefix 로 stderr 출력:

```text
[skim research stats] topic="nvidia earnings" tokens=2 rows_scanned=1247 rows_returned=47 latency_ms=38
```

### JSON 응답 필드

측정 지표는 `stats.rows_scanned`, `stats.rows_returned`, `stats.latency_ms`, `stats.short_tokens` 로 통합 (4차 리뷰 P2-5). top-level `search_stats` 분리 금지.

### 골든셋 회귀

`tests/fixtures/golden_topics.json` 에 20 개 topic 고정:

```json
[
  {"topic": "nvidia earnings", "min_results": 10, "required_platforms": ["hackernews", "reddit"]},
  {"topic": "ai video", "min_results": 5},
  ...
]
```

CI `test_research_golden_topics` 가 고정 샘플 DB 에 대해 실행, 각 topic 의 `matched_fields` 분포를 리포트. Phase 1 성공 기준 (title/summary 비율 ≥ 40%) 검증.

### Latency budget

- 샘플 DB (2 만 행) 기준 p50 < 200ms, p95 < 500ms
- CI 에 `pytest tests/test_research_latency.py --benchmark-only` 추가
- 초과 시 `pytest.warns` 로 경고 (초기엔 xfail, v1.1 에서 강제 통과)

## TDD 체크리스트

- [ ] `test_search_single_token_title`
- [ ] `test_search_single_token_content` — `content` 필드 매칭 (content_markdown NULL row)
- [ ] `test_search_single_token_content_markdown` — enrichment 된 row
- [ ] `test_search_single_token_summary` — RSS summary 매칭
- [ ] `test_search_multi_token_and` — 토큰 AND 동작
- [ ] `test_search_case_insensitive` — 대소문자 무시
- [ ] `test_search_null_title_still_matches_via_content`
- [ ] `test_search_platform_filter` — `--sources reddit,x` 필터링
- [ ] `test_search_date_range_utc` — `--days 7` 경계, UTC 기준 (mixed offset 없음)
- [ ] `test_search_rejects_non_utc_since_iso` — naive/KST since 입력 방어 (2차 리뷰 #2-2)
- [ ] `test_search_timestamp_string_lexicographic_ordering` — UTC ISO 8601 DESC = 시간순
- [ ] `test_search_per_platform_limit` — `--limit 5` 플랫폼별 cap
- [ ] `test_search_like_escape_percent` — topic 에 `%` 포함 시 literal 매칭 (2차 리뷰 #2-9)
- [ ] `test_search_like_escape_underscore` — topic 에 `_` 포함 시 literal 매칭
- [ ] `test_search_attaches_matched_fields` — `matched_fields` 필드 정확도 (2차 리뷰 #2-11)
- [ ] `test_search_returns_search_stats` — `rows_scanned`, `latency_ms` 포함
- [ ] `test_search_warns_short_tokens` — 2글자 이하 토큰 경고 (2차 리뷰 #2-10)
- [ ] `test_cli_emit_json_schema_matches_post_model` — flat engagement, extra JSON 파싱, matched_fields
- [ ] `test_cli_extra_json_invalid_passthrough_with_warning`
- [ ] `test_cli_empty_topic_exits_2`
- [ ] `test_cli_empty_tokens_returns_empty_with_warning`
- [ ] `test_cli_unknown_source_exits_2`
- [ ] `test_cli_empty_db_returns_empty_list`
- [ ] `test_cli_emits_stats_to_stderr` — 측정 로그 regression
- [ ] `test_research_golden_topics` — 골든셋 20 개 회귀 (matched_fields 분포)

## 수동 검증

```bash
# 샘플 데이터 먼저 채움
uv run skim crawl hackernews --days 7

# 검색
uv run skim research "llm" --days 7 --sources hackernews --emit json | jq '.stats'
uv run skim research "nvidia" --days 7 --sources hackernews,reddit --limit 10
```

## TODO (Phase 1 완료 후)

- 댓글 본문 검색 (reddit comments, hn comments)
- 한국어 토크나이저 (현재는 공백 split만)
- FTS5 전환 검토 (posts > 50k 시점)
- stopword 처리 ("the", "a" 등)
- 크롤러별 timestamp 포맷 정규화 감사 스크립트 (`SELECT timestamp FROM posts LIMIT 100` → ISO 8601 검증)
- `ORDER BY timestamp DESC` + 플랫폼별 window partition SQL 로 전환 (광범위 topic 성능)

## 의존성

**Phase 0 완료 필수** (2차 리뷰 #2-1). timestamp 정규화 + DB API 시그니처 확정 없이는 Phase 1 의 LIKE + 날짜 필터가 silent data loss 를 만든다. Phase 0 → Phase 1 순서 엄격.

Phase 1 자체는 Phase 2 auto refresh 없이도 "이미 Phase 0 마이그레이션된 posts" 에 대해 독립 실행 가능.
