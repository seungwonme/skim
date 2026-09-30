"""테스트 공통 격리.

테스트는 작업공간의 data/를 건드리지 않는다. 메인 체크아웃에서는 그 자리가 운영 DB와
운영 크롤의 상태 파일이라, `just test`나 pre-push 훅이 운영 데이터를 읽게 된다 (#32).
"""

import os
import sys

import pytest

from skim_core import db
from skim_core.crawlers.feed import geeknews
from skim_core.paths import DATA_DIR

_REAL_DATA_DIR = os.path.abspath(DATA_DIR)
_touched: list[str] = []


def _under_real_data(target) -> str | None:
    """target이 작업공간 data/이거나 그 아래면 그 경로를, 아니면 None을 돌려준다."""
    if isinstance(target, int):  # 이미 열린 fd
        return None
    try:
        raw = os.fsdecode(target)
    except TypeError:
        return None
    if raw.startswith("file:"):  # sqlite URI
        raw = raw[len("file:") :].split("?", 1)[0]
    # 상대 경로는 보지 않는다. rmtree 같은 호출은 디렉터리 fd 기준 상대 이름("data")을
    # 넘겨서 현재 디렉터리 기준으로 풀면 오탐이 난다. 코드는 경로를 DATA_DIR(절대)에서 만든다.
    if not os.path.isabs(raw):
        return None
    path = os.path.normpath(raw)
    if path == _REAL_DATA_DIR or path.startswith(_REAL_DATA_DIR + os.sep):
        return path
    return None


def _guard_real_data(event, args):
    # 읽기도 막는다. 운영 DB를 읽으면 테스트 결과가 운영 데이터에 따라 달라진다
    # (producthunt는 이미 저장된 런칭을 빼고, blogs/everyto는 실제 소스 목록을 읽었다).
    # mkdir도 본다. get_connection()은 연결 전에 상위 디렉터리부터 만든다.
    if event not in ("sqlite3.connect", "open", "os.mkdir"):
        return
    path = _under_real_data(args[0])
    if path is None:
        return
    _touched.append(path)
    raise RuntimeError(f"테스트가 작업공간 data/에 접근했습니다: {path}")


sys.addaudithook(_guard_real_data)


@pytest.fixture(autouse=True)
def _isolated_default_db(tmp_path, monkeypatch):
    """기본 DB 경로를 테스트마다 새 임시 파일로 둔다.

    예외를 삼키는 경로에서도 새지 않게, data/에 닿은 기록이 남으면 테스트를 실패시킨다.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "skim.db")
    _touched.clear()
    yield
    if _touched:
        touched = ", ".join(sorted(set(_touched)))
        pytest.fail(f"테스트가 작업공간 data/를 건드렸습니다: {touched}")


@pytest.fixture(autouse=True)
def _isolated_geeknews_topic_budget(tmp_path, monkeypatch):
    """GeekNews 토픽 요청 한도 파일을 테스트마다 새로 둔다.

    실제 data/에 쓰면 테스트가 운영 크롤의 한도를 깎는다.
    """
    monkeypatch.setattr(
        geeknews, "TOPIC_BUDGET_FILE", tmp_path / "geeknews_topic_budget.json"
    )
