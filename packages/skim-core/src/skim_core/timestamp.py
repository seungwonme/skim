"""Timestamp normalization helpers.

전 크롤러가 ISO 8601 UTC 문자열로 저장하도록 강제하는 유틸. Phase 0 의 핵심.

규약:
  - 저장 포맷: `2026-04-19T05:00:00+00:00` (UTC, offset 명시)
  - 비교 기준: 사전순. 모든 timestamp 가 UTC canonical 이어야 안전
  - 파싱 실패: None 반환 (호출자가 warning/skip 결정)
"""

import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UTC = timezone.utc

_REL_KO_UNITS = r"분|시간|일|주|달|개월|년"
_REL_KO_PAIR = re.compile(rf"(\d+)\s*({_REL_KO_UNITS})")
_REL_KO_HAS_JEON = re.compile(r"전\s*$|\s전(?:\s|$)")
_REL_KO = re.compile(rf"(?:\d+\s*(?:{_REL_KO_UNITS})\s*)+전")


def epoch_to_iso(value: str | int | float) -> str:
    """정수/문자열 epoch → UTC ISO 8601. 단위는 magnitude 로 추론.

    판정 (절댓값):
      - abs >= 1e14 → microseconds  (14+ 자리 — 2001+ 마이크로 epoch)
      - abs >= 1e11 → milliseconds  (11-13 자리 — 12-digit ms 도 보호)
      - 그 외       → seconds       (10 자리 1e9~1e10 — 2001+ 초 epoch)
    """
    raw = int(float(value))
    mag = abs(raw)
    if mag >= 10**14:
        raw //= 1_000_000
    elif mag >= 10**11:
        raw //= 1000
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
    """'3시간 전', '1시간 30분 전' 한국어 상대시간 → UTC ISO 8601.

    복수 단위를 합산. 끝에 '전' 이 있어야 past 로 간주.
    뒤에 텍스트가 따라붙어도(`'3시간 전 작성'`) prefix span 만 사용.
    매칭된 span 밖의 pair (`'3시간 전 2분'` 의 `2분`) 는 합산되지 않음.
    """
    if not value:
        return None
    match = _REL_KO.search(value)
    if not match:
        return None
    span = match.group(0)
    pairs = _REL_KO_PAIR.findall(span)
    if not pairs:
        return None
    now = now or datetime.now(UTC)
    total = timedelta()
    for n_str, unit in pairs:
        total += _unit_to_delta(int(n_str), unit)
    return (now - total).astimezone(UTC).isoformat()


def to_utc_iso(value: str) -> str | None:
    """ISO 8601 입력을 UTC 로 변환. naive 는 KST 로 간주."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(UTC).isoformat()
