"""`crawl --catch-up`: 놓친 회차를 다음 회차가 채우는지 검증한다 (#21).

데일리는 매일 "전날 0시부터"만 본다. 한 회차를 놓치면 그날 글이 다음 창에 다시
들어오지 않아 영구 유실됐다. 크롤러만 가짜로 두고 DB 함수는 conftest가 돌려 둔
임시 DB로 실제로 태운다.
"""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import typer

import skim_cli.cli as main
import skim_core.db as db
from skim_core.models import Post

# 데일리가 도는 시각.
NOW = datetime(2026, 10, 1, 0, 2, tzinfo=main.KST)


def _midnight(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T00:00:00+09:00")


def _post(platform: str, index: int = 1) -> Post:
    return Post(
        platform=platform,
        external_id=f"{platform}-{index}",
        author="a",
        content="c",
        content_markdown="body",
        url=f"https://example.com/{platform}/{index}",
        timestamp="2026-09-30T12:00:00+09:00",
    )


def _crawl(tmp_path, results, *, now=NOW, catch_up=True, count=None, no_content=False):
    """results는 플랫폼 -> 게시글 목록 또는 예외다. (크롤러가 받은 since, 출력)을 돌려준다."""
    seen = {}

    async def fake_crawler(platform, options):
        seen[platform] = options.get("since")
        result = results[platform]
        if isinstance(result, Exception):
            raise result
        return result

    with (
        patch.object(main, "run_single_crawler", fake_crawler),
        patch.object(main, "DATA_DIR", tmp_path / "data"),
        patch.object(main, "datetime") as mock_datetime,
        patch.object(main.typer, "echo") as echo,
    ):
        mock_datetime.now.return_value = now
        try:
            main.crawl(
                platforms=list(results),
                count=count,
                days=1,
                output=None,
                debug=False,
                no_content=no_content,
                user_id=None,
                subreddit=None,
                sort="hot",
                catch_up=catch_up,
            )
        except typer.Exit:
            pass
    printed = " ".join(str(call.args[0]) for call in echo.call_args_list if call.args)
    return seen, printed


def _mark_recent(platform: str) -> None:
    """최근 유입 이력을 만든다. 0건이면 체크포인트를 옮기지 않는 기준이다."""
    db.init_db()
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "INSERT INTO posts (platform, external_id, author, content) VALUES (?, 'old', 'a', 'c')",
        (platform,),
    )
    conn.commit()
    conn.close()


def test_first_run_keeps_the_default_window_and_records_checkpoints(tmp_path):
    seen, _ = _crawl(
        tmp_path, {"hackernews": [_post("hackernews")], "lobsters": [_post("lobsters")]}
    )

    assert seen["hackernews"] == _midnight("2026-09-30")
    assert db.load_crawl_checkpoints() == {"hackernews": NOW, "lobsters": NOW}


def test_missed_runs_widen_the_next_window(tmp_path):
    # 9/28 00:02 회차 뒤로 두 번을 놓쳤다.
    db.init_db()
    db.save_crawl_checkpoint("hackernews", NOW - timedelta(days=3))

    seen, printed = _crawl(tmp_path, {"hackernews": [_post("hackernews")]})

    assert seen["hackernews"] == _midnight("2026-09-28")
    assert "2026-09-28 0시부터" in printed
    assert db.load_crawl_checkpoints()["hackernews"] == NOW


def test_a_normal_day_does_not_reach_back_into_covered_days(tmp_path):
    db.init_db()
    db.save_crawl_checkpoint("hackernews", NOW - timedelta(days=1))

    seen, printed = _crawl(tmp_path, {"hackernews": [_post("hackernews")]})

    assert seen["hackernews"] == _midnight("2026-09-30")
    assert "0시부터" not in printed


def test_failed_platform_keeps_its_checkpoint_for_the_next_run(tmp_path):
    db.init_db()
    yesterday = NOW - timedelta(days=1)
    db.save_crawl_checkpoint("hackernews", yesterday)
    db.save_crawl_checkpoint("lobsters", yesterday)

    _crawl(
        tmp_path,
        {"hackernews": RuntimeError("502"), "lobsters": [_post("lobsters")]},
    )

    checkpoints = db.load_crawl_checkpoints()
    assert checkpoints["hackernews"] == yesterday
    assert checkpoints["lobsters"] == NOW

    # 다음 날 회차는 실패한 날까지 거슬러 본다.
    seen, _ = _crawl(
        tmp_path,
        {"hackernews": [_post("hackernews")]},
        now=NOW + timedelta(days=1),
    )
    assert seen["hackernews"] == _midnight("2026-09-30")


def test_empty_result_moves_only_platforms_without_recent_history(tmp_path):
    # 평소 들어오던 소스의 0건은 고장일 수 있어 체크포인트를 옮기지 않는다.
    _mark_recent("hackernews")

    _crawl(tmp_path, {"hackernews": [], "everyto": []})

    checkpoints = db.load_crawl_checkpoints()
    assert "hackernews" not in checkpoints
    assert checkpoints["everyto"] == NOW


def test_window_stops_at_the_cap_and_says_what_is_lost(tmp_path):
    db.init_db()
    db.save_crawl_checkpoint("hackernews", NOW - timedelta(days=20))

    seen, printed = _crawl(tmp_path, {"hackernews": [_post("hackernews")]})

    assert seen["hackernews"] == _midnight("2026-09-24")
    assert "채우지 못합니다" in printed


def test_sns_platforms_are_left_alone(tmp_path):
    seen, _ = _crawl(tmp_path, {"threads": [_post("threads")]})

    assert seen["threads"] is None
    assert db.load_crawl_checkpoints() == {}


def test_without_the_flag_nothing_is_recorded(tmp_path):
    db.init_db()
    db.save_crawl_checkpoint("hackernews", NOW - timedelta(days=3))

    seen, _ = _crawl(tmp_path, {"hackernews": [_post("hackernews")]}, catch_up=False)

    assert seen["hackernews"] == _midnight("2026-09-30")
    assert db.load_crawl_checkpoints()["hackernews"] == NOW - timedelta(days=3)


@pytest.mark.parametrize("options", [{"count": 5}, {"no_content": True}])
def test_partial_runs_cannot_move_checkpoints(tmp_path, options):
    # 창을 본문까지 끝까지 채운 회차만 체크포인트를 옮길 수 있다.
    seen, _ = _crawl(tmp_path, {"hackernews": [_post("hackernews")]}, **options)

    assert not seen
    db.init_db()
    assert db.load_crawl_checkpoints() == {}


def test_unreadable_checkpoints_turn_catch_up_off(tmp_path):
    # 빈 체크포인트로 이어 가면 이번 회차가 놓친 구간을 건너뛴 채 오늘로 옮긴다.
    with patch.object(
        main, "load_crawl_checkpoints", side_effect=sqlite3.OperationalError("locked")
    ):
        seen, printed = _crawl(tmp_path, {"hackernews": [_post("hackernews")]})

    assert seen["hackernews"] == _midnight("2026-09-30")
    assert "--catch-up 없이" in printed
    assert db.load_crawl_checkpoints() == {}


def test_checkpoint_needs_a_timezone():
    db.init_db()
    with pytest.raises(ValueError):
        db.save_crawl_checkpoint("hackernews", datetime(2026, 10, 1, 0, 2))
