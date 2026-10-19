"""전용 프로필 브라우저를 띄우는 방식이 로그인 환경과 같게 유지되는지 확인한다 (#105).

conftest가 Chrome 실행(_launch_chrome)을 막으므로, 실행 플래그는 수집 시점에 잡아 둔
원래 함수로 확인한다.
"""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from skim_core.crawlers import browser_profile
from skim_core.crawlers.browser_profile import (
    ProfileBrowserError,
    ProfileMissing,
    _launch_chrome,
    _stop,
    _wait_for_devtools,
    chrome_version,
    open_profile,
    user_agent_for,
)

# 2026-10-20 Chrome 155를 창 표시 모드로 띄웠을 때 navigator.userAgent.
HEADED_UA_155 = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)


def test_user_agent_matches_headed_chrome_of_the_same_version():
    assert user_agent_for("155.0.8059.80", "Darwin") == HEADED_UA_155


def test_unknown_version_or_system_leaves_user_agent_to_chrome():
    assert user_agent_for("", "Darwin") is None
    assert user_agent_for("155.0.8059.80", "Plan9") is None


def test_chrome_version_reads_version_output():
    done = subprocess.CompletedProcess([], 0, stdout="Google Chrome 155.0.8059.80 \n")
    with patch.object(browser_profile.subprocess, "run", return_value=done):
        assert chrome_version("/chrome") == "155.0.8059.80"


def test_chrome_version_is_empty_when_chrome_cannot_run():
    with patch.object(browser_profile.subprocess, "run", side_effect=OSError("no")):
        assert chrome_version("/chrome") == ""


def test_launch_uses_login_flags_without_automation_switches(tmp_path):
    with (
        patch.object(browser_profile, "get_chrome_path", return_value="/chrome"),
        patch.object(browser_profile, "chrome_version", return_value="155.0.8059.80"),
        patch.object(browser_profile, "find_available_port", return_value=9333),
        patch.object(browser_profile.subprocess, "Popen") as popen,
    ):
        _, port = _launch_chrome(tmp_path)

    args = popen.call_args.args[0]
    assert port == 9333
    assert f"--user-data-dir={tmp_path}" in args
    assert "--remote-debugging-port=9333" in args
    assert "--headless=new" in args
    assert f"--user-agent={HEADED_UA_155}" in args
    # Playwright 기본값이 붙이는 두 플래그는 쿠키 복호화와 webdriver 표시를 바꾼다.
    assert "--use-mock-keychain" not in args
    assert "--enable-automation" not in args


def test_missing_profile_fails_before_starting_chrome(tmp_path):
    # conftest가 Chrome 실행을 막아 두었으므로 실행하면 이 테스트가 실패한다.
    async def run():
        async with open_profile(tmp_path / "none"):
            pass  # pragma: no cover - 도달하지 않는다

    with pytest.raises(ProfileMissing):
        asyncio.run(run())


def test_default_profile_path_is_read_when_called(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_profile, "PROFILE_DIR", tmp_path / "elsewhere")

    async def run():
        async with open_profile():
            pass  # pragma: no cover - 도달하지 않는다

    with pytest.raises(ProfileMissing, match="elsewhere"):
        asyncio.run(run())


class FakeProcess:
    def __init__(self, exit_code=None, exits_on_wait=True):
        self._exit_code = exit_code
        self._exits_on_wait = exits_on_wait
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._exit_code

    def wait(self, timeout=None):
        if self._exits_on_wait or self.terminated or self.killed:
            return 0
        raise subprocess.TimeoutExpired("chrome", timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_chrome_that_exits_at_once_is_reported(monkeypatch):
    # 같은 프로필을 연 Chrome이 이미 있으면 새 프로세스는 바로 끝난다.
    monkeypatch.setattr(browser_profile, "STARTUP_TIMEOUT", 1)

    with pytest.raises(ProfileBrowserError, match="바로 종료"):
        asyncio.run(_wait_for_devtools(FakeProcess(exit_code=0), 1))


def test_stop_waits_for_a_clean_exit_after_close():
    process = FakeProcess()

    _stop(process, closing=True)

    assert not process.terminated, "정상 종료 중인 Chrome을 끊으면 쿠키가 안 남는다"


def test_stop_ends_chrome_that_never_got_the_close_command():
    process = FakeProcess(exits_on_wait=False)

    _stop(process, closing=False)

    assert process.terminated


def test_stop_ends_chrome_that_ignores_the_close_command(monkeypatch):
    monkeypatch.setattr(browser_profile, "SHUTDOWN_TIMEOUT", 0)
    process = FakeProcess(exits_on_wait=False)

    _stop(process, closing=True)

    assert process.terminated


def test_profile_dir_defaults_to_the_login_profile():
    from skim_core.crawlers.auth.cdp import CHROME_PROFILE_DIR

    # conftest가 바꾸기 전 값을 모듈 소스에서 확인한다.
    source = Path(browser_profile.__file__).read_text(encoding="utf-8")
    assert "PROFILE_DIR = CHROME_PROFILE_DIR" in source
    assert CHROME_PROFILE_DIR.name == "chrome-profile"
