"""
@file test_papers_abstract_fallback.py
@description arXiv/HF 논문의 HTML 버전이 없을 때(대개 404) abstract를 본문 폴백으로
              채우고 enrichment_method=failed 마커로 재시도 가능하게 두는지 회귀 테스트.
"""

from __future__ import annotations

from skim_core import enrichment
from skim_core.enrichment import _paper_pdf_url


def test_paper_pdf_url_derivation() -> None:
    assert _paper_pdf_url("https://arxiv.org/abs/2607.01188v1") == "https://arxiv.org/pdf/2607.01188v1"
    assert _paper_pdf_url("https://huggingface.co/papers/2606.00248") == "https://arxiv.org/pdf/2606.00248"
    assert _paper_pdf_url("https://example.com/x") is None


def test_pdf_used_when_html_missing(monkeypatch) -> None:
    # HTML은 실패하지만 PDF 추출이 성공하면 전문(pdf)으로 확정, failed 마커 없음.
    monkeypatch.setattr(enrichment, "defuddle", lambda *a, **k: None)
    monkeypatch.setattr(
        enrichment,
        "extract_pdf_text",
        lambda *a, **k: {"content_markdown": "full pdf body " * 100, "word_count": 300},
    )
    items = [
        {
            "platform": "arxiv",
            "title": "P",
            "url": "https://arxiv.org/abs/2607.01188v1",
            "summary": "s",
            "abstract": "a",
        }
    ]
    enrichment.enrich_papers_with_content(items)
    item = items[0]
    assert item["enrichment_method"] == "pdf"
    assert item["word_count"] == 300
    assert item["content_markdown"].startswith("full pdf body")


def test_abstract_fallback_when_no_html(monkeypatch) -> None:
    # HTML(defuddle)과 PDF 추출이 모두 실패하는 상황 -> abstract 폴백.
    monkeypatch.setattr(enrichment, "defuddle", lambda *a, **k: None)
    monkeypatch.setattr(enrichment, "extract_pdf_text", lambda *a, **k: None)

    items = [
        {
            "platform": "arxiv",
            "title": "Some Paper",
            "url": "https://arxiv.org/abs/2607.01188v1",
            "summary": "s" * 500,
            "abstract": "We propose a method. " * 20,
        }
    ]
    enrichment.enrich_papers_with_content(items)
    item = items[0]

    assert item["content_markdown"].startswith("We propose a method.")
    assert item["word_count"] > 0
    # failed 마커가 있어야 다음 크롤에서 HTML이 생기면 upsert가 덮어쓴다.
    assert item["enrichment_method"] == "failed"


def test_marker_set_even_without_abstract(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "defuddle", lambda *a, **k: None)
    monkeypatch.setattr(enrichment, "extract_pdf_text", lambda *a, **k: None)

    items = [
        {
            "platform": "huggingface",
            "title": "No Abstract Paper",
            "url": "https://huggingface.co/papers/2607.00248",
            "summary": "",
            "abstract": "",
        }
    ]
    enrichment.enrich_papers_with_content(items)
    item = items[0]

    assert item["content_markdown"] == ""
    assert item["word_count"] == 0
    assert item["enrichment_method"] == "failed"
