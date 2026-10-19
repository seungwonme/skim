"""
@file session_check.py
@description SNS 로그인 세션이 지금도 그 계정으로 인식되는지 확인한다 (읽기 전용).

수집이 0건이거나 실패해도 그것만으로는 로그인이 풀렸는지 사이트가 응답 구조를
바꿨는지 알 수 없다. 2026-10-08, 10-09 threads 0건은 응답 구조 변경이었는데, 로그는
"세션 만료 또는 응답 구조 변경"이라고만 남겨 재로그인할지 코드를 고칠지 정하지 못했다.
수집 요청과 따로 "로그인한 계정이 누구인가"를 한 번 묻고, 계정이 확인되면 수집
실패를 사이트 쪽 변경으로 본다. hycli `check`(src/smoke.rs)의 판정 방식이다 (#103).

이 요청도 그 계정으로 식별된다. 수집이 잘된 날에는 보내지 않고, 수집이 실패했거나
사람이 `doctor --check-sessions`로 물을 때만 플랫폼당 1건 보낸다.

헤더, 특히 User-Agent는 그 플랫폼 크롤러와 같은 값을 쓴다. 같은 쿠키가 다른 환경에서
쓰이면 세션을 끊는 사이트가 있다. 2026-10-20 LinkedIn 세션이 크롤러와 다른
User-Agent로 보낸 시험 요청 뒤 30분 안에 끊겼다(원인 미확정).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, Optional, Tuple

import requests

from .crawlers.api.linkedin import (
    LINKEDIN_USER_AGENT,
    SESSION_REJECTED_REASONS,
    _classify_redirect,
)
from .crawlers.api.reddit import REDDIT_USER_AGENT, VERIFICATION_TITLE
from .crawlers.browser_profile import ProfileBrowserError, ProfileMissing, open_profile
from .paths import SESSIONS_DIR

PROBE_TIMEOUT = 20

VERIFIED = "verified"
SIGNED_OUT = "signed_out"
BLOCKED = "blocked"
UNREACHABLE = "unreachable"
# 응답은 왔는데 로그인 여부를 가릴 표지가 없다. 계정 확인 요청 쪽이 바뀌었을 수도 있다.
UNVERIFIED = "unverified"

# 수집이 0건이거나 실패했을 때 계정 확인 결과가 가리키는 원인. run summary에 쓴다.
CAUSES = {
    VERIFIED: "응답 구조 변경 의심",
    SIGNED_OUT: "로그인 만료",
    BLOCKED: "요청 제한",
    UNREACHABLE: "연결 실패",
    UNVERIFIED: "원인 판단 불가",
}

# 계정 전환 메뉴가 부르는 목록이다. v1.1 account/settings.json과
# verify_credentials.json은 2026-10-20에 404(code 34)였다.
X_ACCOUNT_URL = "https://x.com/i/api/1.1/account/multi/list.json"
THREADS_HOME_URL = "https://www.threads.com/"
LINKEDIN_ME_URL = "https://www.linkedin.com/voyager/api/me"
REDDIT_ME_URL = "https://www.reddit.com/api/me.json"

# 세션 파일에서 플랫폼 쿠키를 고르는 도메인. 크롤러의 _load_cookies와 같다.
_COOKIE_DOMAINS = {
    "x": ("x.com", "twitter.com"),
    "linkedin": ("linkedin",),
    "reddit": ("reddit",),
}
# 로그인한 페이지에는 viewer에 쿠키 ds_user_id와 같은 id가 있고, 풀리면 null이다.
# 같은 페이지의 USER_ID, ACCOUNT_ID는 로그인해도 "0"이라 표지로 못 쓴다 (2026-10-20).
_THREADS_VIEWER = re.compile(r'"viewer":\{[^{}]*?"id":"(\d+)"')
_X_TWID = re.compile(r"u(?:%3D|=)(\d+)")


@dataclass
class IdentityCheck:
    """플랫폼 하나의 계정 확인 결과."""

    platform: str
    status: str
    detail: str = ""
    # 확인된 계정의 핸들. 모르면 빈 문자열이다.
    account: str = ""


def _http_get(url: str, **kwargs) -> requests.Response:
    """계정 확인 요청이 나가는 유일한 자리. 테스트는 여기를 막는다."""
    return requests.get(url, timeout=PROBE_TIMEOUT, allow_redirects=False, **kwargs)


def _load_cookies(platform: str, session_dir: Path) -> Optional[Dict[str, str]]:
    """세션 파일의 플랫폼 쿠키. 파일이 없으면 None."""
    path = session_dir / f"{platform}_session.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        storage_state = json.load(f)
    domains = _COOKIE_DOMAINS[platform]
    return {
        cookie["name"]: cookie["value"]
        for cookie in storage_state.get("cookies", [])
        if any(domain in cookie.get("domain", "") for domain in domains)
    }


def _common_status(response: requests.Response) -> Optional[str]:
    """플랫폼과 상관없이 상태 코드만으로 정해지는 판정."""
    if response.status_code == 401:
        return SIGNED_OUT
    if response.status_code == 429:
        return BLOCKED
    # 999(LinkedIn 차단)처럼 표준 밖 코드는 플랫폼별 판정에 넘긴다.
    if 500 <= response.status_code < 600:
        return UNREACHABLE
    return None


def _check_x(cookies: Dict[str, str]) -> IdentityCheck:
    ct0 = cookies.get("ct0", "")
    auth_token = cookies.get("auth_token", "")
    if not ct0 or not auth_token:
        return IdentityCheck("x", SIGNED_OUT, "ct0, auth_token 쿠키 없음")
    # 크롤러(twitter-api-client)가 매 요청에 붙이는 헤더를 그대로 쓴다. 쿠키 헤더와
    # 웹앱 공개 Bearer 토큰, User-Agent가 함께 들어 있다.
    # pylint: disable-next=import-outside-toplevel
    from twitter.util import get_headers

    session = SimpleNamespace(cookies={"ct0": ct0, "auth_token": auth_token})
    response = _http_get(X_ACCOUNT_URL, headers=get_headers(session))
    status = _common_status(response)
    if status:
        return IdentityCheck("x", status, f"HTTP {response.status_code}")
    if response.status_code != 200:
        return IdentityCheck("x", UNVERIFIED, f"HTTP {response.status_code}")
    users = response.json().get("users") or []
    if not users:
        return IdentityCheck("x", UNVERIFIED, "응답에 계정 목록이 없음")
    twid = _X_TWID.search(cookies.get("twid", ""))
    me = next(
        (u for u in users if twid and str(u.get("user_id")) == twid.group(1)),
        users[0],
    )
    handle = me.get("screen_name") or ""
    return IdentityCheck("x", VERIFIED, account=f"@{handle}" if handle else "")


def threads_identity(html: str, user_id: str) -> IdentityCheck:
    """threads 홈 문서와 프로필의 ds_user_id 쿠키로 판정한다."""
    if not user_id:
        return IdentityCheck("threads", SIGNED_OUT, "ds_user_id 쿠키 없음")
    viewer = _THREADS_VIEWER.search(html)
    if viewer and viewer.group(1) == user_id:
        return IdentityCheck("threads", VERIFIED)
    if viewer:
        return IdentityCheck("threads", UNVERIFIED, "세션과 다른 계정으로 보임")
    if '"viewer":null' in html:
        return IdentityCheck("threads", SIGNED_OUT, "비로그인 페이지")
    return IdentityCheck("threads", UNVERIFIED, "페이지에서 계정 표지를 찾지 못함")


async def _threads_home() -> Tuple[str, str]:
    async with open_profile() as context:
        page = await context.new_page()
        await page.goto(THREADS_HOME_URL, wait_until="domcontentloaded", timeout=60000)
        html = await page.content()
        cookies = await context.cookies(THREADS_HOME_URL)
    user_id = next((c["value"] for c in cookies if c["name"] == "ds_user_id"), "")
    return html, user_id


def _check_threads() -> IdentityCheck:
    """크롤러와 같은 전용 프로필 브라우저로 홈을 연다. 쿠키 파일은 보지 않는다 (#105)."""
    try:
        html, user_id = asyncio.run(_threads_home())
    except ProfileMissing:
        return IdentityCheck("threads", SIGNED_OUT, "전용 프로필 없음")
    except ProfileBrowserError as exc:
        return IdentityCheck("threads", UNVERIFIED, str(exc))
    except Exception as exc:  # playwright의 시간 초과와 네트워크 오류
        first_line = (str(exc).splitlines() or [type(exc).__name__])[0]
        return IdentityCheck("threads", UNREACHABLE, first_line[:120])
    return threads_identity(html, user_id)


def _check_linkedin(cookies: Dict[str, str]) -> IdentityCheck:
    if not cookies.get("li_at") or not cookies.get("JSESSIONID"):
        return IdentityCheck("linkedin", SIGNED_OUT, "li_at, JSESSIONID 쿠키 없음")
    response = _http_get(
        LINKEDIN_ME_URL,
        headers={
            "Accept": "application/json",
            "Csrf-Token": cookies["JSESSIONID"].strip('"'),
            "User-Agent": LINKEDIN_USER_AGENT,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        cookies=cookies,
    )
    status = _common_status(response)
    if status:
        return IdentityCheck("linkedin", status, f"HTTP {response.status_code}")
    if response.status_code == 999:
        # LinkedIn이 자동 요청으로 보고 막을 때 쓰는 비표준 코드다.
        return IdentityCheck("linkedin", BLOCKED, "HTTP 999")
    if response.status_code in (301, 302, 303, 307, 308):
        reason = _classify_redirect(response)
        if reason in SESSION_REJECTED_REASONS:
            return IdentityCheck("linkedin", SIGNED_OUT, f"리다이렉트 ({reason})")
        return IdentityCheck("linkedin", UNVERIFIED, f"리다이렉트 ({reason})")
    if response.status_code == 403:
        # 크롤러도 403을 인증 거부로 다룬다 (_fetch_feed_page).
        return IdentityCheck("linkedin", SIGNED_OUT, "HTTP 403")
    if response.status_code != 200:
        return IdentityCheck("linkedin", UNVERIFIED, f"HTTP {response.status_code}")
    profile = response.json().get("miniProfile") or {}
    if not profile:
        return IdentityCheck("linkedin", UNVERIFIED, "응답에 miniProfile이 없음")
    return IdentityCheck(
        "linkedin", VERIFIED, account=profile.get("publicIdentifier") or ""
    )


def _check_reddit(cookies: Dict[str, str]) -> IdentityCheck:
    response = _http_get(
        REDDIT_ME_URL, headers={"User-Agent": REDDIT_USER_AGENT}, cookies=cookies
    )
    status = _common_status(response)
    if status:
        return IdentityCheck("reddit", status, f"HTTP {response.status_code}")
    if VERIFICATION_TITLE in response.text.lower():
        return IdentityCheck("reddit", BLOCKED, "verification 페이지")
    if response.status_code == 403:
        # 쿠키 없는 요청도 같은 차단 페이지를 받아서 만료와 차단을 가리지 못한다.
        return IdentityCheck("reddit", UNVERIFIED, "HTTP 403 (세션 만료 또는 차단)")
    if response.status_code != 200:
        return IdentityCheck("reddit", UNVERIFIED, f"HTTP {response.status_code}")
    name = (response.json().get("data") or {}).get("name") or ""
    if not name:
        return IdentityCheck("reddit", SIGNED_OUT, "비로그인 응답")
    return IdentityCheck("reddit", VERIFIED, account=f"u/{name}")


# 세션 파일의 쿠키로 확인하는 플랫폼. 전용 프로필로 옮긴 플랫폼은 _PROFILE_CHECKS에 있다.
_CHECKS: Dict[str, Callable[[Dict[str, str]], IdentityCheck]] = {
    "x": _check_x,
    "linkedin": _check_linkedin,
    "reddit": _check_reddit,
}
_PROFILE_CHECKS: Dict[str, Callable[[], IdentityCheck]] = {"threads": _check_threads}
CHECKED_PLATFORMS = ("x", "threads", "linkedin", "reddit")


def check_identity(platform: str, session_dir: Optional[Path] = None) -> IdentityCheck:
    """그 플랫폼 크롤러와 같은 경로로 계정 확인 요청을 1건 보내 판정한다.

    세션 경로는 부를 때 모듈 속성에서 읽는다. 테스트가 바꾼 경로를 따르게 하려는 것이다.
    """
    if platform in _PROFILE_CHECKS:
        return _PROFILE_CHECKS[platform]()
    cookies = _load_cookies(platform, session_dir or SESSIONS_DIR)
    if cookies is None:
        return IdentityCheck(platform, SIGNED_OUT, "세션 파일 없음")
    try:
        return _CHECKS[platform](cookies)
    except ValueError:
        # JSON이 아닌 응답. 계정 확인 요청이 바뀌었을 수 있다. requests의
        # JSONDecodeError는 RequestException이기도 해서 이 절을 먼저 둔다.
        return IdentityCheck(platform, UNVERIFIED, "응답을 JSON으로 읽지 못함")
    except requests.RequestException as exc:
        return IdentityCheck(platform, UNREACHABLE, type(exc).__name__)


def describe(check: IdentityCheck) -> str:
    """수집이 0건이거나 실패했을 때 사람이 할 일을 한 줄로 적는다."""
    detail = f" ({check.detail})" if check.detail else ""
    if check.status == VERIFIED:
        who = f" {check.account}" if check.account else ""
        return f"계정 확인됨{who}. 로그인은 정상이라 응답 구조나 쿼리 변경을 의심합니다"
    if check.status == SIGNED_OUT:
        return f"로그인 만료{detail}. 재로그인: uv run skim login {check.platform}"
    if check.status == BLOCKED:
        return f"요청 제한{detail}. 기다리거나 브라우저에서 확인하세요"
    if check.status == UNREACHABLE:
        return f"연결 실패{detail}. 네트워크를 확인하고 다시 시도하세요"
    return (
        f"원인 판단 불가{detail}. 재로그인한 뒤 "
        "`uv run skim doctor --check-sessions`로 다시 확인하세요"
    )
