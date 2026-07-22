"""
@file test_producthunt_redirect.py
@description PH 피드 content_html에서 제품 외부 사이트 리다이렉트(/r/p/{id})를
              추출하는 _redirect_url 파서와, 본문 최저선인 _tagline 파서 회귀 테스트.
"""

from __future__ import annotations

from skim_core.crawlers.feed.producthunt import _redirect_url, _tagline

_CONTENT_HTML = (
    "<p>Shared, searchable memory for every AI coding agent</p>"
    '<p><a href="https://www.producthunt.com/products/scritty">Discussion</a> | '
    '<a href="https://www.producthunt.com/r/p/1185930?app_id=339">Link</a></p>'
)


def test_extracts_rp_redirect() -> None:
    item = {"content_html": _CONTENT_HTML, "url": "https://www.producthunt.com/products/scritty"}
    assert _redirect_url(item) == "https://www.producthunt.com/r/p/1185930?app_id=339"


def test_falls_back_to_url_when_no_redirect() -> None:
    item = {"content_html": "<p>no link here</p>", "url": "https://www.producthunt.com/products/x"}
    assert _redirect_url(item) == "https://www.producthunt.com/products/x"


def test_ignores_discussion_link_picks_rp() -> None:
    # Discussion(/products) 링크가 먼저 나와도 /r/p 링크만 골라야 한다.
    item = {"content_html": _CONTENT_HTML, "url": ""}
    assert "/r/p/" in _redirect_url(item)


def test_tagline_extracts_first_paragraph() -> None:
    # 첫 <p>의 제품 태그라인만 뽑고, 뒤따르는 Discussion/Link 링크는 버린다.
    item = {"content_html": _CONTENT_HTML}
    assert _tagline(item) == "Shared, searchable memory for every AI coding agent"


def test_tagline_strips_inner_tags_and_whitespace() -> None:
    item = {"content_html": "<p>\n   Track your <b>music</b> repertoire   \n</p><p>x</p>"}
    assert _tagline(item) == "Track your music repertoire"


def test_tagline_empty_when_no_paragraph() -> None:
    assert _tagline({"content_html": ""}) == ""
    assert _tagline({}) == ""
