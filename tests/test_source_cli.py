"""`skim source` 서브커맨드 end-to-end. 네트워크는 probe_source 모킹으로 막는다.

이 커맨드들은 doctor/coverage와 달리 `--db`를 받지 않고 전역 DB_PATH를 쓴다.
fixture가 그 전역을 tmp로 갈아끼운다.
"""

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from skim_cli import cli as cli_mod
from skim_core import db as db_mod
from skim_core.source_probe import ProbeResult

runner = CliRunner()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "skim.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    db_mod.init_db(path)
    return path


def _result(**kw) -> ProbeResult:
    defaults = {
        "url": "https://example.com/blog",
        "feed_url": "https://example.com/blog/rss",
        "feed_title": "Example Blog",
        "site_url": "https://example.com/blog",
        "discovery": "path:/blog/rss",
        "items": 30,
        "tier": "rss+enrich",
    }
    defaults.update(kw)
    return ProbeResult(**defaults)


def _stub(monkeypatch, default=None, calls=None):
    """probe_source 대체. calls를 주면 (url, sample) 호출 이력을 받는다."""

    def fake(url, sample=True):
        if calls is not None:
            calls.append((url, sample))
        return default if default is not None else _result(url=url)

    monkeypatch.setattr(cli_mod, "probe_source", fake)


def _sources(db, platform="blogs"):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM tracked_sources WHERE platform = ? ORDER BY display_name",
            (platform,),
        )
    ]
    conn.close()
    return rows


def _seed(db, name, feed_url, tier=None, platform="blogs"):
    db_mod.upsert_tracked_source(
        platform=platform,
        canonical_id=feed_url,
        display_name=name,
        handle_or_url=feed_url,
        feed_url=feed_url,
        fetch_tier=tier,
        db_path=db,
    )


# --- probe ---------------------------------------------------------------


def test_probe_diagnoses_without_registering(db, monkeypatch):
    """probe가 add와 갈리는 유일한 지점이다. 읽기 전용이어야 한다."""
    _stub(monkeypatch)

    result = runner.invoke(cli_mod.app, ["source", "probe", "https://example.com/blog"])

    assert result.exit_code == 0, result.stderr
    assert "rss+enrich" in result.stdout
    assert _sources(db) == []


def test_probe_json_carries_warnings(db, monkeypatch):
    """--emit json은 스크립트가 읽는다. 경고가 빠지면 자동화가 못 본다."""
    _stub(monkeypatch, default=_result(warnings=["백필 제한: 피드가 30건만 제공"]))

    result = runner.invoke(
        cli_mod.app,
        ["source", "probe", "https://example.com/blog", "--emit", "json"],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload[0]["feed_url"] == "https://example.com/blog/rss"
    assert payload[0]["warnings"] == ["백필 제한: 피드가 30건만 제공"]


def test_probe_accepts_multiple_urls(db, monkeypatch):
    calls = []
    _stub(monkeypatch, calls=calls)

    result = runner.invoke(
        cli_mod.app,
        ["source", "probe", "https://a.example.com", "https://b.example.com"],
    )

    assert result.exit_code == 0, result.stderr
    assert [c[0] for c in calls] == ["https://a.example.com", "https://b.example.com"]


def test_no_sample_flag_reaches_the_prober(db, monkeypatch):
    """플래그가 전달 안 되면 조용히 느려지기만 해서 눈에 띄지 않는다."""
    calls = []
    _stub(monkeypatch, calls=calls)

    runner.invoke(
        cli_mod.app, ["source", "probe", "https://example.com/blog", "--no-sample"]
    )

    assert calls == [("https://example.com/blog", False)]


# --- add -----------------------------------------------------------------


def test_add_stores_the_observed_tier(db, monkeypatch):
    _stub(monkeypatch)

    result = runner.invoke(cli_mod.app, ["source", "add", "https://example.com/blog"])

    assert result.exit_code == 0, result.stderr
    (row,) = _sources(db)
    assert row["display_name"] == "Example Blog"
    assert row["fetch_tier"] == "rss+enrich"
    assert row["feed_url"] == "https://example.com/blog/rss"
    # 정체는 피드 주소가 아니라 사이트다. 피드가 옮겨가도 같은 소스여야 한다.
    assert row["canonical_id"] == "https://example.com/blog"
    assert row["source_type"] == "feed"


def test_add_refuses_scrape_tier_and_writes_nothing(db, monkeypatch):
    """피드가 없으면 전용 크롤러가 필요하다. 등록만 해두면 조용히 0건이 된다."""
    _stub(
        monkeypatch,
        default=_result(feed_url=None, feed_title="", site_url="", tier="scrape"),
    )

    result = runner.invoke(cli_mod.app, ["source", "add", "https://example.com/blog"])

    assert result.exit_code == 1
    assert _sources(db) == []


def test_add_force_records_scrape_tier(db, monkeypatch):
    _stub(
        monkeypatch,
        default=_result(feed_url=None, feed_title="", site_url="", tier="scrape"),
    )

    result = runner.invoke(
        cli_mod.app, ["source", "add", "https://example.com/blog", "--force"]
    )

    assert result.exit_code == 0, result.stderr
    (row,) = _sources(db)
    assert row["fetch_tier"] == "scrape"
    # 피드 제목도 채널 링크도 없으면 도메인과 입력 URL로 떨어진다.
    assert row["display_name"] == "example.com"
    assert row["canonical_id"] == "https://example.com/blog"


def test_add_name_overrides_the_feed_title(db, monkeypatch):
    _stub(monkeypatch)

    runner.invoke(
        cli_mod.app,
        ["source", "add", "https://example.com/blog", "--name", "손으로 지은 이름"],
    )

    assert _sources(db)[0]["display_name"] == "손으로 지은 이름"


def test_add_platform_routes_the_source(db, monkeypatch):
    _stub(monkeypatch)

    runner.invoke(
        cli_mod.app,
        ["source", "add", "https://example.com/blog", "--platform", "everyto"],
    )

    assert _sources(db, "blogs") == []
    assert len(_sources(db, "everyto")) == 1


def test_re_adding_updates_in_place(db, monkeypatch):
    """같은 사이트를 다시 등록해도 행이 늘지 않고 관측값만 갱신된다."""
    _stub(monkeypatch)
    runner.invoke(cli_mod.app, ["source", "add", "https://example.com/blog"])

    _stub(monkeypatch, default=_result(tier="rss+render"))
    result = runner.invoke(cli_mod.app, ["source", "add", "https://example.com/blog"])

    assert "갱신" in result.stdout
    (row,) = _sources(db)
    assert row["fetch_tier"] == "rss+render"


# --- list ----------------------------------------------------------------


def test_list_empty_points_at_sync(db):
    result = runner.invoke(cli_mod.app, ["source", "list"])

    assert result.exit_code == 0, result.stderr
    assert "skim source sync" in result.stdout


def test_list_markdown_renders_the_committed_inventory(db, monkeypatch):
    """소스 목록이 DB로 옮겨가 저장소 diff에서 사라진 것을 이 출력이 되돌린다."""
    _stub(monkeypatch)
    runner.invoke(cli_mod.app, ["source", "add", "https://example.com/blog"])

    result = runner.invoke(cli_mod.app, ["source", "list", "--emit", "markdown"])

    assert result.exit_code == 0, result.stderr
    assert "| Platform | Source | Tier | Feed |" in result.stdout
    assert (
        "| blogs | Example Blog | rss+enrich | https://example.com/blog/rss |"
        in result.stdout
    )


def test_list_json_labels_the_platform(db, monkeypatch):
    _stub(monkeypatch)
    runner.invoke(cli_mod.app, ["source", "add", "https://example.com/blog"])

    result = runner.invoke(
        cli_mod.app, ["source", "list", "--platform", "blogs", "--emit", "json"]
    )

    assert [r["platform"] for r in json.loads(result.stdout)] == ["blogs"]


def test_list_includes_disabled_sources(db, monkeypatch):
    """비활성 소스가 목록에서 사라지면 왜 안 들어오는지 알 방법이 없다."""
    _stub(monkeypatch)
    runner.invoke(cli_mod.app, ["source", "add", "https://example.com/blog"])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE tracked_sources SET is_enabled = 0")
    conn.commit()
    conn.close()

    result = runner.invoke(cli_mod.app, ["source", "list", "--emit", "json"])

    assert len(json.loads(result.stdout)) == 1


# --- sync ----------------------------------------------------------------

SEED = {"blogs": {"Seeded": "https://seed.example.com/rss"}}


def test_sync_imports_config_then_goes_quiet(db, monkeypatch):
    """멱등해야 반복 실행이 중복 행을 만들지 않는다."""
    monkeypatch.setattr(cli_mod, "registry_platforms", lambda: SEED)

    first = runner.invoke(cli_mod.app, ["source", "sync"])
    second = runner.invoke(cli_mod.app, ["source", "sync"])

    assert first.exit_code == 0, first.stderr
    assert "1개 등록" in first.stdout
    assert "이미 최신이다" in second.stdout
    assert len(_sources(db)) == 1


def test_sync_dry_run_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(cli_mod, "registry_platforms", lambda: SEED)

    result = runner.invoke(cli_mod.app, ["source", "sync", "--dry-run"])

    assert "Seeded" in result.stdout
    assert _sources(db) == []


def test_sync_leaves_tier_for_refresh_to_observe(db, monkeypatch):
    """sync는 네트워크를 안 탄다. tier는 관측값이라 refresh가 채운다."""
    monkeypatch.setattr(cli_mod, "registry_platforms", lambda: SEED)

    runner.invoke(cli_mod.app, ["source", "sync"])

    assert _sources(db)[0]["fetch_tier"] is None


# --- refresh -------------------------------------------------------------


def test_refresh_defaults_to_sources_missing_a_tier(db, monkeypatch):
    _seed(db, "Known", "https://known.example.com/rss", tier="rss")
    _seed(db, "Unknown", "https://unknown.example.com/rss")
    calls = []
    _stub(monkeypatch, calls=calls)

    result = runner.invoke(cli_mod.app, ["source", "refresh"])

    assert result.exit_code == 0, result.stderr
    assert [c[0] for c in calls] == ["https://unknown.example.com/rss"]


def test_refresh_all_reobserves_every_source(db, monkeypatch):
    _seed(db, "Known", "https://known.example.com/rss", tier="rss")
    _seed(db, "Unknown", "https://unknown.example.com/rss")
    calls = []
    _stub(monkeypatch, calls=calls)

    result = runner.invoke(cli_mod.app, ["source", "refresh", "--all"])

    assert sorted(c[0] for c in calls) == [
        "https://known.example.com/rss",
        "https://unknown.example.com/rss",
    ]
    assert "2개 진단" in result.stdout


def test_refresh_flags_a_feed_that_died(db, monkeypatch):
    """every.to/Guides 회귀: 피드가 HTTP 500이 되고 2개월간 아무 신호도 없었다.

    살아있던 tier가 scrape로 떨어지는 것이 유일하게 관측 가능한 신호다.
    """
    _seed(db, "Guides", "https://every.to/guides/feed", tier="rss+enrich")
    _stub(
        monkeypatch,
        default=_result(
            url="https://every.to/guides/feed",
            feed_url=None,
            feed_title="",
            site_url="",
            tier="scrape",
            warnings=["RSS/Atom 피드 없음. 인덱스 파싱 크롤러를 직접 만들어야 한다"],
        ),
    )

    result = runner.invoke(cli_mod.app, ["source", "refresh", "--all"])

    assert result.exit_code == 0, result.stderr
    assert "피드가 사라졌다" in result.stdout
    assert "변경: rss+enrich -> scrape" in result.stdout
    assert "1개 등급 변경" in result.stdout
    # 죽은 주소를 지우면 다음 refresh가 대상에서 빼버려 영영 재확인을 못 한다.
    assert _sources(db)[0]["feed_url"] == "https://every.to/guides/feed"


def test_refresh_stays_quiet_when_nothing_moved(db, monkeypatch):
    _seed(db, "Stable", "https://stable.example.com/rss", tier="rss+enrich")
    _stub(monkeypatch)

    result = runner.invoke(cli_mod.app, ["source", "refresh", "--all"])

    assert "변경:" not in result.stdout
    assert "0개 등급 변경" in result.stdout


def test_refresh_skips_sources_without_a_feed_url(db, monkeypatch):
    """데스크톱에서 등록한 youtube 채널 행에는 feed_url이 없다."""
    db_mod.upsert_tracked_source(
        platform="blogs",
        canonical_id="UC123",
        display_name="Channel",
        handle_or_url="UC123",
        db_path=db,
    )
    calls = []
    _stub(monkeypatch, calls=calls)

    result = runner.invoke(cli_mod.app, ["source", "refresh", "--all"])

    assert calls == []
    assert "갱신할 소스 없음" in result.stdout


def test_refresh_surfaces_probe_warnings(db, monkeypatch):
    _seed(db, "Thin", "https://thin.example.com/rss")
    _stub(
        monkeypatch,
        default=_result(warnings=["본문 빈약(60단어). 내비게이션만 긁혔을 수 있다"]),
    )

    result = runner.invoke(cli_mod.app, ["source", "refresh"])

    assert "경고: 본문 빈약" in result.stdout
