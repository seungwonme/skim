"""소스별 추출 회귀 탐지 테스트. 실제 producthunt 사고를 재현한다."""

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from skim_core.db import init_db
from skim_core.source_health import scan_source_health


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "skim.db"
    init_db(path)
    return path


_seq = itertools.count()


def _insert(path, platform, source, days_ago, body, words, n=1):
    import sqlite3

    batch = next(_seq)
    conn = sqlite3.connect(path)
    for i in range(n):
        conn.execute(
            "INSERT INTO posts (platform, source, external_id, author, title, url,"
            " timestamp, content, content_markdown, summary, word_count)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, '', ?)",
            (
                platform,
                source,
                f"{source}-{batch}-{i}",
                "author",
                "t",
                f"https://example.com/{source}/{days_ago}/{i}",
                _iso(days_ago),
                body,
                words,
            ),
        )
    conn.commit()
    conn.close()


def test_detects_body_rate_regression(db_path):
    """평소 본문이 채워지던 소스가 갑자기 빈 본문을 저장하기 시작하면 잡는다."""
    _insert(db_path, "producthunt", "producthunt", 60, "본문 " * 300, 300, n=20)
    _insert(db_path, "producthunt", "producthunt", 3, "", 0, n=10)

    issues = scan_source_health(db_path)
    assert len(issues) == 1
    assert issues[0]["kind"] == "body_rate_drop"
    assert issues[0]["source"] == "producthunt"
    assert issues[0]["baseline"] == 1.0
    assert issues[0]["recent"] == 0.0


def test_healthy_source_is_silent(db_path):
    _insert(db_path, "blogs", "blogs/Good", 60, "본문 " * 300, 300, n=20)
    _insert(db_path, "blogs", "blogs/Good", 3, "본문 " * 300, 300, n=10)
    assert scan_source_health(db_path) == []


def test_short_but_consistent_source_is_not_flagged(db_path):
    """팟캐스트/제품 태그라인처럼 원래 짧은 소스를 절대 임계로 때리지 않는다."""
    _insert(db_path, "producthunt", "producthunt", 60, "짧은 태그라인", 15, n=20)
    _insert(db_path, "producthunt", "producthunt", 3, "짧은 태그라인", 15, n=10)
    assert scan_source_health(db_path) == []


def test_detects_word_count_collapse(db_path):
    """본문은 있는데 내비게이션만 긁혀 분량이 붕괴한 경우."""
    _insert(db_path, "blogs", "blogs/JS", 60, "본문 " * 2000, 2000, n=20)
    _insert(db_path, "blogs", "blogs/JS", 3, "메뉴 바로가기", 60, n=20)

    issues = scan_source_health(db_path)
    assert len(issues) == 1
    assert issues[0]["kind"] == "word_count_collapse"


def test_social_platforms_skip_word_count_check(db_path):
    """레딧 본문 길이는 사용자가 쓰는 대로라 분량 변동이 회귀가 아니다."""
    _insert(db_path, "reddit", "r/nextjs", 60, "긴 글 " * 150, 150, n=20)
    _insert(db_path, "reddit", "r/nextjs", 3, "짧은 글", 30, n=20)
    assert scan_source_health(db_path) == []


def test_social_platforms_still_checked_for_missing_body(db_path):
    """분량은 안 봐도, 본문이 통째로 안 들어오는 건 소셜에서도 회귀다."""
    _insert(db_path, "reddit", "r/mcp", 60, "본문 " * 100, 100, n=20)
    _insert(db_path, "reddit", "r/mcp", 3, "", 0, n=10)

    issues = scan_source_health(db_path)
    assert [i["kind"] for i in issues] == ["body_rate_drop"]


def test_word_count_check_needs_a_larger_sample(db_path):
    """분량은 흔들리기 쉬워 표본이 적으면 판정하지 않는다."""
    _insert(db_path, "blogs", "blogs/Few", 60, "본문 " * 2000, 2000, n=20)
    _insert(db_path, "blogs", "blogs/Few", 3, "짧음", 50, n=8)
    assert scan_source_health(db_path) == []


def test_detects_source_broken_from_the_start(db_path):
    """기준선이 없는(처음부터 깨진) 소스도 잡는다.

    producthunt가 실제로 이 경우였다. DB 시작 시점부터 본문의 70%가 비어서
    기준선 대비 방식만으로는 탐지 0건이었다.
    """
    _insert(db_path, "producthunt", "producthunt", 3, "", 0, n=14)
    _insert(db_path, "producthunt", "producthunt", 3, "본문 " * 300, 300, n=6)

    issues = scan_source_health(db_path)
    assert [i["kind"] for i in issues] == ["low_body_rate"]
    assert issues[0]["recent"] == 0.3


def test_youtube_backfill_rows_are_exempt(db_path):
    """youtube-history 백필은 본문이 비는 게 정상이다 (AGENTS.md 예외)."""
    _insert(db_path, "youtube", "youtube/Ch", 3, "", 0, n=20)
    assert scan_source_health(db_path) == []


def test_body_column_is_content_markdown_only(db_path):
    """summary만 채워진 행을 '본문 있음'으로 세면 안 된다 (정본 계약)."""
    import sqlite3

    _insert(db_path, "producthunt", "producthunt", 3, "", 0, n=20)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE posts SET summary = '제품 태그라인'")
    conn.commit()
    conn.close()

    issues = scan_source_health(db_path)
    assert [i["kind"] for i in issues] == ["low_body_rate"]


def test_thin_samples_are_ignored(db_path):
    """표본이 적으면 판정하지 않는다 (신규 소스가 매번 경고를 내지 않게)."""
    _insert(db_path, "blogs", "blogs/New", 60, "본문 " * 300, 300, n=3)
    _insert(db_path, "blogs", "blogs/New", 3, "", 0, n=2)
    assert scan_source_health(db_path) == []


def test_sources_are_scored_independently(db_path):
    """한 소스의 회귀가 같은 플랫폼의 멀쩡한 소스를 오염시키지 않는다."""
    _insert(db_path, "blogs", "blogs/Broken", 60, "본문 " * 300, 300, n=20)
    _insert(db_path, "blogs", "blogs/Broken", 3, "", 0, n=10)
    _insert(db_path, "blogs", "blogs/Fine", 60, "본문 " * 300, 300, n=20)
    _insert(db_path, "blogs", "blogs/Fine", 3, "본문 " * 300, 300, n=10)

    issues = scan_source_health(db_path)
    assert [i["source"] for i in issues] == ["blogs/Broken"]


# --- 경계 ---------------------------------------------------------------
#
# 임계가 흐려지면 오탐과 누락이 같이 는다. 양쪽 바로 옆을 고정해 둔다.


def test_body_rate_threshold_is_a_hard_edge(db_path):
    """29%p 하락은 조용하고 31%p 하락은 잡힌다 (BODY_RATE_DROP = 0.30)."""
    for name, empty in (("Quiet", 29), ("Flagged", 31)):
        _insert(db_path, "blogs", f"blogs/{name}", 60, "본문 " * 300, 300, n=20)
        _insert(db_path, "blogs", f"blogs/{name}", 3, "본문 " * 300, 300, n=100 - empty)
        _insert(db_path, "blogs", f"blogs/{name}", 3, "", 0, n=empty)

    assert [i["source"] for i in scan_source_health(db_path)] == ["blogs/Flagged"]


def test_low_body_rate_threshold_is_a_hard_edge(db_path):
    """기준선이 없을 때의 하한선. 본문 51%는 조용하고 49%는 잡힌다 (0.50)."""
    for name, ok in (("Quiet", 51), ("Flagged", 49)):
        _insert(db_path, "blogs", f"blogs/{name}", 3, "본문 " * 300, 300, n=ok)
        _insert(db_path, "blogs", f"blogs/{name}", 3, "", 0, n=100 - ok)

    issues = scan_source_health(db_path)
    assert [(i["source"], i["kind"]) for i in issues] == [
        ("blogs/Flagged", "low_body_rate")
    ]


def test_word_count_threshold_is_a_hard_edge(db_path):
    """기준선의 41%는 통과하고 39%는 잡힌다 (WORD_COUNT_RATIO = 0.40)."""
    for name, words in (("Quiet", 410), ("Flagged", 390)):
        _insert(db_path, "blogs", f"blogs/{name}", 60, "본문 " * 1000, 1000, n=20)
        _insert(db_path, "blogs", f"blogs/{name}", 3, "본문", words, n=20)

    assert [i["source"] for i in scan_source_health(db_path)] == ["blogs/Flagged"]


def test_posts_older_than_the_baseline_window_are_ignored(db_path):
    """기준선은 최근 14일 직전의 120일이다. 그보다 옛날 데이터는 '평소'가 아니다."""
    _insert(db_path, "blogs", "blogs/Old", 300, "본문 " * 300, 300, n=40)  # 창 밖
    _insert(db_path, "blogs", "blogs/Old", 3, "", 0, n=10)

    issues = scan_source_health(db_path)
    # 비교 대상이 없으니 기준선 대비가 아니라 하한선으로 잡혀야 한다.
    assert [i["kind"] for i in issues] == ["low_body_rate"]
    assert issues[0]["baseline"] is None


def test_windows_are_movable_for_retro_validation(db_path):
    """되감기 검증이 과거 시점을 재현하려면 창을 옮길 수 있어야 한다.

    producthunt 사고를 실제로 이 방식으로 재현했다.
    """
    _insert(db_path, "blogs", "blogs/Past", 200, "본문 " * 300, 300, n=20)
    _insert(db_path, "blogs", "blogs/Past", 100, "", 0, n=10)

    # 오늘 기준 창에는 양쪽 다 안 들어와 판정할 게 없다.
    assert scan_source_health(db_path) == []

    issues = scan_source_health(db_path, recent_days=120, baseline_days=200)
    assert [i["kind"] for i in issues] == ["body_rate_drop"]
