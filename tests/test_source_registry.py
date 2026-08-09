"""tracked_sources 레지스트리와 blogs 크롤러의 소스 해석 회귀 테스트."""

import sqlite3

import pytest

from skim_core import db as db_mod
from skim_core import source_registry as registry_mod
from skim_core.crawlers.feed import blogs as blogs_mod
from skim_core.crawlers.feed import everyto as everyto_mod
from skim_core.db import init_db, list_tracked_sources, upsert_tracked_source


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "skim.db"
    init_db(path)
    return path


def test_migration_adds_columns_to_legacy_table(tmp_path):
    """feed_url/fetch_tier 없던 옛 DB도 init_db 한 번으로 따라온다."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tracked_sources (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            platform      TEXT NOT NULL,
            source_type   TEXT NOT NULL,
            display_name  TEXT NOT NULL,
            canonical_id  TEXT NOT NULL,
            handle_or_url TEXT,
            is_enabled    INTEGER NOT NULL DEFAULT 1,
            focus_level   INTEGER NOT NULL DEFAULT 0,
            notes         TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(platform, canonical_id)
        );
        INSERT INTO tracked_sources (platform, source_type, display_name, canonical_id)
        VALUES ('youtube', 'channel', 'Existing', 'UC123');
        """
    )
    conn.commit()
    conn.close()

    init_db(path)

    cols = {
        r[1]
        for r in sqlite3.connect(path).execute("PRAGMA table_info(tracked_sources)")
    }
    assert {"feed_url", "fetch_tier"} <= cols
    # 기존 행은 보존된다.
    assert len(list_tracked_sources("youtube", db_path=path)) == 1


def test_upsert_reports_new_vs_update(db_path):
    created = upsert_tracked_source(
        platform="blogs",
        canonical_id="https://example.com",
        display_name="Example",
        feed_url="https://example.com/rss",
        fetch_tier="rss",
        db_path=db_path,
    )
    assert created is True

    again = upsert_tracked_source(
        platform="blogs",
        canonical_id="https://example.com",
        display_name="Example Renamed",
        feed_url="https://example.com/feed.xml",
        fetch_tier="rss+enrich",
        db_path=db_path,
    )
    assert again is False

    rows = list_tracked_sources("blogs", db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Example Renamed"
    # 관측값(tier, feed_url)은 재등록마다 덮어쓴다.
    assert rows[0]["fetch_tier"] == "rss+enrich"
    assert rows[0]["feed_url"] == "https://example.com/feed.xml"


def test_upsert_keeps_notes_when_not_supplied(db_path):
    """사람이 적어둔 notes를 probe 재등록이 지우지 않는다."""
    upsert_tracked_source(
        platform="blogs",
        canonical_id="https://example.com",
        display_name="Example",
        feed_url="https://example.com/rss",
        notes="손으로 적은 메모",
        db_path=db_path,
    )
    upsert_tracked_source(
        platform="blogs",
        canonical_id="https://example.com",
        display_name="Example",
        feed_url="https://example.com/rss",
        fetch_tier="rss",
        db_path=db_path,
    )
    conn = sqlite3.connect(db_path)
    notes = conn.execute("SELECT notes FROM tracked_sources").fetchone()[0]
    assert notes == "손으로 적은 메모"


def test_disabled_source_is_excluded(db_path):
    upsert_tracked_source(
        platform="blogs",
        canonical_id="https://off.example.com",
        display_name="Off",
        feed_url="https://off.example.com/rss",
        db_path=db_path,
    )
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE tracked_sources SET is_enabled = 0")
    conn.commit()
    conn.close()

    assert list_tracked_sources("blogs", db_path=db_path) == []
    assert len(list_tracked_sources("blogs", enabled_only=False, db_path=db_path)) == 1


def _fake_rows(rows):
    def _inner(platform, db_path=None, enabled_only=True):
        del platform, db_path, enabled_only
        return rows

    return _inner


def test_falls_back_to_config_when_registry_empty(monkeypatch):
    monkeypatch.setattr(registry_mod, "list_tracked_sources", _fake_rows([]))
    feeds = registry_mod.resolve_feed_sources(
        "blogs", {"Cfg": "https://cfg.example.com/rss"}
    )
    assert [(f["name"], f["feed_url"]) for f in feeds] == [
        ("Cfg", "https://cfg.example.com/rss")
    ]


def test_registry_wins_over_config(monkeypatch):
    monkeypatch.setattr(
        registry_mod,
        "list_tracked_sources",
        _fake_rows(
            [{"display_name": "Reg", "feed_url": "https://reg.example.com/rss"}]
        ),
    )
    feeds = registry_mod.resolve_feed_sources(
        "blogs", {"Cfg": "https://cfg.example.com/rss"}
    )
    assert [(f["name"], f["feed_url"]) for f in feeds] == [
        ("Reg", "https://reg.example.com/rss")
    ]


def test_rows_without_feed_url_are_reported(monkeypatch, capsys):
    """데스크톱에서 추가한 feed_url 없는 행이 조용히 사라지지 않는다."""
    monkeypatch.setattr(
        registry_mod,
        "list_tracked_sources",
        _fake_rows(
            [
                {"display_name": "Good", "feed_url": "https://good.example.com/rss"},
                {"display_name": "NoFeed", "feed_url": None},
            ]
        ),
    )
    feeds = registry_mod.resolve_feed_sources("blogs", {})
    assert [f["name"] for f in feeds] == ["Good"]
    assert "NoFeed" in capsys.readouterr().out


def test_non_feed_source_types_are_excluded(monkeypatch):
    """전용 파서가 필요한 소스는 피드 순회 대상에서 뺀다."""
    monkeypatch.setattr(
        registry_mod,
        "list_tracked_sources",
        _fake_rows(
            [
                {
                    "display_name": "Feed",
                    "feed_url": "https://a.example.com/rss",
                    "source_type": "feed",
                },
                {
                    "display_name": "Scraped",
                    "feed_url": "https://b.example.com",
                    "source_type": "anthropic",
                },
            ]
        ),
    )
    feeds = registry_mod.resolve_feed_sources("ailabs", {})
    assert [f["name"] for f in feeds] == ["Feed"]


def test_crawlers_go_through_the_shared_resolver():
    """blogs/everyto가 각자 구현을 갖지 않고 공용 경로를 쓴다."""
    assert blogs_mod.resolve_feed_sources is registry_mod.resolve_feed_sources
    assert everyto_mod.resolve_feed_sources is registry_mod.resolve_feed_sources


def test_list_returns_empty_on_db_error(monkeypatch):
    """DB를 못 읽어도 예외로 크롤을 죽이지 않는다 (호출자가 폴백한다)."""

    def _boom(_path=None):
        raise sqlite3.Error("locked")

    monkeypatch.setattr(db_mod, "get_connection", _boom)
    assert db_mod.list_tracked_sources("blogs") == []
