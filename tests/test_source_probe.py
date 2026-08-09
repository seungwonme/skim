"""source_probe 회귀 테스트. 네트워크는 전부 모킹한다."""

from types import SimpleNamespace

import pytest

from skim_core import source_probe


def _rss(item_count: int, with_body: bool = False, with_author: bool = True) -> bytes:
    items = ""
    for i in range(item_count):
        body = ""
        if with_body:
            body = (
                "<content:encoded><![CDATA[<p>"
                + ("word " * 300)
                + "</p>]]></content:encoded>"
            )
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
            return _resp(
                b"<html><head></head><body>no feed link</body></html>", url, "text/html"
            )
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
        lambda url: (
            _resp(_rss(30, with_body=True), feed_url) if url == feed_url else None
        ),
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
        lambda url: (
            _resp(_rss(30, with_author=False), feed_url) if url == feed_url else None
        ),
    )
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 900}, "trafilatura", None),
    )

    result = source_probe.probe_source(feed_url)
    assert any("author 없음" in w for w in result.warnings)


# --- 발견 순서 -----------------------------------------------------------
#
# 어느 피드를 잡느냐가 곧 데이터 손실이라 순서에 회귀가 나면 조용히 글이 샌다.


def test_path_feed_wins_over_root_feed(monkeypatch):
    """tech.kakao.com 실측: 루트 /feed/ 가 최근 글 2건을 빠뜨리고 /blog/rss 가 정본이었다."""

    def fake_get(url):
        if url == "https://tech.kakao.com/blog/rss":
            return _resp(_rss(12), url)
        if url == "https://tech.kakao.com/feed/":
            return _resp(_rss(11), url)
        if url == "https://tech.kakao.com/blog":
            return _resp(b"<html><head></head><body></body></html>", url, "text/html")
        return None

    monkeypatch.setattr(source_probe, "_get", fake_get)
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 900}, "trafilatura", None),
    )

    result = source_probe.probe_source("https://tech.kakao.com/blog")
    assert result.feed_url == "https://tech.kakao.com/blog/rss"
    assert result.items == 12


def test_feed_url_short_circuits_candidate_sweep(monkeypatch):
    """피드 주소를 직접 주면 관용 경로를 12번 더 두드리지 않는다."""
    feed_url = "https://example.com/blog/rss"
    seen = []

    def fake_get(url):
        seen.append(url)
        return _resp(_rss(30), feed_url) if url == feed_url else None

    monkeypatch.setattr(source_probe, "_get", fake_get)
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 900}, "trafilatura", None),
    )

    result = source_probe.probe_source(feed_url)
    assert result.discovery == "self"
    assert seen == [feed_url]


def test_link_tag_beats_path_guessing(monkeypatch):
    """사이트가 스스로 알려준 주소가 관용 경로 추측보다 정확하다."""
    declared = "https://example.com/atom-full.xml"
    html = (
        '<html><head><link rel="alternate" type="application/rss+xml" '
        f'href="{declared}"></head></html>'
    ).encode()

    def fake_get(url):
        if url == "https://example.com/blog":
            return _resp(html, url, "text/html")
        if url == declared:
            return _resp(_rss(40), declared)
        if url == "https://example.com/blog/rss":
            return _resp(_rss(5), url)  # 살아 있지만 부실한 후보
        return None

    monkeypatch.setattr(source_probe, "_get", fake_get)
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 900}, "trafilatura", None),
    )

    result = source_probe.probe_source("https://example.com/blog")
    assert result.discovery == "link-tag"
    assert result.feed_url == declared
    assert result.items == 40


# --- 등급 판정과 메타데이터 ----------------------------------------------


def test_no_sample_skips_extraction(monkeypatch):
    """--no-sample은 추출을 건너뛰고 등급을 낙관적으로 rss+enrich로 둔다."""
    feed_url = "https://example.com/feed/"
    monkeypatch.setattr(
        source_probe,
        "_get",
        lambda url: _resp(_rss(30), feed_url) if url == feed_url else None,
    )

    def _boom(url, title):  # pragma: no cover — 호출되면 안 된다
        raise AssertionError("--no-sample 인데 추출기를 태웠다")

    monkeypatch.setattr(source_probe, "extract_article_content", _boom)

    result = source_probe.probe_source(feed_url, sample=False)
    assert result.tier == "rss+enrich"
    assert result.sample_url == ""


def test_failed_extraction_warns_but_keeps_enrich_tier(monkeypatch):
    """추출 실패가 곧 '렌더가 필요하다'는 뜻은 아니다. 등급은 두고 경고만 올린다."""
    feed_url = "https://example.com/feed/"
    monkeypatch.setattr(
        source_probe,
        "_get",
        lambda url: _resp(_rss(30), feed_url) if url == feed_url else None,
    )
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: (None, "failed", "timeout"),
    )

    result = source_probe.probe_source(feed_url)
    assert result.tier == "rss+enrich"
    assert any("본문 추출 실패" in w for w in result.warnings)
    # 실패는 '0단어'가 아니다. 빈약 경고까지 겹쳐 찍으면 원인이 흐려진다.
    assert not any("본문 빈약" in w for w in result.warnings)


def test_undated_entries_are_counted_and_warned(monkeypatch):
    """발행일 없는 항목은 since 필터에서 조용히 빠져 '수집 0건'처럼 보인다."""
    feed_url = "https://example.com/feed/"
    body = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        + "".join(
            f"<item><title>p{i}</title><link>https://example.com/{i}</link>"
            "<author>a@example.com</author></item>"
            for i in range(30)
        )
        + "</channel></rss>"
    ).encode()
    monkeypatch.setattr(
        source_probe,
        "_get",
        lambda url: _resp(body, feed_url) if url == feed_url else None,
    )
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 900}, "trafilatura", None),
    )

    result = source_probe.probe_source(feed_url)
    assert result.undated == 30
    assert result.oldest == ""
    assert any("발행일 없는 항목 30건" in w for w in result.warnings)


def test_site_url_drops_query_and_trailing_slash(monkeypatch):
    """site_url은 canonical_id로 쓰인다. 흔들리면 같은 사이트가 두 행으로 갈라진다."""
    feed_url = "https://example.com/feed/"
    body = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
        "<link>https://example.com/blog/?utm_source=rss</link>"
        "<item><title>p</title><link>https://example.com/p</link>"
        "<pubDate>Wed, 05 Aug 2026 03:43:27 GMT</pubDate>"
        "<author>a@example.com</author></item></channel></rss>"
    ).encode()
    monkeypatch.setattr(
        source_probe,
        "_get",
        lambda url: _resp(body, feed_url) if url == feed_url else None,
    )
    monkeypatch.setattr(
        source_probe,
        "extract_article_content",
        lambda url, title: ({"word_count": 900}, "trafilatura", None),
    )

    result = source_probe.probe_source(feed_url)
    assert result.site_url == "https://example.com/blog"


def test_sitemap_is_reported_when_no_feed(monkeypatch):
    """피드가 없어도 sitemap이 있으면 발행일을 확보할 길은 남아 있다."""

    def fake_get(url):
        if url == "https://example.com/blog/sitemap.xml":
            return _resp(
                b'<?xml version="1.0"?><urlset><url><loc>x</loc></url></urlset>', url
            )
        return None

    monkeypatch.setattr(source_probe, "_get", fake_get)

    result = source_probe.probe_source("https://example.com/blog")
    assert result.tier == "scrape"
    assert result.sitemap_url == "https://example.com/blog/sitemap.xml"
    assert not any("sitemap도 없음" in w for w in result.warnings)


def test_format_renders_the_no_feed_case():
    """CLI가 사람에게 보여주는 유일한 표면이라 조용히 비면 진단이 안 된다."""
    text = source_probe.format_probe_result(
        source_probe.probe_source("https://example.com/blog")
    )
    assert "feed    없음" in text
    assert "-> tier=scrape" in text
    assert "경고:" in text
