# Phase 0 — 전제 조건 (Timestamp 정규화 + DB API 확정)

## 목표

Phase 1/2 가 동작하기 위해 반드시 선행되어야 하는 두 가지:

1. **Timestamp 정규화**: 모든 posts 가 ISO 8601 문자열(UTC 권장)로 저장되도록 크롤러·DB 를 수정하고, 기존 row 를 마이그레이션
2. **DB API 시그니처 확정**: Phase 2 refresh 로직이 기댈 `save_posts`, `save_run`, `update_run_progress`, `finish_run` 의 실제 호출 규약을 문서화

## 배경 (2차 Codex 리뷰 대응)

1차 리뷰 반영 후에도 Phase 1 은 "ISO 8601 사전순 = 시간순" 을 가정했으나, 실제 코드는:

- **HN Firebase 모드** (`hackernews.py:60`): `timestamp=str(item.get("time", ""))` → `"1700000000"` 같은 **epoch 문자열**
- **GeekNews 홈페이지 모드** (`geeknews.py:92-94`): `"3시간전"` 같은 **한국어 상대시간**
- **RSS 모드**: ISO 8601 문자열 (정상)

즉 `timestamp >= '2026-04-12T...'` 비교가 플랫폼마다 다르게 작동하고, Phase 1 의 LIKE + 날짜 필터가 silent data loss 를 만든다. Phase 2 의 `_parse_iso()` fallback 으로도 해결 불가 (저장 단계에서 이미 형식 깨짐).

또한 Phase 2 계획의 `save_posts(posts)`, `finish_run(run_id, status=..., summary=...)` 호출은 실제 시그니처와 불일치:

| 함수 | 실제 시그니처 (db.py) |
|---|---|
| `save_posts` | `save_posts(posts, platform, source=None, db_path=None)` — `platform` **필수 위치 인자** |
| `save_run` | `save_run(status="running", db_path=None)` |
| `update_run_progress` | `update_run_progress(run_id, current_platform, summary=None, db_path=None)` |
| `finish_run` | `finish_run(run_id, status, posts_count, summary=None, db_path=None)` — `posts_count` **필수 위치 인자** |

Phase 2 코드를 현재 시그니처 기준으로 재작성해야 한다.

## 산출물

- `packages/skim-core/src/skim_core/timestamp.py` — 정규화 헬퍼
- 크롤러 수정: `hackernews.py`, `geeknews.py` (비-RSS 모드)
- `scripts/normalize_existing_timestamps.py` — 기존 DB row 마이그레이션
- `tests/test_timestamp_normalization.py`
- `tests/test_crawler_timestamp_iso8601.py` — 전 크롤러 회귀

## Timestamp 정규화 규약

### 원칙

1. **저장 포맷**: ISO 8601 + timezone offset. **UTC 강제** (`2026-04-19T05:00:00+00:00`). RSS 경로가 KST 로 저장 중이면 마이그레이션 대상 — Phase 1 의 사전순 비교는 모든 row 가 동일 canonical tz 여야 안전 (3차 리뷰 RISK-02 대응).
2. **비교 기준**: `search_posts` 내부에서 `since_iso` 도 UTC 로 변환 후 비교. mixed-offset silent data loss 방지.
3. **파싱 실패 row**: `timestamp=""`, epoch 잔여물, 파싱 불가 문자열은 **Phase 0 마이그레이션 시점에 감지**해 별도 로그로 남김. 감지 후 크롤러 수정으로 재발 방지.
4. **Multi-unit 파싱**: `'1시간 30분 전'` 같은 복수 단위를 각각 더해서 처리 (3차 리뷰 P1-2 대응).
5. **Ms epoch 구분**: 13-digit 은 millisecond 로 간주하고 `/1000` 변환 (3차 리뷰 P1-1 대응).

### 헬퍼

```python
# packages/skim-core/src/skim_core/timestamp.py
from datetime import datetime, timedelta, timezone
import re

KST = timezone(timedelta(hours=9))
UTC = timezone.utc

_REL_KO_UNITS = r"분|시간|일|주|달|개월|년"
_REL_KO_PAIR = re.compile(rf"(\d+)\s*({_REL_KO_UNITS})")
_REL_KO_HAS_JEON = re.compile(r"전\s*$|\s전(?:\s|$)")
# 7차 리뷰 P1-6: geeknews 가 import 하는 "한국어 상대시간 여부" 판별 통합 패턴.
# 크롤러 측에서는 `_REL_KO.search(text)` 로 감지 후 `relative_ko_to_iso(text)` 를 호출.
_REL_KO = re.compile(
    rf"(?:\d+\s*(?:{_REL_KO_UNITS})\s*)+전"
)


def epoch_to_iso(value: str | int | float) -> str:
    """정수/문자열 epoch → UTC ISO 8601. 단위는 magnitude 로 추론 (4차 리뷰 P3-9).

    판정 기준 (절댓값):
      - abs >= 1e15 → microseconds (13자리 이상 연도 2001+)
      - abs >= 1e12 → milliseconds (12-13자리)
      - 그 외 → seconds
    이렇게 하면 12-digit ms 도 올바르게 처리됨.
    """
    raw = int(float(value))
    mag = abs(raw)
    if mag >= 10**15:
        raw //= 1_000_000  # micro → seconds
    elif mag >= 10**12:
        raw //= 1000       # ms → seconds
    # negative epoch (1970 이전) 은 fromtimestamp 가 처리
    return datetime.fromtimestamp(raw, tz=UTC).isoformat()


def _unit_to_delta(n: int, unit: str) -> timedelta:
    return {
        "분": timedelta(minutes=n),
        "시간": timedelta(hours=n),
        "일": timedelta(days=n),
        "주": timedelta(weeks=n),
        "달": timedelta(days=30 * n),
        "개월": timedelta(days=30 * n),
        "년": timedelta(days=365 * n),
    }[unit]


def relative_ko_to_iso(value: str, now: datetime | None = None) -> str | None:
    """'3시간 전', '1시간 30분 전' 한국어 상대시간 → UTC ISO 8601 (P1-2 대응).

    복수 단위를 전부 합산. 마지막에 '전' 이 있어야 past 로 간주.
    """
    if not value or not _REL_KO_HAS_JEON.search(value):
        return None
    pairs = _REL_KO_PAIR.findall(value)
    if not pairs:
        return None
    now = now or datetime.now(UTC)
    total = timedelta()
    for n_str, unit in pairs:
        total += _unit_to_delta(int(n_str), unit)
    # 모든 파싱 결과를 UTC 기준으로 저장 (KST 고려 안 함 — 모든 timestamp UTC canonical)
    return (now - total).astimezone(UTC).isoformat()


def to_utc_iso(value: str) -> str | None:
    """ISO 8601 입력을 UTC 로 변환. 이미 UTC 면 그대로. naive 면 KST 로 간주."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(UTC).isoformat()
```

## 크롤러 수정

### HackerNews Firebase 모드

```python
# hackernews.py:60 변경
from ...timestamp import epoch_to_iso

timestamp=epoch_to_iso(item.get("time", 0)),
```

### GeekNews 홈페이지 모드

```python
# geeknews.py:92-94 교체 (7차 리뷰 P1-6: _REL_KO 는 timestamp.py 에 정의됨)
from ...timestamp import _REL_KO, relative_ko_to_iso

info_text = topicinfo.get_text(" ", strip=True)
rel_match = _REL_KO.search(info_text)
if rel_match:
    timestamp = relative_ko_to_iso(rel_match.group(0)) or ""
else:
    timestamp = ""
```

### 기타 크롤러 감사

Phase 0 시작 시 전 크롤러의 `timestamp` 저장 경로를 전수 점검:

```bash
grep -RnE "timestamp\s*=" packages/skim-core/src/skim_core/crawlers
```

- RSS 경로 (`feed_utils.fetch_feed`): 이미 ISO 8601 생성 확인됨 → 변경 불필요
- API 크롤러 (threads/x/linkedin/reddit): ISO 8601 기록 확인됨 (AGENTS.md 2026-04-08 노트)
- YouTube/arxiv/producthunt/huggingface/everyto: feed_utils 경유 → OK

발견되는 모든 비-ISO 저장 경로는 Phase 0 안에서 수정한다.

## 마이그레이션 스크립트

```python
# scripts/normalize_existing_timestamps.py
"""기존 posts.timestamp 를 ISO 8601 UTC 로 정규화.

전략:
1. platform 별로 현재 저장 포맷을 sampling 으로 추정
2. 파싱 가능한 row 는 in-place UPDATE
3. 파싱 실패 row 는 stdout 으로 dump + 원본 보존 (수동 조사 대상)
4. --dry-run 지원, --commit 없이는 쓰기 금지
"""
import argparse
import sqlite3
from pathlib import Path

from skim_core.paths import workspace_root
from skim_core.timestamp import epoch_to_iso, relative_ko_to_iso, to_utc_iso


def detect_and_normalize(raw: str, platform: str) -> tuple[str | None, str]:
    """(정규화된 값 or None, 사유) 반환."""
    if not raw:
        return None, "empty"
    # epoch 문자열: 9~16 digit 전부 허용 (magnitude 기반 단위 추론)
    if raw.lstrip("-").isdigit() and 9 <= len(raw.lstrip("-")) <= 16:
        try:
            return epoch_to_iso(raw), "epoch"
        except (ValueError, OSError):
            return None, "invalid_epoch"
    # 한국어 상대시간
    if "전" in raw:
        normalized = relative_ko_to_iso(raw)
        return (normalized, "relative_ko") if normalized else (None, "unparsed_relative")
    # ISO 8601 가정
    normalized = to_utc_iso(raw)
    return (normalized, "iso") if normalized else (None, "unknown")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    db = workspace_root() / "data" / "skim.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, platform, timestamp FROM posts").fetchall()
    stats = {"updated": 0, "skipped": 0, "failed": 0}
    for row in rows:
        new_value, reason = detect_and_normalize(row["timestamp"], row["platform"])
        if new_value is None:
            stats["failed"] += 1
            print(f"FAIL id={row['id']} platform={row['platform']} raw={row['timestamp']!r} reason={reason}")
            continue
        if new_value == row["timestamp"]:
            stats["skipped"] += 1
            continue
        stats["updated"] += 1
        if args.commit:
            conn.execute("UPDATE posts SET timestamp=? WHERE id=?", (new_value, row["id"]))
    if args.commit:
        conn.commit()
    print(stats)


if __name__ == "__main__":
    main()
```

## DB API 시그니처 (실측)

Phase 2 가 호출할 함수들 — 실제 `db.py` 기준:

```python
# 저장
saved_count: int = save_posts(posts, platform, source=None)

# 실행 기록
run_id: int = save_run(status="running")
update_run_progress(run_id, current_platform, summary=None)
finish_run(run_id, status, posts_count, summary=None)
```

**Phase 2 `refresh_platforms` 는 플랫폼별로 `save_posts(posts, platform)` 을 호출해야 함**. 단일 `save_posts(posts)` 호출 불가 (platform 이 위치 필수).

## TDD 체크리스트

- [ ] `test_epoch_to_iso_10_digit_seconds`
- [ ] `test_epoch_to_iso_13_digit_ms_divides_1000` — 1712345678901 → 2024-04-06T... (P1-1)
- [ ] `test_epoch_to_iso_16_digit_micro_divides_1M`
- [ ] `test_rel_ko_detects_3시간전` — `_REL_KO` 패턴이 geeknews 본문에서 상대시간 토큰을 찾음 (P1-6)
- [ ] `test_rel_ko_multiunit_detected` — `'1시간 30분 전'` 같은 복수 단위도 `_REL_KO.search()` 로 감지
- [ ] `test_relative_ko_matches_prefix_before_suffix_text` — `'3시간 전 작성'` 같이 뒤에 텍스트가 붙어도 prefix 만 매칭 (9차 리뷰 P3-7)
- [ ] `test_relative_ko_single_unit_hours_days_weeks_months_years`
- [ ] `test_relative_ko_multi_unit_sums` — `'1시간 30분 전'` → now - 1h30m (P1-2)
- [ ] `test_relative_ko_without_jeon_returns_none` — `'3시간'` (전 없음) → None
- [ ] `test_relative_ko_unparseable_returns_none`
- [ ] `test_relative_ko_output_is_utc` — `tzinfo` 가 `UTC` 임을 검증 (RISK-02)
- [ ] `test_to_utc_iso_already_utc`
- [ ] `test_to_utc_iso_kst_offset_converted`
- [ ] `test_to_utc_iso_naive_treated_as_kst`
- [ ] `test_hackernews_firebase_mode_stores_iso8601`
- [ ] `test_geeknews_homepage_mode_stores_iso8601`
- [ ] `test_migration_script_dry_run_no_writes`
- [ ] `test_migration_script_commit_updates_epoch_rows`
- [ ] `test_migration_script_logs_unparseable_rows`

## 수동 검증

```bash
# 1. 기존 DB 샘플링
sqlite3 data/skim.db 'SELECT platform, timestamp FROM posts ORDER BY RANDOM() LIMIT 20;'

# 2. 크롤러별 재크롤 후 포맷 확인
uv run skim crawl hackernews --count 3
uv run skim crawl geeknews --count 3
sqlite3 data/skim.db "SELECT platform, timestamp FROM posts WHERE platform IN ('hackernews','geeknews') ORDER BY id DESC LIMIT 10;"

# 3. 마이그레이션 dry-run → commit
uv run python scripts/normalize_existing_timestamps.py
uv run python scripts/normalize_existing_timestamps.py --commit
```

## 성공 기준

- `SELECT COUNT(*) FROM posts WHERE timestamp NOT LIKE '____-__-__T%'` 결과 0 (또는 의도된 empty 만 남음)
- `tests/test_crawler_timestamp_iso8601.py` 전 플랫폼 회귀 그린
- Phase 2 가 호출할 DB 함수 시그니처가 이 문서에 확정됨

## 의존성

- 없음. Phase 0 은 가장 먼저 완료되어야 한다.

## TODO (Phase 0 완료 후)

- `Post` 모델에 `timestamp_utc` computed property 추가 (편의용)
- 크롤러 저장 시점에 ISO 8601 validator 적용 (pydantic `@field_validator`)
- CI 에서 샘플 DB 로 회귀
