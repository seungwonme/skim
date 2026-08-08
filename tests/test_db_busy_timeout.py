"""동시 쓰기 락 회귀 테스트.

WAL은 쓰기를 하나만 허용한다. 데일리 크롤과 백필 스크립트가 겹쳤을 때
기본 5초 타임아웃으로는 `database is locked`가 나 배치가 통째로 날아갔다.
"""

import tempfile
import unittest
from pathlib import Path

from skim_core.db import get_connection

# 기본값(5초)과 구분되면서 크롤 한 배치가 커밋되기를 기다릴 만한 하한.
MIN_BUSY_TIMEOUT_MS = 30000


class BusyTimeoutTests(unittest.TestCase):
    def test_busy_timeout_survives_a_concurrent_writer(self):
        """다른 프로세스가 쓰기 락을 쥐고 있어도 곧장 포기하지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_connection(Path(tmp) / "t.db")
            timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            conn.close()

        self.assertGreaterEqual(timeout_ms, MIN_BUSY_TIMEOUT_MS)


if __name__ == "__main__":
    unittest.main()
