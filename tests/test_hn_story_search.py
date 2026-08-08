"""옛 hackernews 행의 story id 복원 회귀 테스트.

hnrss 경로로 저장한 행은 external_id가 guid 해시고 url이 원문이라 story id가 없다.
그 행들은 댓글도 지표도 못 채워 1,394건이 통째로 건너뛰어졌다.
"""

import unittest
from unittest.mock import patch

from skim_core.crawlers.feed.hackernews import search_story_id


def _hits(*hits):
    """Algolia 응답 흉내. 첫 쿼리에만 결과가 있고 이후 쿼리는 빈손이다."""
    return [list(hits), []]


class SearchStoryIdTests(unittest.TestCase):
    def test_matches_after_stripping_a_feed_suffix(self):
        """피드와 HN이 서로 다른 꼬리표를 붙여도 같은 글이면 찾아야 한다."""
        with patch(
            "skim_core.crawlers.feed.hackernews._algolia_hits",
            side_effect=_hits(
                {"objectID": "48766209", "title": "EFF letter to FTC [pdf]", "url": ""}
            ),
        ):
            found = search_story_id("EFF letter to FTC (2 July 2026)", "")

        self.assertEqual(found, "48766209")

    def test_matches_on_url_even_when_the_title_differs(self):
        with patch(
            "skim_core.crawlers.feed.hackernews._algolia_hits",
            side_effect=_hits(
                {"objectID": "1", "title": "전혀 다른 제목", "url": "https://a.b/c/"}
            ),
        ):
            found = search_story_id("무관한 제목", "https://a.b/c")

        self.assertEqual(found, "1")

    def test_rejects_a_hit_that_matches_neither(self):
        """엉뚱한 스토리를 물면 남의 댓글과 점수가 그 행에 붙는다."""
        with patch(
            "skim_core.crawlers.feed.hackernews._algolia_hits",
            side_effect=_hits({"objectID": "9", "title": "다른 글", "url": "https://x.y/z"}),
        ):
            found = search_story_id("우리 글", "https://a.b/c")

        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
