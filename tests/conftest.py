"""테스트 공통 격리."""

import pytest

from skim_core.crawlers.feed import geeknews


@pytest.fixture(autouse=True)
def _isolated_geeknews_topic_budget(tmp_path, monkeypatch):
    """GeekNews 토픽 요청 한도 파일을 테스트마다 새로 둔다.

    실제 data/에 쓰면 테스트가 운영 크롤의 한도를 깎는다.
    """
    monkeypatch.setattr(
        geeknews, "TOPIC_BUDGET_FILE", tmp_path / "geeknews_topic_budget.json"
    )
