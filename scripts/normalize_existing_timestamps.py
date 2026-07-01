"""기존 posts.timestamp 를 ISO 8601 UTC 로 정규화.

전략:
  1. 각 row 의 raw timestamp 를 분류 (epoch / 한국어 상대시간 / ISO)
  2. 파싱 가능하면 UPDATE 후보로 모은다
  3. --commit 없이는 쓰기 금지 (dry-run 기본)
  4. 파싱 실패 row 는 stdout 으로 dump (수동 조사 대상)

사용법:
    uv run python scripts/normalize_existing_timestamps.py            # dry-run
    uv run python scripts/normalize_existing_timestamps.py --commit   # 실제 반영
    uv run python scripts/normalize_existing_timestamps.py --db path  # 임의 DB
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Optional

from skim_core.paths import workspace_root
from skim_core.timestamp import epoch_to_iso, to_utc_iso


def detect_and_normalize(raw: Optional[str], platform: str) -> tuple[Optional[str], str]:
    """raw timestamp 를 분류해 (정규화 결과, 사유) 반환.

    relative_ko 는 의도적으로 자동 변환하지 않는다 (data-loss 방지):
    원본 wall-clock 정보가 사라진 상태에서 migration 시점 NOW 기준으로 재anchor
    하면 발행 시각이 마이그레이션 실행 시각으로 망가진다. 사유 'relative_ko_skipped'
    로 분류하고 원본을 보존. 후속 크롤로 수집되는 신규 row 는 새 크롤러가 직접
    UTC ISO 로 저장하므로 자연 치유된다.
    """
    del platform  # 감사 로그용 인자 유지 (미사용)
    if raw is None or raw == "":
        return None, "empty"
    candidate = raw.lstrip("-")
    if candidate.isdigit() and 9 <= len(candidate) <= 16:
        try:
            return epoch_to_iso(raw), "epoch"
        except (ValueError, OSError):
            return None, "invalid_epoch"
    if "전" in raw:
        # 보존 — 원본 wall-clock 시점 손실 우려 (codex review HIGH)
        return None, "relative_ko_skipped"
    normalized = to_utc_iso(raw)
    if normalized:
        return normalized, "iso"
    return None, "unknown"


def normalize_db(db_path: Path, *, commit: bool) -> dict:
    """DB 의 모든 posts row 를 분류하고 정규화. 통계 반환."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, platform, timestamp FROM posts").fetchall()
    stats = {"total": len(rows), "updated": 0, "skipped": 0, "failed": 0}

    for row in rows:
        raw = row["timestamp"]
        new_value, reason = detect_and_normalize(raw, row["platform"])
        if new_value is None:
            stats["failed"] += 1
            print(
                f"FAIL id={row['id']} platform={row['platform']!s} " f"raw={raw!r} reason={reason}"
            )
            continue
        if new_value == raw:
            stats["skipped"] += 1
            continue
        stats["updated"] += 1
        if commit:
            conn.execute(
                "UPDATE posts SET timestamp=? WHERE id=?",
                (new_value, row["id"]),
            )

    if commit:
        conn.commit()
    conn.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="실제 UPDATE 실행 (기본은 dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="명시적 no-op (기본 동작과 동일)")
    parser.add_argument("--db", type=Path, help="대상 DB 경로 (기본: workspace data/skim.db)")
    args = parser.parse_args()

    db_path = args.db or (workspace_root() / "data" / "skim.db")
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    stats = normalize_db(db_path, commit=args.commit)
    print(stats)


if __name__ == "__main__":
    main()
