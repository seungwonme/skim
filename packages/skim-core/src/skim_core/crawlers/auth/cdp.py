"""
@file cdp_auth.py
@description CDP 기반 범용 로그인 모듈

Chrome DevTools Protocol을 사용하여 SNS 플랫폼 로그인 쿠키를 추출합니다.
지원 플랫폼: Threads, X (Twitter), LinkedIn, Reddit

플로우:
1. Chrome을 debugging port와 함께 실행
2. 로그인 페이지로 이동
3. 사용자가 수동 로그인
4. Network.getCookies로 쿠키 추출
5. data/sessions/{platform}_session.json에 저장
6. Chrome 종료
"""

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests
import typer

from ...paths import SESSIONS_DIR

CHROME_PROFILE_DIR = Path.home() / ".skim" / "chrome-profile"
CDP_DEFAULT_PORT = 9222
LOGIN_TIMEOUT = 300  # 5분

# 플랫폼별 설정
PLATFORM_CONFIG = {
    "threads": {
        "login_url": "https://www.threads.net/login",
        "session_path": SESSIONS_DIR / "threads_session.json",
        "cookie_domains": ["threads", "instagram"],
        "display_name": "Threads",
        "identifier_selectors": [
            'input[name="username"]',
            'input[name="text"]',
            'input[autocomplete="username"]',
            'input[type="text"]',
            'input[type="email"]',
        ],
        "password_selectors": ['input[type="password"]'],
        "advance_selectors": [],
        "advance_texts": [],
        "submit_selectors": ['button[type="submit"]', 'button[role="button"]'],
        "submit_texts": ["log in", "login", "로그인"],
    },
    "x": {
        "login_url": "https://x.com/i/flow/login",
        "session_path": SESSIONS_DIR / "x_session.json",
        "cookie_domains": ["x.com", "twitter.com"],
        "display_name": "X (Twitter)",
        "identifier_selectors": [
            'input[autocomplete="username"]',
            'input[name="text"]',
            'input[data-testid="ocfEnterTextTextInput"]',
        ],
        "password_selectors": ['input[name="password"]', 'input[type="password"]'],
        "advance_selectors": ['button[role="button"]', 'div[role="button"]'],
        "advance_texts": ["next", "다음"],
        "submit_selectors": [
            'button[data-testid="LoginForm_Login_Button"]',
            'button[role="button"]',
        ],
        "submit_texts": ["log in", "로그인", "sign in"],
    },
    "linkedin": {
        "login_url": "https://www.linkedin.com/login",
        "session_path": SESSIONS_DIR / "linkedin_session.json",
        "cookie_domains": ["linkedin.com"],
        "display_name": "LinkedIn",
        "identifier_selectors": [
            "#username",
            'input[name="session_key"]',
            'input[autocomplete="username"]',
            'input[type="email"]',
            'input[name="username"]',
        ],
        "password_selectors": [
            "#password",
            'input[name="session_password"]',
            'input[type="password"]',
        ],
        "advance_selectors": [],
        "advance_texts": [],
        "submit_selectors": ['button[type="submit"]', 'button[aria-label*="Sign in"]'],
        "submit_texts": ["sign in", "로그인", "log in"],
    },
    "reddit": {
        "login_url": "https://www.reddit.com/login/",
        "session_path": SESSIONS_DIR / "reddit_session.json",
        "cookie_domains": ["reddit.com"],
        "display_name": "Reddit",
        "identifier_selectors": [
            'input[name="username"]',
            'input[autocomplete="username"]',
            'input[name="loginUsername"]',
        ],
        "password_selectors": [
            'input[name="password"]',
            'input[type="password"]',
            'input[name="loginPassword"]',
        ],
        "advance_selectors": [],
        "advance_texts": [],
        "submit_selectors": ['button[type="submit"]', "form button"],
        "submit_texts": ["log in", "로그인", "continue"],
    },
}


def load_login_credentials_from_env() -> Optional[tuple[str, str]]:
    """Tauri backend가 전달한 저장된 로그인 자격 증명을 읽습니다."""
    login_identifier = os.getenv("SKIM_LOGIN_IDENTIFIER")
    password = os.getenv("SKIM_LOGIN_PASSWORD")

    if not login_identifier or not password:
        return None

    return login_identifier, password


def build_autofill_expression(platform_name: str, login_identifier: str, password: str) -> str:
    """로그인 폼 자동 입력용 Runtime.evaluate 스크립트를 생성합니다."""
    config = PLATFORM_CONFIG[platform_name]
    payload = json.dumps(
        {
            "loginIdentifier": login_identifier,
            "password": password,
            "identifierSelectors": config["identifier_selectors"],
            "passwordSelectors": config["password_selectors"],
            "advanceSelectors": config["advance_selectors"],
            "advanceTexts": config["advance_texts"],
            "submitSelectors": config["submit_selectors"],
            "submitTexts": config["submit_texts"],
        },
        ensure_ascii=False,
    )

    return f"""
(async () => {{
  const payload = {payload};
  const state = window.__skimAutoLoginState || (window.__skimAutoLoginState = {{ lastActionAt: 0 }});
  const now = Date.now();
  const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  const normalize = (value) => (value || "").trim().toLowerCase();
  const isVisible = (element) => {{
    if (!element) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }};
  const isInteractable = (element) => isVisible(element) && !element.disabled && element.getAttribute("aria-disabled") !== "true";
  const findFirst = (selectors) => {{
    for (const selector of selectors) {{
      const element = Array.from(document.querySelectorAll(selector)).find(isVisible);
      if (element) return element;
    }}
    return null;
  }};
  const setNativeValue = (element, value) => {{
    if (!element) return false;
    const prototype = element.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor && descriptor.set) {{
      descriptor.set.call(element, value);
    }} else {{
      element.value = value;
    }}
    element.dispatchEvent(new Event("input", {{ bubbles: true }}));
    element.dispatchEvent(new Event("change", {{ bubbles: true }}));
    return true;
  }};
  const findAction = (selectors, texts) => {{
    for (const selector of selectors) {{
      const element = Array.from(document.querySelectorAll(selector)).find((candidate) => {{
        if (!isInteractable(candidate)) return false;
        if (!texts.length) return true;
        const text = normalize(
          candidate.innerText ||
          candidate.textContent ||
          candidate.getAttribute("aria-label") ||
          candidate.value
        );
        return texts.some((target) => text.includes(target));
      }});
      if (element) return element;
    }}
    return null;
  }};
  const triggerClick = (element) => {{
    if (!element) return false;
    element.focus?.();
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {{
      element.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
    }}
    if (typeof element.click === "function") {{
      element.click();
    }}
    return true;
  }};
  const submitForm = (action, fallbackField) => {{
    const form = action?.form || fallbackField?.form || action?.closest?.("form") || fallbackField?.closest?.("form");
    if (action && triggerClick(action)) {{
      return true;
    }}
    if (form && typeof form.requestSubmit === "function") {{
      form.requestSubmit(action || undefined);
      return true;
    }}
    if (fallbackField) {{
      for (const type of ["keydown", "keypress", "keyup"]) {{
        fallbackField.dispatchEvent(new KeyboardEvent(type, {{
          key: "Enter",
          code: "Enter",
          keyCode: 13,
          which: 13,
          bubbles: true,
        }}));
      }}
      return true;
    }}
    return false;
  }};

  const identifierField = findFirst(payload.identifierSelectors);
  const passwordField = findFirst(payload.passwordSelectors);
  const result = {{
    attempted: true,
    identifierFilled: false,
    passwordFilled: false,
    actionClicked: false,
  }};

  if (identifierField && payload.loginIdentifier) {{
    if (identifierField.value !== payload.loginIdentifier) {{
      setNativeValue(identifierField, payload.loginIdentifier);
    }}
    result.identifierFilled = identifierField.value === payload.loginIdentifier;
  }}

  if (passwordField && payload.password) {{
    if (passwordField.value !== payload.password) {{
      setNativeValue(passwordField, payload.password);
    }}
    result.passwordFilled = passwordField.value === payload.password;
  }}

  if (result.identifierFilled || result.passwordFilled) {{
    await wait(180);
  }}

  const action = !passwordField && payload.advanceSelectors.length
    ? findAction(payload.advanceSelectors, payload.advanceTexts)
    : findAction(payload.submitSelectors, payload.submitTexts);

  if (action && now - state.lastActionAt > 1500) {{
    result.actionClicked = submitForm(action, passwordField || identifierField);
    state.lastActionAt = now;
  }} else if (!action && passwordField && now - state.lastActionAt > 1500) {{
    result.actionClicked = submitForm(null, passwordField);
    state.lastActionAt = now;
  }}

  return result;
}})();
""".strip()


def attempt_login_autofill(
    ws_url: str,
    platform_name: str,
    login_identifier: Optional[str],
    password: Optional[str],
) -> dict:
    """저장된 자격 증명으로 로그인 폼 자동 입력을 시도합니다."""
    if not login_identifier or not password:
        return {
            "attempted": False,
            "identifierFilled": False,
            "passwordFilled": False,
            "actionClicked": False,
        }

    expression = build_autofill_expression(platform_name, login_identifier, password)
    result = execute_cdp_command(
        ws_url,
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
    )

    value = result.get("result", {}).get("value", {})
    return {
        "attempted": True,
        "identifierFilled": bool(value.get("identifierFilled")),
        "passwordFilled": bool(value.get("passwordFilled")),
        "actionClicked": bool(value.get("actionClicked")),
    }


def get_chrome_path() -> Optional[str]:
    """현재 플랫폼의 Chrome 실행 경로 반환"""
    system = platform.system()

    if system == "Darwin":
        path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return path if Path(path).exists() else None
    elif system == "Linux":
        for candidate in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]:
            if shutil.which(candidate):
                return candidate
        return None
    elif system == "Windows":
        path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        return path if Path(path).exists() else None

    return None


def find_available_port(starting_from: int = CDP_DEFAULT_PORT, max_attempts: int = 10) -> int:
    """사용 가능한 포트 찾기"""
    for offset in range(max_attempts):
        port = starting_from + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", port))
                return port
        except OSError:
            continue
    raise RuntimeError(
        f"포트 {starting_from}-{starting_from + max_attempts - 1} 범위에서 사용 가능한 포트 없음"
    )


def execute_cdp_command(ws_url: str, method: str, params: Optional[dict] = None) -> dict:
    """WebSocket을 통한 CDP 명령 실행"""
    try:
        import websocket  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        typer.echo("websocket-client 패키지가 필요합니다:")
        typer.echo("  uv pip install websocket-client")
        raise typer.Exit(1) from exc

    ws = websocket.create_connection(ws_url, timeout=30)
    try:
        command = {"id": 1, "method": method, "params": params or {}}
        ws.send(json.dumps(command))
        while True:
            response = json.loads(ws.recv())
            if response.get("id") == 1:
                return response.get("result", {})
    finally:
        ws.close()


def get_current_url(ws_url: str) -> str:
    """현재 페이지 URL 반환"""
    execute_cdp_command(ws_url, "Runtime.enable")
    result = execute_cdp_command(ws_url, "Runtime.evaluate", {"expression": "window.location.href"})
    return result.get("result", {}).get("value", "")


def is_logged_in(url: str, platform_name: str) -> bool:
    """로그인 상태 확인 (URL 기반)"""
    if "/login" in url or "/flow/login" in url:
        return False

    if platform_name == "threads":
        if "accounts.google.com" in url or "instagram.com/accounts" in url:
            return False
        return "threads.net" in url or "threads.com" in url

    elif platform_name == "x":
        return "x.com/home" in url or ("x.com" in url and "/i/flow" not in url)

    elif platform_name == "linkedin":
        return "linkedin.com/feed" in url
    elif platform_name == "reddit":
        return "reddit.com" in url and "/login" not in url

    return False


def login(platform_name: str = "threads"):  # noqa: C901
    """CDP 기반 로그인 (Chrome에서 수동 로그인 후 쿠키 자동 추출)"""
    config = PLATFORM_CONFIG.get(platform_name)
    if not config:
        typer.echo(f"지원하지 않는 플랫폼: {platform_name}")
        typer.echo(f"지원 플랫폼: {', '.join(PLATFORM_CONFIG.keys())}")
        raise typer.Exit(1)

    chrome_path = get_chrome_path()
    if not chrome_path:
        typer.echo("Chrome이 설치되어 있지 않습니다.")
        raise typer.Exit(1)

    port = find_available_port()
    session_path = config["session_path"]
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    # Chrome 실행
    args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={CHROME_PROFILE_DIR}",
        "--remote-allow-origins=*",
        config["login_url"],
    ]

    typer.echo(f"Chrome을 실행합니다... {config['display_name']}에 로그인해주세요.")
    typer.echo(f"(최대 {LOGIN_TIMEOUT // 60}분 대기)")
    credentials = load_login_credentials_from_env()
    if credentials:
        typer.echo(
            "저장된 credential로 자동 입력을 시도합니다. 필요하면 수동으로 이어서 로그인하세요."
        )

    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(3)

    try:
        # WebSocket URL 얻기
        debugger_url = None
        for _ in range(10):
            try:
                resp = requests.get(f"http://localhost:{port}/json/version", timeout=3)
                if resp.status_code == 200:
                    debugger_url = resp.json().get("webSocketDebuggerUrl")
                    break
            except requests.RequestException:
                time.sleep(1)

        if not debugger_url:
            typer.echo("Chrome DevTools에 연결할 수 없습니다.")
            raise typer.Exit(1)

        # 페이지의 WebSocket URL 찾기
        pages_ws_url = None
        for _ in range(10):
            try:
                resp = requests.get(f"http://localhost:{port}/json", timeout=3)
                pages = resp.json()
                for page in pages:
                    page_url = page.get("url", "")
                    if any(d in page_url for d in config["cookie_domains"]):
                        pages_ws_url = page.get("webSocketDebuggerUrl")
                        break
                if pages_ws_url:
                    break
                if pages:
                    pages_ws_url = pages[0].get("webSocketDebuggerUrl")
                    break
            except requests.RequestException:
                time.sleep(1)

        if not pages_ws_url:
            typer.echo("Chrome 페이지에 연결할 수 없습니다.")
            raise typer.Exit(1)

        # 로그인 대기
        start_time = time.time()
        autofill_reported = False
        while time.time() - start_time < LOGIN_TIMEOUT:
            try:
                if credentials:
                    autofill_result = attempt_login_autofill(
                        pages_ws_url,
                        platform_name,
                        credentials[0],
                        credentials[1],
                    )
                    if (
                        not autofill_reported
                        and autofill_result["attempted"]
                        and (
                            autofill_result["identifierFilled"]
                            or autofill_result["passwordFilled"]
                            or autofill_result["actionClicked"]
                        )
                    ):
                        typer.echo(
                            "자동 입력을 적용했습니다. 추가 인증이 필요하면 브라우저에서 계속 진행하세요."
                        )
                        autofill_reported = True

                current_url = get_current_url(pages_ws_url)
                if is_logged_in(current_url, platform_name):
                    typer.echo("로그인 감지! 쿠키를 추출합니다...")
                    time.sleep(2)
                    break
            except Exception:
                pass
            time.sleep(3)
        else:
            typer.echo("로그인 타임아웃")
            raise typer.Exit(1)

        # 쿠키 추출
        result = execute_cdp_command(pages_ws_url, "Network.getCookies")
        raw_cookies = result.get("cookies", [])

        # 플랫폼 관련 쿠키만 필터링
        filtered_cookies = []
        for c in raw_cookies:
            domain = c.get("domain", "")
            if any(d in domain for d in config["cookie_domains"]):
                filtered_cookies.append(
                    {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c.get("path", "/"),
                        "expires": c.get("expires", -1),
                        "httpOnly": c.get("httpOnly", False),
                        "secure": c.get("secure", True),
                        "sameSite": c.get("sameSite", "None"),
                    }
                )

        if not filtered_cookies:
            typer.echo("쿠키를 추출하지 못했습니다. 로그인이 완료되었는지 확인하세요.")
            raise typer.Exit(1)

        # 세션 파일 저장
        session_data = {"cookies": filtered_cookies}
        with open(session_path, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)

        typer.echo(f"세션 저장 완료: {session_path} ({len(filtered_cookies)}개 쿠키)")

    finally:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        typer.echo("Chrome을 종료했습니다.")
