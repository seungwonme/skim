"""arXiv API가 406으로 거절할 때의 동작 검증.

export.arxiv.org는 2026-09 중순부터 406을 섞어 돌려준다. 9/24~9/29 여섯 회차 중
다섯 번이 0건이었는데, 크롤러가 거절을 빈 결과로 바꿔서 run에는 degraded(0건
회귀)로만 남았다. 주말의 정상 0건과 구분되지 않아 6일 동안 실패로 드러나지 않았다.
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

from skim_core.crawlers.feed import arxiv
from skim_core.crawlers.feed.arxiv import ArxivCrawler
from skim_core.feed_config import ARXIV_CATEGORIES

# export.arxiv.org가 실제로 보내는 응답에서 한 건만 남겼다 (2026-09-29).
API_FEED = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <id>https://arxiv.org/api/HQttLwSI9ar2vqfmj4lJ7u3JUas</id>
  <title>arXiv Query: search_query=cat:cs.AI&amp;id_list=&amp;start=0&amp;max_results=1</title>
  <updated>2026-09-29T11:28:43Z</updated>
  <opensearch:totalResults>202786</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2609.35770v1</id>
    <title>FurE: Efficient Instance-Specific 3D Fur Reconstruction without Animal-Fur Datasets</title>
    <updated>2026-09-28T17:59:58Z</updated>
    <link href="https://arxiv.org/abs/2609.35770v1" rel="alternate" type="text/html"/>
    <summary>Realistic and editable animal fur reconstruction from multi-view images is challenging.</summary>
    <category term="cs.CV" scheme="http://arxiv.org/schemas/atom"/>
    <published>2026-09-28T17:59:58Z</published>
    <author><name>Toshi</name></author>
  </entry>
</feed>
"""

# rss.arxiv.org/atom/cs.AI 실제 응답에서 공지 유형별로 한 건씩 남겼다 (2026-09-29).
# cross에는 교차 등록이 새로 붙은 옛 논문(2023년 id)도 섞여 온다.
RSS_FEED = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns="http://www.w3.org/2005/Atom" xml:lang="en-us">
  <id>http://rss.arxiv.org/atom/cs.AI</id>
  <title>cs.AI updates on arXiv.org</title>
  <updated>2026-09-29T05:15:23.205359+00:00</updated>
  <entry>
    <id>oai:arXiv.org:2609.31763v1</id>
    <title>SMARtCARE: Privacy-Preserving Agentic AI Systems for Bounded-Autonomy Clinical Decision Support</title>
    <updated>2026-09-29T05:15:23.205445+00:00</updated>
    <link href="https://arxiv.org/abs/2609.31763" rel="alternate" type="text/html"/>
    <summary>arXiv:2609.31763v1 Announce Type: new
Abstract: Long-context clinical AI systems can miss relevant patient history.</summary>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <published>2026-09-29T00:00:00-04:00</published>
    <arxiv:announce_type>new</arxiv:announce_type>
    <dc:rights>http://creativecommons.org/licenses/by/4.0/</dc:rights>
    <dc:creator>Srini Ramaswamy, Deveeshree Nayak</dc:creator>
  </entry>
  <entry>
    <id>oai:arXiv.org:2301.02225v1</id>
    <title>$l_{1-2}$ GLasso: Regularized Multi-task Graphical Lasso</title>
    <updated>2026-09-29T05:15:23.253858+00:00</updated>
    <link href="https://arxiv.org/abs/2301.02225" rel="alternate" type="text/html"/>
    <summary>arXiv:2301.02225v1 Announce Type: cross
Abstract: A critical problem in genetics is to discover how gene expression is regulated.</summary>
    <category term="stat.ML"/>
    <category term="cs.AI"/>
    <published>2026-09-29T00:00:00-04:00</published>
    <arxiv:announce_type>cross</arxiv:announce_type>
    <dc:rights>http://creativecommons.org/licenses/by/4.0/</dc:rights>
    <dc:creator>Someone Else</dc:creator>
  </entry>
  <entry>
    <id>oai:arXiv.org:2306.13961v3</id>
    <title>Categorical Approach to Conflict Resolution</title>
    <updated>2026-09-29T05:15:23.282015+00:00</updated>
    <link href="https://arxiv.org/abs/2306.13961" rel="alternate" type="text/html"/>
    <summary>arXiv:2306.13961v3 Announce Type: replace
Abstract: This note is a substantially revised version.</summary>
    <category term="cs.AI"/>
    <published>2026-09-29T00:00:00-04:00</published>
    <arxiv:announce_type>replace</arxiv:announce_type>
    <dc:creator>Old Author</dc:creator>
  </entry>
</feed>
"""


def _resp(status, content=b"", headers=None):
    resp = Mock(status_code=status, content=content, headers=headers or {})
    resp.ok = status < 400
    resp.text = content.decode("utf-8", "replace")
    if status >= 400:
        from requests import HTTPError  # pylint: disable=import-outside-toplevel

        resp.raise_for_status.side_effect = HTTPError(f"{status} Client Error")
    return resp


class _ThrottleReset(unittest.TestCase):
    def setUp(self):
        arxiv._last_request = 0.0  # pylint: disable=protected-access
        self.addCleanup(setattr, arxiv, "_last_request", 0.0)


class FetchCategoryTests(_ThrottleReset):
    def test_406_carrying_a_feed_is_used(self):
        # 406 응답에 멀쩡한 Atom 피드가 실려 오는 경우가 있다. 버리면 그날 분이 유실된다.
        with (
            patch.object(arxiv._SESSION, "get", return_value=_resp(406, API_FEED)),
            patch.object(arxiv.time, "sleep"),
            patch.object(arxiv.typer, "echo"),
        ):
            feed = arxiv._fetch_category("cs.AI")

        self.assertIsNotNone(feed)
        self.assertEqual(len(feed.entries), 1)

    def test_bare_406_is_logged_with_diagnostics(self):
        # 사유가 로그에 없어서 여섯 회차 동안 원인을 추정만 했다.
        resp = _resp(406, b"", {"server": "Google Frontend"})
        with (
            patch.object(arxiv._SESSION, "get", return_value=resp),
            patch.object(arxiv.time, "sleep"),
            patch.object(arxiv.typer, "echo") as echo,
        ):
            self.assertIsNone(arxiv._fetch_category("cs.AI"))

        logged = echo.call_args[0][0]
        self.assertIn("HTTP 406", logged)
        self.assertIn("Google Frontend", logged)

    def test_406_is_retried_by_the_session(self):
        retry = arxiv._SESSION.get_adapter("https://export.arxiv.org").max_retries
        self.assertIn(406, retry.status_forcelist)
        self.assertGreaterEqual(retry.total, 3)

    def test_requests_are_spaced_across_categories(self):
        # 실패한 분야가 곧바로 다음 분야로 넘어가 네 분야를 1초 안에 몰아쳤다.
        with (
            patch.object(arxiv._SESSION, "get", return_value=_resp(200, API_FEED)),
            patch.object(arxiv.time, "sleep") as sleep,
        ):
            arxiv._fetch_category("cs.AI")
            arxiv._fetch_category("cs.CL")

        self.assertEqual(sleep.call_count, 1)
        self.assertLessEqual(
            sleep.call_args[0][0], arxiv.ARXIV_REQUEST_INTERVAL_SECONDS
        )


class RssFallbackTests(_ThrottleReset):
    def _entries(self, count=50):
        with (
            patch.object(arxiv._SESSION, "get", return_value=_resp(200, RSS_FEED)),
            patch.object(arxiv.time, "sleep"),
        ):
            return arxiv._fetch_rss_entries("cs.AI", count)

    def test_keeps_new_submissions_and_drops_replacements(self):
        ids = [e["paper_id"] for e in self._entries()]
        # replace는 옛 논문의 새 버전이라 API의 최신 제출 목록에는 나오지 않는다.
        self.assertEqual(ids, ["2609.31763v1", "2301.02225v1"])

    def test_link_matches_the_api_form(self):
        # 링크가 API와 다르면 같은 논문이 두 행으로 갈라진다.
        first = self._entries()[0]
        self.assertEqual(first["link"], "https://arxiv.org/abs/2609.31763v1")

    def test_summary_is_the_abstract_only(self):
        first = self._entries()[0]
        self.assertTrue(first["summary"].startswith("Long-context clinical"))

    def test_newest_ids_win_the_cap(self):
        # RSS 발행일은 공지일 하나라 제출순 정렬을 id로 대신한다.
        self.assertEqual([e["paper_id"] for e in self._entries(1)], ["2609.31763v1"])

    def test_refused_rss_returns_none(self):
        with (
            patch.object(arxiv._SESSION, "get", return_value=_resp(503)),
            patch.object(arxiv.time, "sleep"),
            patch.object(arxiv.typer, "echo"),
        ):
            self.assertIsNone(arxiv._fetch_rss_entries("cs.AI", 50))


class CrawlFallbackTests(_ThrottleReset):
    SINCE = datetime(2026, 9, 25, tzinfo=timezone.utc)

    def _crawl(self, api, rss):
        """api/rss: {category: 반환값}. 없는 분야는 빈 피드/빈 목록."""
        rss_calls = []

        def fake_fetch(category, start=0):
            if category in api:
                return api[category]
            feed = MagicMock()
            feed.entries = []
            return feed

        def fake_rss(category, count):
            rss_calls.append(category)
            return rss.get(category, [])

        with (
            patch.object(arxiv, "_fetch_category", fake_fetch),
            patch.object(arxiv, "_fetch_rss_entries", fake_rss),
            patch.object(arxiv.typer, "echo"),
        ):
            posts = asyncio.run(
                ArxivCrawler().crawl(since=self.SINCE, no_content=True, count=50)
            )
        return posts, rss_calls

    def _rss_entry(self, paper_id):
        return {
            "title": "t",
            "link": f"https://arxiv.org/abs/{paper_id}",
            "published": "2026-09-29T00:00:00-04:00",
            "authors": [{"name": "A"}],
            "summary": "abstract",
            "arxiv_feed": "rss",
            "paper_id": paper_id,
        }

    def test_refused_category_falls_back_to_rss(self):
        posts, rss_calls = self._crawl(
            api={"cs.AI": None}, rss={"cs.AI": [self._rss_entry("2609.31763v1")]}
        )

        self.assertEqual(rss_calls, ["cs.AI"])
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].url, "https://arxiv.org/abs/2609.31763v1")
        # 어느 경로로 받았는지 남겨야 나중에 폴백 빈도를 셀 수 있다.
        self.assertEqual(posts[0].model_extra.get("arxiv_feed"), "rss")

    def test_rss_is_not_touched_when_the_api_answers(self):
        _, rss_calls = self._crawl(api={}, rss={})
        self.assertEqual(rss_calls, [])

    def test_total_refusal_raises_instead_of_returning_zero(self):
        refused = {category: None for category in ARXIV_CATEGORIES}
        down = {category: None for category in ARXIV_CATEGORIES}
        with self.assertRaises(RuntimeError):
            self._crawl(api=refused, rss=down)

    def test_partial_refusal_keeps_the_rest(self):
        now = datetime.now(timezone.utc)
        feed = MagicMock()
        feed.entries = [
            {
                "title": "kept",
                "link": "https://arxiv.org/abs/2609.40000v1",
                "published": (now - timedelta(hours=1)).isoformat(),
                "summary": "s",
                "authors": [{"name": "B"}],
            }
        ]
        posts, _ = self._crawl(api={"cs.AI": None, "cs.CL": feed}, rss={"cs.AI": None})

        self.assertEqual([p.title for p in posts], ["kept"])


if __name__ == "__main__":
    unittest.main()
