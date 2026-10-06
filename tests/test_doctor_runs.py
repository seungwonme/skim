"""doctor의 시각 표기(#56)와 회차 경고(#62)."""

import json
import time

import pytest
from typer.testing import CliRunner

from skim_cli.cli import app
from skim_core.db import get_connection, init_db

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr("skim_cli.cli.probe_user_agent", lambda *a, **k: None)
    monkeypatch.setattr(
        "skim_cli.cli._playwright_status", lambda: {"ok": True, "detail": "stub"}
    )
    # CI에는 yt-dlp, bunx가 없다. 설치된 도구에 따라 --strict 결과가 갈리지 않게 한다.
    monkeypatch.setattr("skim_cli.cli.shutil.which", lambda name: f"/stub/{name}")


@pytest.fixture
def kst(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Seoul")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data" / "skim.db"
    init_db(path)
    return path


def _add_run(db, status, started="datetime('now')"):
    """`started`는 SQLite 식이다 (예: "datetime('now', '-2 days')")."""
    conn = get_connection(db)
    cur = conn.execute(
        f"INSERT INTO runs (status, started_at) VALUES (?, {started})", (status,)
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def _doctor(db, *args):
    result = runner.invoke(app, ["doctor", "--db", str(db), *args])
    return result, result.stdout


def _listed(out):
    return sum(1 for line in out.splitlines() if line.startswith("  #"))


def test_runs_and_latest_crawl_show_local_time_with_tz_label(db, kst):
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO runs (status, started_at) VALUES ('success', '2026-10-05 15:02:14')"
    )
    conn.execute(
        """INSERT INTO posts (platform, external_id, author, title, content, url,
                              timestamp, crawled_at)
           VALUES ('hackernews', 'x', 'a', 't', 'c', 'https://e.com/x', 'ts',
                   '2026-10-05 15:03:00')"""
    )
    conn.commit()
    conn.close()

    result, out = _doctor(db)

    assert result.exit_code == 0, out
    assert "started=2026-10-06 00:02:14 KST" in out
    assert "latest=2026-10-06 00:03:00 KST" in out


def test_json_output_keeps_stored_utc_strings(db, kst):
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO runs (status, started_at) VALUES ('success', '2026-10-05 15:02:14')"
    )
    conn.commit()
    conn.close()

    _, out = _doctor(db, "--emit", "json")

    assert json.loads(out)["runs"][0]["started_at"] == "2026-10-05 15:02:14"


def test_runs_option_sets_how_many_runs_are_listed(db):
    for _ in range(12):
        _add_run(db, "success")

    _, default_out = _doctor(db)
    _, ten_out = _doctor(db, "--runs", "10")
    _, one_out = _doctor(db, "--runs", "1")

    assert (_listed(default_out), _listed(ten_out), _listed(one_out)) == (5, 10, 1)


def test_unrecovered_failure_beyond_listed_runs_warns_and_fails_strict(db):
    # 실패 뒤에 success가 있으면 목록(5회차) 밖이어도 복구다.
    _add_run(db, "failed", "datetime('now', '-4 days')")
    for _ in range(4):
        _add_run(db, "success", "datetime('now', '-3 days')")
    result, out = _doctor(db, "--strict")
    assert result.exit_code == 0, out

    # 4번째 위치(옛 판정의 최근 3회차 밖)의 interrupted 뒤에 success가 없으면 경고한다.
    _add_run(db, "interrupted", "datetime('now', '-1 days')")
    for _ in range(3):
        _add_run(db, "degraded", "datetime('now')")
    result, out = _doctor(db, "--strict", "--runs", "2")

    assert result.exit_code == 1
    assert "warning: recent runs need attention" in out


def test_recovered_failure_is_informational_only(db):
    failed = _add_run(db, "failed", "datetime('now', '-2 days')")
    fixed = _add_run(db, "success", "datetime('now', '-1 days')")

    result, out = _doctor(db, "--strict")

    assert result.exit_code == 0, out
    assert f"recovered: #{failed} failed -> #{fixed} success" in out
    assert "recent runs need attention" not in out


def test_failure_older_than_seven_days_is_ignored(db):
    _add_run(db, "failed", "datetime('now', '-8 days')")

    result, out = _doctor(db, "--strict")

    assert result.exit_code == 0, out
    assert "recent runs need attention" not in out
    assert "recovered:" not in out


def test_latest_running_run_still_warns(db):
    _add_run(db, "success", "datetime('now', '-1 days')")
    _add_run(db, "running")

    result, out = _doctor(db, "--strict")

    assert result.exit_code == 1
    assert "recent runs need attention" in out
