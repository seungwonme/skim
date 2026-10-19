"""SNS 계정 확인으로 로그인 만료와 응답 구조 변경을 가르는 회귀 테스트 (#103).

응답 모양은 2026-10-20에 실제 세션과 쿠키 없는 요청으로 확인한 것이다.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
import requests
from requests.structures import CaseInsensitiveDict
from typer.testing import CliRunner

import skim_cli.cli as main
from skim_core import db, session_check
from skim_core.crawlers.browser_profile import ProfileBrowserError
from skim_core.db import PostCadence
from skim_core.models import Post
from skim_core.session_check import (
    BLOCKED,
    SIGNED_OUT,
    UNREACHABLE,
    UNVERIFIED,
    VERIFIED,
    IdentityCheck,
    check_identity,
    threads_identity,
)

COOKIES = {
    "x": {"ct0": "c" * 32, "auth_token": "a" * 40, "twid": "u%3D42"},
    "linkedin": {"li_at": "l", "JSESSIONID": '"ajax:1"'},
    "reddit": {"reddit_session": "r"},
}
DOMAINS = {
    "x": ".x.com",
    "linkedin": ".linkedin.com",
    "reddit": ".reddit.com",
}


def _write_session(session_dir, platform):
    session_dir.mkdir(parents=True, exist_ok=True)
    cookies = [
        {"name": name, "value": value, "domain": DOMAINS[platform]}
        for name, value in COOKIES[platform].items()
    ]
    # 다른 플랫폼 쿠키가 섞여 있어도 도메인으로 걸러야 한다.
    cookies.append({"name": "ct0", "value": "other", "domain": ".example.com"})
    (session_dir / f"{platform}_session.json").write_text(
        json.dumps({"cookies": cookies}), encoding="utf-8"
    )


def _response(status, body="", url="https://example.test/", headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = body.encode() if isinstance(body, str) else body
    response.url = url
    response.headers = CaseInsensitiveDict(headers or {})
    return response


def _check(tmp_path, monkeypatch, platform, response):
    """세션을 쓰고 응답 하나를 돌려주는 계정 확인을 한 번 돌린다."""
    _write_session(tmp_path / "sessions", platform)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(session_check, "_http_get", fake_get)
    return check_identity(platform, tmp_path / "sessions"), calls


def test_missing_session_file_is_signed_out_without_a_request(tmp_path):
    # conftest가 요청을 막아 두었으므로 요청을 보내면 이 테스트가 실패한다.
    check = check_identity("x", tmp_path / "sessions")

    assert check.status == SIGNED_OUT
    assert "세션 파일 없음" in check.detail


def test_threads_without_profile_is_signed_out_without_chrome():
    # threads는 쿠키 파일이 아니라 전용 프로필 브라우저로 확인한다 (#105). conftest가
    # 프로필 경로를 빈 폴더로 두고 Chrome 실행을 막아 두었다.
    check = check_identity("threads")

    assert check.status == SIGNED_OUT
    assert "전용 프로필 없음" in check.detail


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProfileBrowserError("Chrome이 바로 종료됐습니다"), UNVERIFIED),
        (
            RuntimeError("Page.goto: net::ERR_INTERNET_DISCONNECTED\nCall log:"),
            UNREACHABLE,
        ),
    ],
)
def test_threads_browser_failures(monkeypatch, error, expected):
    async def broken():
        raise error

    monkeypatch.setattr(session_check, "_threads_home", broken)

    check = check_identity("threads")

    assert check.status == expected
    assert "\n" not in check.detail


def test_x_account_list_matching_twid_is_verified(tmp_path, monkeypatch):
    body = json.dumps(
        {
            "users": [
                {"user_id": "1", "screen_name": "someone"},
                {"user_id": "42", "screen_name": "me"},
            ]
        }
    )

    check, calls = _check(tmp_path, monkeypatch, "x", _response(200, body))

    assert (check.status, check.account) == (VERIFIED, "@me")
    url, kwargs = calls[0]
    assert url == session_check.X_ACCOUNT_URL
    headers = kwargs["headers"]
    # 세션 파일의 다른 도메인 쿠키가 섞이지 않는다.
    assert headers["x-csrf-token"] == COOKIES["x"]["ct0"]
    assert "other" not in headers["cookie"]


def test_probes_use_the_crawler_user_agent(tmp_path, monkeypatch):
    # 같은 쿠키를 다른 User-Agent로 쓰면 세션을 끊는 사이트가 있다 (2026-10-20 LinkedIn).
    # pylint: disable-next=import-outside-toplevel
    from twitter.util import get_headers

    from skim_core.crawlers.api import linkedin, reddit

    # threads는 전용 프로필 브라우저로 확인해 User-Agent를 Chrome이 정한다.
    crawler_agents = {
        "x": get_headers(type("S", (), {"cookies": {}})())["user-agent"],
        "linkedin": linkedin.LINKEDIN_USER_AGENT,
        "reddit": reddit.REDDIT_USER_AGENT,
    }
    for platform, agent in crawler_agents.items():
        _, calls = _check(tmp_path, monkeypatch, platform, _response(200, "{}"))
        headers = {k.lower(): v for k, v in calls[0][1]["headers"].items()}
        assert headers["user-agent"] == agent, platform


def test_x_401_is_signed_out(tmp_path, monkeypatch):
    body = '{"errors":[{"message":"Could not authenticate you","code":32}]}'

    check, _ = _check(tmp_path, monkeypatch, "x", _response(401, body))

    assert check.status == SIGNED_OUT


def test_threads_viewer_matching_cookie_is_verified():
    html = '..."viewer":{"fbid":"178","id":"777"},"USER_ID":"0"...'

    assert threads_identity(html, "777").status == VERIFIED


def test_threads_null_viewer_is_signed_out():
    # 로그인이 풀려도 페이지는 열린다. USER_ID는 로그인해도 "0"이라 표지가 아니다.
    html = '..."viewer":null,"USER_ID":"0"...'

    assert threads_identity(html, "777").status == SIGNED_OUT


def test_threads_page_without_marker_is_unverified():
    # 페이지 구조가 바뀌어 표지를 못 찾으면 로그인 만료로 단정하지 않는다.
    assert threads_identity("<html/>", "777").status == UNVERIFIED


def test_threads_without_session_cookie_is_signed_out():
    assert threads_identity("<html/>", "").status == SIGNED_OUT


def test_linkedin_me_is_verified(tmp_path, monkeypatch):
    body = json.dumps({"miniProfile": {"publicIdentifier": "me-123"}})

    check, calls = _check(tmp_path, monkeypatch, "linkedin", _response(200, body))

    assert (check.status, check.account) == (VERIFIED, "me-123")
    assert calls[0][1]["headers"]["Csrf-Token"] == "ajax:1"


def test_linkedin_self_redirect_is_signed_out(tmp_path, monkeypatch):
    # 만료된 쿠키는 같은 주소로 302를 받는다.
    response = _response(
        302,
        url=session_check.LINKEDIN_ME_URL,
        headers={"location": session_check.LINKEDIN_ME_URL},
    )

    check, _ = _check(tmp_path, monkeypatch, "linkedin", response)

    assert check.status == SIGNED_OUT


def test_linkedin_999_is_blocked(tmp_path, monkeypatch):
    check, _ = _check(tmp_path, monkeypatch, "linkedin", _response(999))

    assert check.status == BLOCKED


def test_reddit_named_account_is_verified(tmp_path, monkeypatch):
    body = json.dumps({"kind": "t2", "data": {"name": "me"}})

    check, _ = _check(tmp_path, monkeypatch, "reddit", _response(200, body))

    assert (check.status, check.account) == (VERIFIED, "u/me")


def test_reddit_anonymous_answer_is_signed_out(tmp_path, monkeypatch):
    check, _ = _check(tmp_path, monkeypatch, "reddit", _response(200, "{}"))

    assert check.status == SIGNED_OUT


def test_reddit_403_does_not_claim_a_cause(tmp_path, monkeypatch):
    # 쿠키 없는 요청도 같은 차단 페이지를 받아 만료와 차단을 가리지 못한다.
    check, _ = _check(
        tmp_path, monkeypatch, "reddit", _response(403, "<html>blocked</html>")
    )

    assert check.status == UNVERIFIED


@pytest.mark.parametrize(
    ("status", "expected"), [(429, BLOCKED), (503, UNREACHABLE), (401, SIGNED_OUT)]
)
def test_common_status_codes(tmp_path, monkeypatch, status, expected):
    check, _ = _check(tmp_path, monkeypatch, "reddit", _response(status))

    assert check.status == expected


def test_connection_error_is_unreachable(tmp_path, monkeypatch):
    check, _ = _check(tmp_path, monkeypatch, "x", requests.ConnectionError("no route"))

    assert check.status == UNREACHABLE


def test_non_json_answer_is_unverified(tmp_path, monkeypatch):
    check, _ = _check(tmp_path, monkeypatch, "linkedin", _response(200, "<html>"))

    assert check.status == UNVERIFIED


ACTIVE = PostCadence(silent_days=1.0, max_gap_days=0.5, long_gaps=())


def _crawl(platforms, results, identity):
    """크롤을 끝까지 돌리고 (finish_run 인자, 출력, check_identity mock)을 돌려준다."""
    with (
        patch("skim_cli.cli.run_single_crawler", new_callable=AsyncMock) as crawler,
        patch(
            "skim_cli.cli.platform_post_cadence",
            return_value={p: ACTIVE for p in platforms},
        ),
        patch("skim_cli.cli.save_posts", return_value=1),
        patch("skim_cli.cli.save_posts_to_file"),
        patch("skim_cli.cli.save_run", return_value=7),
        patch("skim_cli.cli.init_db"),
        patch("skim_cli.cli.update_run_progress"),
        patch("skim_cli.cli.finish_run") as finish_run,
        patch("skim_cli.cli.check_identity", side_effect=identity) as checker,
        patch("skim_cli.cli.typer.echo") as echo,
    ):
        crawler.side_effect = results
        try:
            main.crawl(
                platforms=list(platforms),
                count=None,
                days=None,
                output=None,
                debug=False,
                no_content=True,
                user_id=None,
            )
        except main.typer.Exit:
            pass
    messages = " ".join(str(c.args[0]) for c in echo.call_args_list if c.args)
    return finish_run.call_args.args, messages, checker


def test_zero_result_with_live_account_points_at_the_site():
    args, messages, checker = _crawl(
        ["threads"], [[]], lambda p: IdentityCheck(p, VERIFIED, account="@me")
    )

    checker.assert_called_once_with("threads")
    assert args[1] == "degraded"
    assert "계정 확인: threads 응답 구조 변경 의심" in args[3]
    assert "응답 구조나 쿼리 변경" in messages


def test_failed_platform_with_expired_login_says_how_to_log_in():
    args, messages, _ = _crawl(
        ["x"],
        [RuntimeError("boom")],
        lambda p: IdentityCheck(p, SIGNED_OUT, "HTTP 401"),
    )

    assert args[1] == "failed"
    assert "계정 확인: x 로그인 만료" in args[3]
    assert "uv run skim login x" in messages


def test_collected_and_feed_platforms_send_no_account_request():
    post = Post(
        platform="threads",
        author="a",
        content="c",
        url="https://t/1",
        timestamp="2026-10-20T00:00:00+09:00",
    )

    args, _, checker = _crawl(
        ["threads", "hackernews"],
        [[post], []],
        lambda p: IdentityCheck(p, VERIFIED),
    )

    checker.assert_not_called()
    assert "계정 확인" not in args[3]


def test_identity_failure_does_not_lose_the_run_record():
    def explode(_platform):
        raise RuntimeError("probe broke")

    args, messages, _ = _crawl(["reddit"], [[]], explode)

    assert args[1] == "degraded"
    assert "계정 확인 실패" in messages


def _doctor(db_path, *args):
    return CliRunner().invoke(
        main.app, ["doctor", "--db", str(db_path), "--emit", "json", *args]
    )


def test_doctor_checks_sessions_only_when_asked(tmp_path, monkeypatch):
    db_path = tmp_path / "skim.db"
    db.init_db(db_path)
    monkeypatch.setattr(main, "_playwright_status", lambda: {"ok": True, "detail": ""})
    monkeypatch.setattr(main, "probe_user_agent", lambda *a, **k: None)

    # 기본 doctor는 계정 요청을 보내지 않는다. 보내면 conftest가 실패시킨다.
    plain = json.loads(_doctor(db_path).stdout)
    assert "session_checks" not in plain

    _write_session(tmp_path / "sessions", "reddit")
    monkeypatch.setattr(
        session_check,
        "_http_get",
        lambda url, **kw: _response(200, json.dumps({"data": {"name": "me"}})),
    )
    report = json.loads(_doctor(db_path, "--check-sessions").stdout)

    by_platform = {c["platform"]: c for c in report["session_checks"]}
    assert by_platform["reddit"]["status"] == VERIFIED
    # doctor의 세션 경로는 DB 옆 sessions/다. 파일이 없는 플랫폼은 요청 없이 만료다.
    assert by_platform["x"]["status"] == SIGNED_OUT
    assert any(w.startswith("x 세션:") for w in report["warnings"])
    assert not any(w.startswith("reddit 세션:") for w in report["warnings"])
