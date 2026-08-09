"""지표 백필 스크립트의 중단/건너뜀 판정 회귀 테스트.

차단 감지용 연속 실패 카운터가 잘못 도는 경우가 둘 있었다.
id를 못 찾은 행을 실패로 세면 원래 못 찾는 글 5개에 전체가 멈추고,
반대로 진짜 차단됐을 때 계속 두들기면 차단이 길어진다.
"""

import importlib.util
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "backfill_feed_metrics.py"


def _load():
    spec = importlib.util.spec_from_file_location("backfill_feed_metrics", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load()

WITH_ID = "https://news.hada.io/topic?id={}"
WITHOUT_ID = "https://example.com/no-topic-id"


def _rows(count, url_template=WITH_ID):
    return [
        {"id": i, "platform": "geeknews", "url": url_template.format(i), "title": ""}
        for i in range(1, count + 1)
    ]


def _run(argv, rows, **fetch_kwargs):
    """main()을 돌리고 (fetch_metrics mock, sleep mock)을 돌려준다."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(sys, "argv", ["backfill_feed_metrics.py", *argv]))
        stack.enter_context(patch.object(backfill, "get_connection"))
        stack.enter_context(patch.object(backfill, "fetch_targets", return_value=rows))
        fetch = stack.enter_context(patch.object(backfill, "fetch_metrics", **fetch_kwargs))
        sleep = stack.enter_context(patch.object(backfill.time, "sleep"))
        backfill.main()
    return fetch, sleep


class StopConditionTests(unittest.TestCase):
    def test_unresolvable_ids_do_not_trip_the_breaker(self):
        """id를 못 찾은 행은 차단 신호가 아니다. 뒤의 정상 행까지 죽이면 안 된다."""
        rows = _rows(5, WITHOUT_ID) + _rows(3)

        fetch, _ = _run([], rows, return_value={"likes": 3, "comments": 1})

        # id 없는 5건은 요청조차 안 나가고, 그 5건 때문에 멈추지도 않는다.
        self.assertEqual(fetch.call_count, 3)

    def test_without_cooldown_it_stops_at_the_streak(self):
        """기본값은 멈추는 쪽이다. 차단 뒤에도 두들기면 차단이 길어진다."""
        fetch, _ = _run([], _rows(20), side_effect=[None] * 20)

        self.assertEqual(fetch.call_count, backfill.MAX_CONSECUTIVE_FAILURES)

    def test_cooldown_resumes_instead_of_stopping(self):
        """--cooldown을 주면 차단을 만나도 쉬었다 이어간다."""
        side_effect = [None] * backfill.MAX_CONSECUTIVE_FAILURES + [
            {"likes": 2, "comments": 0}
        ] * 15

        fetch, sleep = _run(["--cooldown", "60"], _rows(20), side_effect=side_effect)

        self.assertIn(60, [call.args[0] for call in sleep.call_args_list])
        self.assertGreater(fetch.call_count, backfill.MAX_CONSECUTIVE_FAILURES)


if __name__ == "__main__":
    unittest.main()
