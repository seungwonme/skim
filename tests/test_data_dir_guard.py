"""conftest의 data/ 감시가 실제로 막는지 확인한다 (#32).

감시가 조용히 꺼지면(예: DATA_DIR 계산이 바뀌면) 운영 DB를 읽는 테스트가 다시 통과한다.
"""

import os
import sqlite3

import conftest
import pytest

from skim_core import db


@pytest.fixture(name="expect_touch")
def _expect_touch():
    """일부러 data/에 닿는 테스트다. teardown 검사 전에 기록을 지운다."""
    yield
    conftest._touched.clear()


def test_default_db_path_is_per_test_tmp(tmp_path):
    assert db.DB_PATH == tmp_path / "skim.db"


@pytest.mark.usefixtures("expect_touch")
def test_connect_to_workspace_db_is_blocked():
    real_db = os.path.join(conftest._REAL_DATA_DIR, "skim.db")
    with pytest.raises(RuntimeError, match="data/"):
        sqlite3.connect(real_db)


@pytest.mark.usefixtures("expect_touch")
def test_write_under_workspace_data_is_blocked():
    probe = os.path.join(conftest._REAL_DATA_DIR, "guard-probe.json")
    with pytest.raises(RuntimeError, match="data/"):
        with open(probe, "w", encoding="utf-8"):
            pass
    assert not os.path.exists(probe)


@pytest.mark.usefixtures("expect_touch")
def test_mkdir_of_workspace_data_is_blocked():
    with pytest.raises(RuntimeError, match="data/"):
        os.makedirs(os.path.join(conftest._REAL_DATA_DIR, "guard-probe"))
