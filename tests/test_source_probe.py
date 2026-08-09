"""source_probe 회귀 테스트. 네트워크는 전부 모킹한다."""

from types import SimpleNamespace

import pytest

from skim_core import source_probe


def _rss(item_count: int, with_body: bool = False, with_author: bool = True) -> bytes:
    items = ""
    for i in range(item_count):
        body = ""
        if with_body:
            body = "<content:encoded><![CDATA[<p>" + ("word " * 300) + "</p>]]></content:encoded>"
        author = "<author>crew@example.com</author>" if with_author else ""
        items += (
            f"<item><title>post {i}</title>"
            f"<link>https://example.com/posts/{i}</link>"
            f"<pubDate>Wed, 05 Aug 2026 03:43:27 GMT</pubDate>"
            f"{author}{body}</item>"
        )
    return (
        '<?xml version="1.0"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        f"<channel><title>t</title>{items}</channel></rss>"
    ).encode()


def _resp(content: bytes, url: str, content_type: str = "text/xml"):
    return SimpleNamespace(
        content=content,
        text=content.decode("utf-8", "ignore"),
        url=url,
        headers={"content-type": content_type},
        status_code=200,
    )


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """기본은 전부 실패. 각 테스트가 필요한 URL만 열어준다."""
    monkeypatch.setattr(source_probe, "_get", lambda url: None)


def test_discovers_feed_on_path_suffix(monkeypatch):
    """kakao.vc 케이스: 루트 후보는 죽고 {경로}/rss만 산다."""
    feed_url = "https://www.kakao.vc/blog/rss"

    def fake_get(url):
        if url == feed_url:
            return _resp(_rss(50), feed_url)
        if url == "https://www.kakao.vc/blog":
            return _resp(b"<html><head></head><body>no feed link</body></html>", url, "text/html")
        return None

    monkeypatch.setattr(source_probe, "_get", fake_get)
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 1500}, "trafilatura", None),
    )

    result = source_probe.probe_source("https://www.kakao.vc/blog")
    assert result.feed_url == feed_url
    assert result.discovery == "path:/blog/rss"
    assert result.tier == "rss+enrich"


def test_full_content_feed_is_plain_rss(monkeypatch):
    """피드가 본문을 실어 보내면 추출기를 태우지 않는다."""
    feed_url = "https://example.com/feed/"
    monkeypatch.setattr(
        source_probe,
        "_get",
        lambda url: (_resp(_rss(30, with_body=True), feed_url) if url == feed_url else None),
    )

    def _boom(url, title):  # pragma: no cover — 호출되면 안 된다
        raise AssertionError("본문이 피드에 있으면 추출하지 않아야 한다")

    monkeypatch.setattr(source_probe, "extract_article_content", _boom)

    result = source_probe.probe_source(feed_url)
    assert result.tier == "rss"
    assert result.sample_url == ""


def test_playwright_extraction_downgrades_to_render_tier(monkeypatch):
    """tech.kakao.com 케이스: JS 렌더가 필요하면 등급이 내려가고 경고가 붙는다."""
    feed_url = "https://tech.kakao.com/feed/"
    monkeypatch.setattr(
        source_probe,
        "_get",
        lambda url: _resp(_rss(10), feed_url) if url == feed_url else None,
    )
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 2770}, "playwright+trafilatura", None),
    )

    result = source_probe.probe_source(feed_url)
    assert result.tier == "rss+render"
    assert any("playwright" in w for w in result.warnings)
    # 10건짜리 피드는 백필이 안 된다는 경고가 함께 나와야 한다.
    assert any("백필 제한" in w for w in result.warnings)


def test_thin_body_raises_warning(monkeypatch):
    """내비게이션만 긁힌 60단어짜리를 조용히 통과시키지 않는다."""
    feed_url = "https://example.com/feed/"
    monkeypatch.setattr(
        source_probe,
        "_get",
        lambda url: _resp(_rss(30), feed_url) if url == feed_url else None,
    )
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 60}, "defuddle", None),
    )

    result = source_probe.probe_source(feed_url)
    assert any("본문 빈약" in w for w in result.warnings)


def test_no_feed_falls_back_to_scrape():
    """피드도 sitemap도 없으면 scrape 등급 + 직접 만들라는 경고."""
    result = source_probe.probe_source("https://example.com/blog")
    assert result.tier == "scrape"
    assert result.feed_url is None
    assert any("피드 없음" in w for w in result.warnings)


def test_missing_author_is_reported(monkeypatch):
    feed_url = "https://example.com/feed/"
    monkeypatch.setattr(
        source_probe,
        "_get",
        lambda url: (_resp(_rss(30, with_author=False), feed_url) if url == feed_url else None),
    )
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 900}, "trafilatura", None),
    )

    result = source_probe.probe_source(feed_url)
    assert any("author 없음" in w for w in result.warnings)
