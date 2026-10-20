"""
@file browser_profile.py
@description `skim login`이 로그인해 둔 전용 Chrome 프로필로 수집용 브라우저를 띄운다 (#105).

쿠키를 파일로 꺼내 다른 환경에 주입하지 않는다. 같은 세션이 로그인한 브라우저 하나에서만
쓰이고, 브라우저가 갱신한 쿠키는 프로필에 그대로 남는다.

Playwright가 프로필을 직접 열면(launch_persistent_context) macOS에서 `--use-mock-keychain`이
붙어 실제 키체인으로 암호화된 쿠키를 읽지 못하고, `navigator.webdriver`도 켜진다. 그래서
`skim login`과 같은 플래그로 Chrome을 직접 띄우고 CDP로 붙는다. 이렇게 띄우면
`navigator.webdriver`는 false다(2026-10-20 실측).

헤드리스 Chrome은 User-Agent에 `HeadlessChrome`을 넣는다. 설치된 Chrome 버전으로 창을 띄운
Chrome과 같은 User-Agent를 만들어 넘긴다. 버전을 코드에 박으면 Chrome이 올라갈 때 브랜드
정보(sec-ch-ua)와 User-Agent의 버전이 어긋난다.
"""

from __future__ import annotations

import asyncio
import contextlib
import platform
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Tuple

from .auth.cdp import CHROME_PROFILE_DIR, find_available_port, get_chrome_path

# 부를 때 이 속성에서 읽는다. 테스트는 이 값을 임시 폴더로 바꾼다.
PROFILE_DIR = CHROME_PROFILE_DIR
# DevTools 포트가 열리기까지 기다리는 시간(초).
STARTUP_TIMEOUT = 30
# 정상 종료 명령 뒤 Chrome 프로세스가 끝나기를 기다리는 시간(초). 이 사이에 쿠키를 디스크에 쓴다.
SHUTDOWN_TIMEOUT = 15
WINDOW_SIZE = "1440,900"

# User-Agent 축소 정책으로 운영체제 부분은 고정값이다.
_UA_PLATFORMS = {
    "Darwin": "Macintosh; Intel Mac OS X 10_15_7",
    "Windows": "Windows NT 10.0; Win64; x64",
    "Linux": "X11; Linux x86_64",
}


class ProfileBrowserError(RuntimeError):
    """전용 프로필 브라우저를 띄우지 못했다."""


class ProfileMissing(ProfileBrowserError):
    """전용 프로필이 없다. 아직 `skim login`을 한 적이 없다."""


def user_agent_for(version: str, system: Optional[str] = None) -> Optional[str]:
    """창을 띄운 같은 버전의 Chrome이 보내는 User-Agent. 모르는 형식이면 None."""
    major = re.match(r"(\d+)\.", version or "")
    token = _UA_PLATFORMS.get(system or platform.system())
    if not major or not token:
        return None
    return (
        f"Mozilla/5.0 ({token}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major.group(1)}.0.0.0 Safari/537.36"
    )


def chrome_version(chrome_path: str) -> str:
    """`chrome --version` 출력의 버전 번호. 못 읽으면 빈 문자열."""
    try:
        result = subprocess.run(
            [chrome_path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    found = re.search(r"\d+\.\d+\.\d+\.\d+", result.stdout)
    return found.group(0) if found else ""


def _launch_chrome(profile: Path) -> Tuple[subprocess.Popen, int]:
    """`skim login`과 같은 플래그로 헤드리스 Chrome을 띄운다. 실제 실행은 여기서만 한다."""
    chrome = get_chrome_path()
    if not chrome:
        raise ProfileBrowserError("Chrome이 설치되어 있지 않습니다")
    port = find_available_port()
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        "--remote-allow-origins=*",
        "--headless=new",
        f"--window-size={WINDOW_SIZE}",
    ]
    agent = user_agent_for(chrome_version(chrome))
    if agent:
        args.append(f"--user-agent={agent}")
    args.append("about:blank")
    # PIPE로 열면 아무도 안 읽는 버퍼가 차서 Chrome이 멈출 수 있다.
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return process, port


async def _wait_for_devtools(process: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            # 같은 프로필을 연 Chrome이 이미 있으면 새 프로세스는 그쪽에 창을 넘기고 끝난다.
            raise ProfileBrowserError(
                "Chrome이 바로 종료됐습니다. 전용 프로필이 다른 Chrome(예: `skim login` 창)에서 "
                "열려 있으면 닫고 다시 시도하세요"
            )
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ):
                return
        except OSError:
            await asyncio.sleep(0.5)
    raise ProfileBrowserError(
        f"Chrome DevTools가 {STARTUP_TIMEOUT}초 안에 열리지 않았습니다"
    )


def stop_chrome(process: subprocess.Popen, closing: bool) -> None:
    """정상 종료 명령을 보냈으면 끝나기를 기다리고, 안 보냈거나 안 끝나면 끊는다."""
    if closing:
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT)
            return
        except subprocess.TimeoutExpired:
            pass
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@contextlib.asynccontextmanager
async def open_profile(profile_dir: Optional[Path] = None) -> AsyncIterator[Any]:
    """전용 프로필로 헤드리스 Chrome을 띄워 그 브라우저의 기본 컨텍스트를 내준다.

    블록이 끝나면 `Browser.close`로 Chrome을 정상 종료한다. 프로세스를 바로 죽이면
    브라우저가 갱신한 쿠키가 디스크에 남지 않는다.
    """
    # pylint: disable-next=import-outside-toplevel
    from playwright.async_api import async_playwright

    profile = profile_dir or PROFILE_DIR
    if not (profile / "Local State").exists():
        raise ProfileMissing(
            f"전용 Chrome 프로필이 없습니다({profile}). 먼저 로그인하세요: "
            "uv run skim login <platform>"
        )
    process, port = _launch_chrome(profile)
    closing = False
    try:
        await _wait_for_devtools(process, port)
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            try:
                yield browser.contexts[0]
            finally:
                # Chrome이 닫히며 연결이 끊겨 send가 예외로 끝날 수 있다. 종료는 stop_chrome이 확인한다.
                with contextlib.suppress(Exception):
                    session = await browser.new_browser_cdp_session()
                    closing = True
                    await session.send("Browser.close")
    finally:
        stop_chrome(process, closing)
