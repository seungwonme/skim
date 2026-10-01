"""
@file enrichment.py
@description 콘텐츠 enrichment 유틸리티 (defuddle, YouTube transcript, etc.)
"""

import asyncio
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura  # pylint: disable=import-error

    # pylint: disable-next=import-error
    from trafilatura.settings import use_config
except ImportError:  # pragma: no cover — optional dependency
    trafilatura = None  # type: ignore[assignment]

try:
    import fitz  # PyMuPDF  # pylint: disable=import-error
except ImportError:  # pragma: no cover — optional dependency
    fitz = None  # type: ignore[assignment]

from .comments import Comment, append_comment_section, render_comment_section
from .feed_utils import USER_AGENT, make_retrying_session
from .paths import workspace_root

# 렌더 스레드를 기다릴 때 playwright 자체 타임아웃 위에 얹는 여유. 브라우저 launch와
# 종료가 이 안에 들어간다.
RENDER_JOIN_GRACE_SECONDS = 60

SRT_TO_TXT = str(workspace_root() / "scripts" / "srt_to_txt.sh")
YOUTUBE_MAX_COMMENTS = 15


def _run_group(cmd: list, timeout: int) -> subprocess.CompletedProcess:
    """subprocess.run 대체. 타임아웃 시 직접 자식만이 아니라 프로세스 그룹 전체를 죽인다.
    bunx/yt-dlp가 띄우는 하위 node 프로세스가 고아로 남는 것을 막는다."""
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
            raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def defuddle(url: str, timeout: int = 45) -> Optional[dict]:
    """defuddle CLI로 URL의 본문 콘텐츠를 추출 (bunx 콜드스타트 + 원격 fetch 고려)"""
    try:
        result = _run_group(
            ["bunx", "defuddle", "parse", url, "--json", "--markdown"],
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            return {
                "content_markdown": data.get("content", ""),
                "word_count": data.get("wordCount", 0),
                "description": data.get("description", ""),
                "image": data.get("image", ""),
            }
    except Exception as e:
        print(f"    [!] defuddle 실패 ({url[:50]}...): {e}")
    return None


def _defuddle_html_file(html_path: str, timeout: int = 45) -> Optional[dict]:
    """미리 렌더링된 HTML 파일에 defuddle 적용 (bunx 콜드스타트 고려)."""
    try:
        result = _run_group(
            ["bunx", "defuddle", "parse", html_path, "--json", "--markdown"],
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            return {
                "content_markdown": data.get("content", ""),
                "word_count": data.get("wordCount", 0),
                "description": data.get("description", ""),
                "image": data.get("image", ""),
            }
    except Exception as e:
        print(f"    [!] defuddle(file) 실패 ({html_path}): {e}")
    return None


def _fetch_rendered_html_sync(url: str, timeout_ms: int = 30000) -> Optional[str]:
    """Playwright headless Chromium으로 JS 렌더링된 최종 HTML을 반환."""
    try:
        # pylint: disable=import-outside-toplevel
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                page.goto(url, wait_until="load", timeout=timeout_ms)
                page.wait_for_timeout(1500)
                return page.content()
            finally:
                browser.close()
    except Exception as e:
        print(f"    [!] playwright 렌더링 실패 ({url[:50]}...): {e}")
        return None


def _fetch_rendered_html(url: str, timeout_ms: int = 30000) -> Optional[str]:
    """async crawler 안에서도 sync Playwright를 별도 thread에서 실행한다."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _fetch_rendered_html_sync(url, timeout_ms)

    result: dict[str, Optional[str]] = {"html": None}

    def _run() -> None:
        result["html"] = _fetch_rendered_html_sync(url, timeout_ms)

    # join에 상한이 없으면 playwright launch나 page.content()가 멈출 때 크롤 프로세스가
    # 무기한 정지한다. daemon으로 두고 상한을 걸면 그 항목만 포기하고 다음으로 넘어간다.
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_ms / 1000 + RENDER_JOIN_GRACE_SECONDS)
    if thread.is_alive():
        print(f"    [!] playwright 렌더링 시간 초과 ({url[:50]}...)")
        return None
    return result["html"]


# 남의 글을 링크하는 소스. 원문 추출 품질이 곧 본문 품질이라 3단 사다리를 태운다.
_AGGREGATOR_PLATFORMS = frozenset({"hackernews", "lobsters", "everyto", "reddit"})
# 릴리스 노트나 짧은 공지도 정당한 본문이라 기사용 60단어 게이트를 낮춘다.
_AGGREGATOR_MIN_WORDS = 20

_UA_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,*/*",
}
# 본문 추출은 소스당 여러 건을 연달아 때린다. 503 하나로 그 항목을 통째로 버리지 않는다.
_HTTP_SESSION = make_retrying_session(_UA_HEADERS)

_PLACEHOLDER_EXACT = {
    "00:00",
    "loading",
    "loading...",
    "stage 1",
    "불러오는 중",
    "불러오는 중...",
}
_PLACEHOLDER_START = re.compile(
    r"^\s*(?:"
    r"stage \d+|"
    r"loading wasm|"
    r"just a moment|"
    r"access denied|"
    r"403 forbidden|"
    r"forbidden|"
    r"please enable javascript|"
    r"checking your browser|"
    r"please wait while we check|"
    r"verify you are human|"
    r"human verification|"
    r"captcha|"
    r"too many requests|"
    r"service unavailable|"
    r"application error|"
    r"error \d{3}|"
    r"not found|"
    r"page not found|"
    r"오늘의 .*불러오는 중입니다|"
    r"sito in allestimento"
    r")\b",
    re.IGNORECASE,
)
_PLACEHOLDER_ANY = re.compile(
    r"(?:"
    r"cloudflare ray id|"
    r"cf-browser-verification|"
    r"ddos protection by cloudflare|"
    r"sign in to confirm you.?re not a bot|"
    r"unusual traffic from your computer network|"
    r"서버 연결이 불안정합니다|"
    r"자동 재시도 중|"
    r"연결 중\.\.\."
    r")",
    re.IGNORECASE,
)

# GitHub 페이지의 화면 문구. 로그인 안내, 저장소 머리말, 알림/포크/스타 버튼이다.
# 60단어를 넘기도 해서 단어 수 게이트가 본문으로 세면 화면 문구만 받은 글이
# 통과한다. GitHub PDF 링크 4건이 그렇게 PDF 폴백까지 못 가고 화면 문구만
# 저장됐다 (#46). 게이트는 이걸 빼고 센다. 저장하는 본문은 건드리지 않는다.
_GITHUB_CHROME = re.compile(
    r"You (?:signed in with|signed out in|switched accounts on) another tab or window\."
    r"\s*Reload to refresh your session\.|"
    r"Dismiss alert|"
    r"\{\{ message \}\}|"
    r"You must be signed in to change notification settings|"
    r"^[ \t]*/ \*\*\[[^\]\n]+\]\(https://github\.com/[^)\s]+\)\*\*[^\n]*$|"
    r"\[(?:Notifications|Fork[^\]\n]*|Star[^\]\n]*)\]\(https://github\.com/login[^)\s]*\)",
    re.MULTILINE,
)
_GITHUB_CHROME_HINTS = ("another tab or window", "github.com/login")

# 릴리스 페이지에서 작성자가 쓴 부분은 노트 영역(`.markdown-body`)뿐이다.
_GITHUB_RELEASE_URL = re.compile(
    r"^https?://github\.com/[^/]+/[^/]+/releases/tag/[^/?#]+"
)
# blob 주소는 파일이 아니라 보기 페이지다. 파일은 같은 경로의 raw 주소에 있다.
_GITHUB_BLOB_URL = re.compile(r"^(https?://github\.com/[^/]+/[^/]+)/blob/(.+)$")


@lru_cache(maxsize=1)
def _fragment_config():
    """trafilatura는 250자보다 짧은 추출 결과를 버린다(MIN_EXTRACTED_SIZE).

    페이지째 추출할 때 메뉴만 잡힌 결과를 거르는 값이라, 본문만 떼어 낸 조각(피드
    본문, 릴리스 노트 영역)에는 맞지 않는다. 한 줄짜리 Claude Code 노트("Bug fixes
    and reliability improvements") 11건이 이 값에 걸려 못 들어왔다 (#46). 조각에도
    단어 수 게이트는 그대로 걸린다.
    """
    config = use_config()
    config.set("DEFAULT", "MIN_EXTRACTED_SIZE", "1")
    return config


def _trafilatura_extract(html: str, url: str, fragment: bool = False) -> Optional[dict]:
    """trafilatura로 HTML → markdown 본문 추출. subprocess 없이 Python 내부 처리.

    fragment는 이미 본문만 떼어 낸 HTML이다. 짧은 결과도 버리지 않는다.
    """
    if trafilatura is None:
        return None
    options = {"config": _fragment_config()} if fragment else {}
    try:
        md = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            **options,
        )
    except Exception as e:  # pylint: disable=broad-except
        print(f"    [!] trafilatura 실패 ({url[:60]}...): {e}")
        return None
    if not md:
        return None
    md = md.strip()
    return {
        "content_markdown": md,
        "word_count": len(md.split()),
        "description": "",
        "image": "",
    }


def _http_fetch_html(url: str, timeout: int = 20) -> Optional[str]:
    try:
        resp = _HTTP_SESSION.get(url, timeout=timeout)
    except requests.RequestException as e:
        print(f"    [!] HTTP fetch 실패 ({url[:60]}...): {e}")
        return None
    if resp.status_code != 200:
        print(f"    [!] HTTP {resp.status_code} ({url[:60]}...)")
        return None
    return resp.text


def _looks_like_placeholder_content(content: str) -> bool:
    normalized = re.sub(r"\s+", " ", content).strip()
    if not normalized:
        return False
    if normalized.lower() in _PLACEHOLDER_EXACT:
        return True
    return bool(
        _PLACEHOLDER_START.search(normalized) or _PLACEHOLDER_ANY.search(normalized)
    )


def has_github_chrome(content: str) -> bool:
    """GitHub 보기 페이지를 추출한 흔적(로그인 안내, 저장소 머리말)이 있는지."""
    return any(hint in content for hint in _GITHUB_CHROME_HINTS)


def _words_outside_github_chrome(content: str) -> int:
    """GitHub 화면 문구를 뺀 단어 수. 글머리표처럼 기호만 남은 토큰은 세지 않는다."""
    stripped = _GITHUB_CHROME.sub(" ", content)
    return sum(1 for token in stripped.split() if any(ch.isalnum() for ch in token))


def _is_content_usable(data: Optional[dict], title: str, min_words: int = 60) -> bool:
    """defuddle 결과가 실제 본문으로 쓸만한지 판정."""
    if not data:
        return False
    content = (data.get("content_markdown") or "").strip()
    if not content:
        return False
    if _looks_like_placeholder_content(content):
        return False
    word_count = data.get("word_count") or len(content.split())
    if has_github_chrome(content):
        word_count = _words_outside_github_chrome(content)
    if word_count < min_words:
        return False
    title_clean = (title or "").strip()
    if title_clean and content == title_clean:
        return False
    return True


def extract_article_content(
    url: str, title: str
) -> tuple[Optional[dict], str, Optional[str]]:
    """
    품질 게이트가 붙은 본문 추출 — Python 내부에서 전부 처리.

    순서:
      1) HTTP fetch + trafilatura
      2) 얇으면 Playwright 렌더 + trafilatura
      3) 둘 다 실패하면 defuddle (subprocess, 외부 노드 CLI; 최후의 수단)

    defuddle이 일부 사이트(Anthropic)에서 Node fetch 내부 hang을 일으키는 경우가 있어
    기본 경로에서는 제외했다.
    """
    # 1) HTTP + trafilatura
    html = _http_fetch_html(url)
    data = _trafilatura_extract(html, url) if html else None
    if _is_content_usable(data, title):
        return data, "trafilatura", None
    thin_reason_1 = (
        "http fetch failed"
        if not html
        else "trafilatura empty"
        if not data
        else f"thin (words={data.get('word_count', 0)})"
    )

    # 2) Playwright 렌더 + trafilatura
    rendered_html = _fetch_rendered_html(url)
    rendered_data = _trafilatura_extract(rendered_html, url) if rendered_html else None
    if _is_content_usable(rendered_data, title):
        return rendered_data, "playwright+trafilatura", None
    thin_reason_2 = (
        "playwright fetch failed"
        if not rendered_html
        else (
            "trafilatura empty (rendered)"
            if not rendered_data
            else f"thin after playwright (words={rendered_data.get('word_count', 0)})"
        )
    )

    # 3) defuddle 최후 시도 (실패해도 무관)
    try:
        defuddle_data = defuddle(url)
        if _is_content_usable(defuddle_data, title):
            return defuddle_data, "defuddle", None
        if rendered_html:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".html", delete=False
            ) as tmp:
                tmp.write(rendered_html)
                tmp_path = tmp.name
            try:
                defuddle_file_data = _defuddle_html_file(tmp_path)
            finally:
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass
            if _is_content_usable(defuddle_file_data, title):
                return defuddle_file_data, "playwright+defuddle", None
    except Exception as e:  # pylint: disable=broad-except
        print(f"    [!] defuddle fallback 예외: {e}")

    fallback = rendered_data or data
    best_method = (
        "playwright+trafilatura"
        if rendered_data
        else "trafilatura"
        if data
        else "failed"
    )
    return fallback, best_method, f"{thin_reason_1}; {thin_reason_2}"


def _extract_feed_content_html(item: dict) -> Optional[dict]:
    html = (item.get("content_html") or "").strip()
    if not html:
        return None
    return _trafilatura_extract(html, item.get("url", ""), fragment=True)


def _extract_article_or_feed_content(
    item: dict, target_url: str
) -> tuple[Optional[dict], str, Optional[str]]:
    title = item.get("title", "")
    data, method, error = extract_article_content(target_url, title)
    if _is_content_usable(data, title):
        return data, method, error

    feed_data = _extract_feed_content_html(item)
    if _is_content_usable(feed_data, title):
        return feed_data, "feed-content", None

    return data, method, error


def extract_youtube_transcript(url: str, timeout: int = 60) -> Optional[dict]:
    """yt-dlp로 YouTube 자막을 추출하고 srt_to_txt.sh로 정리"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1) 자막 목록 확인 → 수동 자막 시도 → 자동 자막 폴백
        subs_info = _run_group(
            ["yt-dlp", "--list-subs", "--skip-download", url],
            timeout=30,
        )

        info = subs_info.stdout + subs_info.stderr
        lang = _select_youtube_subtitle_languages(info)

        # 수동 자막 시도
        _run_group(
            [
                "yt-dlp",
                "--write-sub",
                "--sub-lang",
                lang,
                "--skip-download",
                "--sub-format",
                "srt",
                "-o",
                f"{tmpdir}/%(id)s.%(ext)s",
                url,
            ],
            timeout=timeout,
        )

        # 수동 자막 파일 찾기
        srt_files = list(Path(tmpdir).glob("*.srt"))

        # 없으면 자동 자막 폴백
        if not srt_files:
            _run_group(
                [
                    "yt-dlp",
                    "--write-auto-sub",
                    "--sub-lang",
                    lang,
                    "--skip-download",
                    "--sub-format",
                    "srt",
                    "-o",
                    f"{tmpdir}/%(id)s.%(ext)s",
                    url,
                ],
                timeout=timeout,
            )
            srt_files = list(Path(tmpdir).glob("*.srt"))

        if not srt_files:
            return None

        # glob 순서는 보장이 없다. 선호 언어(lang) 순서대로 자막 파일을 고른다.
        preferred = lang.split(",")

        def _pref_rank(path: Path) -> int:
            code = path.stem.split(".")[-1] if "." in path.stem else ""
            return preferred.index(code) if code in preferred else len(preferred)

        srt_file = min(srt_files, key=_pref_rank)
        txt_file = srt_file.with_suffix(".txt")

        # 2) srt_to_txt.sh로 정리
        subprocess.run(
            ["bash", SRT_TO_TXT, str(srt_file), str(txt_file)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        if txt_file.exists():
            content = txt_file.read_text(encoding="utf-8")
        else:
            # fallback: SRT 원본에서 텍스트만 추출
            content = srt_file.read_text(encoding="utf-8")

        word_count = len(content.split())

        # 자막 언어 감지
        lang_code = srt_file.stem.split(".")[-1] if "." in srt_file.stem else "unknown"

        return {
            "content_markdown": content,
            "word_count": word_count,
            "subtitle_lang": lang_code,
        }


def extract_youtube_comments(
    url: str, limit: int = YOUTUBE_MAX_COMMENTS, timeout: int = 90
) -> Optional[str]:
    """yt-dlp로 상위 댓글을 받아 본문용 마크다운 섹션으로 만든다.

    자막과 달리 요청이 따로 들어가므로 실패해도 자막 추출을 막지 않는다.
    답글은 받지 않는다(max_comments의 마지막 인자 0) — 영상 댓글의 답글은
    대개 잡담이라 본문 신호 대비 길이만 늘린다.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _run_group(
            [
                "yt-dlp",
                "--skip-download",
                "--write-comments",
                "--write-info-json",
                "--extractor-args",
                f"youtube:comment_sort=top;max_comments={limit},all,{limit},0",
                "-o",
                f"{tmpdir}/%(id)s.%(ext)s",
                url,
            ],
            timeout=timeout,
        )

        info_files = list(Path(tmpdir).glob("*.info.json"))
        if not info_files:
            return None
        try:
            info = json.loads(info_files[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    collected = [
        Comment(
            author=str(raw.get("author") or "unknown"),
            text=str(raw.get("text") or ""),
            score=raw.get("like_count"),
        )
        for raw in (info.get("comments") or [])
    ]
    return render_comment_section(
        "YouTube Comments", collected, max_comments=limit, score_unit="like"
    )


def _parse_subtitle_codes(section_text: str) -> List[str]:
    """yt-dlp --list-subs 출력에서 자막 코드 목록을 추출합니다."""
    codes: List[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("["):
            continue
        if (
            stripped.startswith("Language")
            or stripped.startswith("Name")
            or stripped.startswith("Formats")
        ):
            continue
        match = re.match(r"^([A-Za-z0-9-]+)\s{2,}", stripped)
        if match:
            codes.append(match.group(1))
    return codes


def _extract_subtitle_sections(info: str) -> tuple[List[str], List[str]]:
    """수동/자동 자막 코드를 분리합니다."""
    manual_lines: List[str] = []
    auto_lines: List[str] = []
    current: Optional[str] = None

    for line in info.splitlines():
        if line.startswith("[info] Available subtitles"):
            current = "manual"
            continue
        if line.startswith("[info] Available automatic captions"):
            current = "auto"
            continue
        if current == "manual":
            manual_lines.append(line)
        elif current == "auto":
            auto_lines.append(line)

    return _parse_subtitle_codes("\n".join(manual_lines)), _parse_subtitle_codes(
        "\n".join(auto_lines)
    )


def _resolve_preferred_subtitle_codes(available_codes: List[str]) -> List[str]:
    """선호 언어 prefix를 실제 yt-dlp 자막 코드로 매핑합니다."""
    if not available_codes:
        return []

    if any(
        code == "en-orig" or code.startswith("en-orig-") for code in available_codes
    ):
        prefixes = ["en-orig", "en", "ko"]
    elif any(
        code == "ko-orig" or code.startswith("ko-orig-") for code in available_codes
    ):
        prefixes = ["ko-orig", "ko", "en"]
    else:
        prefixes = ["en", "ko"]

    selected: List[str] = []
    for prefix in prefixes:
        for code in available_codes:
            if code == prefix or code.startswith(f"{prefix}-"):
                if code not in selected:
                    selected.append(code)
                break
    return selected


def _select_youtube_subtitle_languages(info: str) -> str:
    """yt-dlp가 실제로 인식한 자막 코드 중 우선순위가 높은 값을 반환합니다."""
    manual_codes, auto_codes = _extract_subtitle_sections(info)
    selected = _resolve_preferred_subtitle_codes(manual_codes)
    if not selected:
        selected = _resolve_preferred_subtitle_codes(auto_codes)
    return ",".join(selected) if selected else "en,ko"


def _apply_youtube_summary_fallback(item: dict) -> bool:
    """자막 추출이 실패하면 description 요약을 digest용 본문으로 사용합니다."""
    summary = (item.get("summary") or "").strip()
    if not summary:
        return False
    item["content_markdown"] = summary
    item["word_count"] = len(summary.split())
    item["subtitle_lang"] = "summary"
    return True


def is_github_release_url(url: str) -> bool:
    """GitHub 릴리스 한 건의 페이지인지."""
    return bool(_GITHUB_RELEASE_URL.match(url or ""))


def github_raw_url(url: str) -> Optional[str]:
    """GitHub blob 주소(보기 페이지)를 파일을 내려받는 raw 주소로 바꾼다."""
    match = _GITHUB_BLOB_URL.match(url or "")
    if not match:
        return None
    return f"{match.group(1)}/raw/{match.group(2)}"


def is_github_pdf_url(url: str) -> bool:
    """저장소 안 PDF의 보기 페이지(blob)인지."""
    if github_raw_url(url) is None:
        return False
    return urlsplit(url).path.lower().endswith(".pdf")


def extract_github_release_notes(url: str) -> Optional[dict]:
    """릴리스 페이지에서 노트 영역(`.markdown-body`)만 마크다운으로 받는다.

    페이지째 추출하면 로그인 안내, 저장소 머리말, 커밋 서명 안내가 노트를 감싼다.
    노트 영역이 없으면(노트를 안 쓴 릴리스, 마크업 변경) None이다.
    """
    html = _http_fetch_html(url)
    if not html:
        return None
    node = BeautifulSoup(html, "html.parser").select_one(".markdown-body")
    if node is None:
        return None
    return _trafilatura_extract(str(node), url, fragment=True)


def _github_release_notes(item: dict, url: str) -> tuple[Optional[dict], str]:
    """GitHub 릴리스 노트를 받는다 (#46). (data, method)를 돌려준다.

    저장소의 릴리스 피드를 구독한 경우(blogs) 피드 본문이 노트 그 자체라 페이지를
    열지 않는다. 애그리게이터의 피드 본문은 그 사이트의 설명이라 쓰지 않는다.
    패치 릴리스의 노트는 30단어 안팎이라 기사용 60단어 게이트에 걸려 빈 본문이
    됐다. 노트는 한 줄이어도 정당한 본문이라 단어 수로 거르지 않는다.
    """
    title = item.get("title", "")
    if (item.get("platform") or "").startswith("blogs"):
        feed_data = _extract_feed_content_html(item)
        if _is_content_usable(feed_data, title, min_words=1):
            return feed_data, "feed-content"
    page_data = extract_github_release_notes(url)
    if _is_content_usable(page_data, title, min_words=1):
        return page_data, "github-release"
    return None, "failed"


def _pdf_fallback(url: str, min_words: int = 60) -> Optional[dict]:
    """링크가 PDF면 PDF 추출을 시도한다. HTML 추출기는 PDF에서 늘 실패한다."""
    if not url or ".pdf" not in url.lower():
        return None
    data = extract_pdf_text(github_raw_url(url) or url, min_words=min_words)
    return data or None


def _github_content(
    item: dict, url: str, min_words: int
) -> Optional[tuple[Optional[dict], str, str]]:
    """GitHub 릴리스와 PDF 링크는 보기 페이지를 추출하지 않는다 (#46).

    (data, method, 못 받았을 때의 오류)를 돌려준다. 다른 링크면 None이다.
    페이지째 추출하면 로그인 안내와 메뉴 문구가 본문 자리를 차지한다. 릴리스는 노트
    영역만 본다. PDF 보기 페이지는 파일을 iframe으로 띄워 HTML에 본문이 없어서,
    메뉴 문구 381단어가 게이트를 통과해 PDF 대신 저장됐다(Steins Gate). raw만 본다.
    """
    if is_github_release_url(url):
        data, method = _github_release_notes(item, url)
        return data, method, "release notes not found"
    if is_github_pdf_url(url):
        data = _pdf_fallback(url, min_words=min_words)
        return data, ("pdf" if data else "failed"), "github pdf not found"
    return None


def _enrich_article_item(item: dict, url: str, min_words: int = 60) -> Optional[dict]:
    """기사형 소스 공통 본문 추출 (trafilatura 기반, defuddle hang 회피).

    producthunt는 /products SPA(403) 대신 제품 외부 사이트 리다이렉트를 추출 대상으로 쓴다.
    품질 게이트를 통과 못하면 enrichment_method="failed"로 표시한다 (DB upsert는 이 마커를
    "재시도 가능"으로 해석해 다음 크롤링에서 더 좋은 본문이 오면 덮어쓴다). 단 producthunt는
    외부 사이트 추출이 실패해도 제품 태그라인을 본문 최저선으로 남긴다 - 없으면 digest에서
    항목이 조용히 사라진다.

    min_words는 링크 애그리게이터(hackernews/lobsters)를 위해 뚫어 뒀다. 그쪽은
    짧은 릴리스 노트나 공지도 정당한 본문이라 60단어 게이트에 걸리면 멀쩡한 글이
    failed로 떨어진다.
    """
    target_url = item.get("enrich_url") or url
    github = _github_content(item, target_url, min_words)
    if github is not None:
        # 페이지째 추출로 넘어가지 않는다. 넘어가면 화면 문구만 받아 온다.
        data, method, missing = github
        if data is None:
            item.setdefault("enrichment_error", missing)
        item["enrichment_method"] = method
        print(f"    -> method={method}")
        return data
    data, method, error = _extract_article_or_feed_content(item, target_url)
    if error:
        item["enrichment_error"] = error
        print(f"    [!] enrichment 경고: {error}")
    if not _is_content_usable(data, item.get("title", ""), min_words=min_words):
        tagline = (item.get("tagline") or "").strip()
        pdf = _pdf_fallback(target_url, min_words=min_words)
        if pdf:
            item["enrichment_method"] = "pdf"
            print("    -> method=pdf")
            return pdf
        if tagline:
            data = {"content_markdown": tagline, "word_count": len(tagline.split())}
        else:
            data = None
            item.setdefault("enrichment_error", "content not usable")
        method = "failed"
    item["enrichment_method"] = method
    print(f"    -> method={method}")
    return data


def _apply_youtube_comments(item: dict, url: str) -> None:
    """영상 댓글을 본문 뒤에 잇는다. 자막과 별개 요청이라 자막이 없어도 남길 가치가 있다."""
    try:
        section = extract_youtube_comments(url)
    except Exception as e:  # noqa: BLE001 - 댓글 실패가 영상 저장을 막지 않는다
        print(f"    [!] 댓글 추출 실패: {e}")
        return
    if not section:
        return
    item["content_markdown"] = append_comment_section(
        item.get("content_markdown"), section
    )
    print(f"    -> 댓글 {section.count(chr(10) + '- ') + 1}건")


def _extract_for_platform(item: dict, url: str) -> Optional[dict]:
    """플랫폼에 맞는 본문 추출 경로를 고른다."""
    platform = item["platform"]
    if platform == "geeknews":
        # GeekNews 본문은 crawlers/feed/geeknews.py의 enrich_geeknews_items가 만든다.
        # 토픽 페이지는 요청량으로 막히는 자리라 그 모듈의 간격과 서킷브레이커를
        # 거쳐야 한다. 여기서 열면 둘 다 우회해 2026-09의 차단을 되풀이한다 (#29).
        print("    [!] GeekNews는 enrich_geeknews_items로 추출합니다. 건너뜁니다.")
        return None
    if (
        platform.startswith("ailabs")
        or platform.startswith("blogs")
        or platform == "producthunt"
    ):
        return _enrich_article_item(item, url)
    if platform in _AGGREGATOR_PLATFORMS:
        # 링크 애그리게이터는 defuddle 단발만 타고 있었다. 3단 사다리
        # (HTTP+trafilatura -> playwright 렌더 -> defuddle)를 못 받아 파이프라인에서
        # 가장 약한 추출 경로였다. 짧은 릴리스 노트도 정당한 본문이라 게이트는 낮춘다.
        return _enrich_article_item(item, url, min_words=_AGGREGATOR_MIN_WORDS)
    return defuddle(url)


def enrich_with_content(items: List[dict]) -> List[dict]:
    """각 항목에 defuddle로 원문 콘텐츠 추가"""
    targets = list(items)
    if not targets:
        return items

    print(f"\n[콘텐츠] {len(targets)}개 항목의 원문을 추출합니다...")

    for i, item in enumerate(targets):
        # 필드 누락 항목 하나가 KeyError로 배치 전체를 죽이지 않게 .get으로 읽는다.
        url = item.get("url", "")
        title_short = (item.get("title") or "")[:50]
        print(f"  [{i + 1}/{len(targets)}] {title_short}...")

        # YouTube 영상: yt-dlp로 자막 추출
        if (item.get("platform") or "").startswith("youtube/"):
            try:
                data = extract_youtube_transcript(url)
                if data:
                    item["content_markdown"] = data["content_markdown"]
                    item["word_count"] = data["word_count"]
                    item["subtitle_lang"] = data.get("subtitle_lang", "")
                    print(
                        f"    -> 자막: {data['word_count']} words ({data.get('subtitle_lang', '')})"
                    )
                elif _apply_youtube_summary_fallback(item):
                    print(f"    -> 요약 fallback: {item['word_count']} words")
                else:
                    item["content_markdown"] = ""
                    item["word_count"] = 0
                    print("    -> 자막 없음")
            except Exception as e:
                if _apply_youtube_summary_fallback(item):
                    print(f"    [!] 자막 추출 실패, 요약 fallback 사용: {e}")
                else:
                    item["content_markdown"] = ""
                    item["word_count"] = 0
                    print(f"    [!] 자막 추출 실패: {e}")

            _apply_youtube_comments(item, url)
            continue

        data = _extract_for_platform(item, url)

        if data and _is_content_usable(data, item.get("title", ""), min_words=3):
            item["content_markdown"] = data.get("content_markdown", "")
            item["word_count"] = data.get("word_count", 0)
            item["description"] = data.get("description", "")
            item["image"] = data.get("image", "")
        else:
            item["content_markdown"] = ""
            item["word_count"] = 0

    extracted = sum(1 for it in targets if it.get("word_count", 0) > 0)
    print(f"  -> {extracted}/{len(targets)}개 콘텐츠 추출 성공")
    return items


def _paper_pdf_url(url: str) -> Optional[str]:
    """논문 URL에서 arXiv PDF URL을 유도. HTML 버전이 없어도 PDF는 대개 존재한다."""
    if "arxiv.org/abs/" in url:
        return "https://arxiv.org/pdf/" + url.split("/abs/")[-1]
    if "huggingface.co/papers/" in url:
        return "https://arxiv.org/pdf/" + url.split("/papers/")[-1]
    return None


def _pdf_page_text(page) -> str:
    """PDF 한 페이지 텍스트. 2단 레이아웃을 고려해 좌측 컬럼을 먼저, 우측을 나중에 읽는다."""
    mid = page.rect.width / 2
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    left = sorted((b for b in blocks if b[0] < mid), key=lambda b: b[1])
    right = sorted((b for b in blocks if b[0] >= mid), key=lambda b: b[1])
    return "\n".join(b[4].strip() for b in left + right)


def extract_pdf_text(
    pdf_url: str, timeout: int = 30, min_words: int = 100
) -> Optional[dict]:
    """arXiv PDF를 내려받아 본문 텍스트를 추출 (2단 레이아웃 대응)."""
    if fitz is None:
        return None
    try:
        # arxiv.org/pdf를 한 회차에 최대 50건 연속으로 때리는 경로다. 단발이면
        # 여기서 막힐 때 본문이 조용히 abstract 폴백으로 떨어진다.
        resp = _HTTP_SESSION.get(pdf_url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"    [!] PDF fetch 실패 ({pdf_url[:60]}...): {exc}")
        return None
    try:
        doc = fitz.open(stream=resp.content, filetype="pdf")
    except Exception as exc:  # pylint: disable=broad-except
        print(f"    [!] PDF 파싱 실패: {exc}")
        return None
    try:
        pages = [_pdf_page_text(doc.load_page(i)) for i in range(doc.page_count)]
    finally:
        doc.close()

    text = re.sub(r"[ \t]+", " ", "\n".join(pages)).strip()
    words = len(text.split())
    if words < min_words:
        return None
    return {"content_markdown": text, "word_count": words}


def enrich_papers_with_content(items: List[dict]) -> List[dict]:
    """논문 항목에 arXiv HTML 전문을 defuddle로 추출"""
    targets = list(items)
    if not targets:
        return items

    print(f"\n[논문 전문] {len(targets)}개 논문의 전문을 추출합니다...")

    for i, item in enumerate(targets):
        title_short = (item.get("title") or "")[:50]
        print(f"  [{i + 1}/{len(targets)}] {title_short}...")

        # arxiv URL에서 HTML 버전 URL 생성
        url = item.get("url", "")
        html_url = None
        if "arxiv.org/abs/" in url:
            html_url = url.replace("/abs/", "/html/")
        elif "huggingface.co/papers/" in url:
            paper_id = url.split("/papers/")[-1]
            html_url = f"https://arxiv.org/html/{paper_id}"

        if not html_url:
            continue

        data = defuddle(html_url, timeout=20)
        if data and data["word_count"] > 100:
            item["content_markdown"] = data["content_markdown"]
            item["word_count"] = data["word_count"]
            print(f"    -> {data['word_count']} words")
        else:
            # HTML 버전이 없으면(대개 404) PDF 전문을 추출한다. PDF는 전문이라 확정 처리한다.
            pdf_url = _paper_pdf_url(url)
            pdf_data = extract_pdf_text(pdf_url) if pdf_url else None
            if pdf_data:
                item["content_markdown"] = pdf_data["content_markdown"]
                item["word_count"] = pdf_data["word_count"]
                item["enrichment_method"] = "pdf"
                print(f"    -> HTML 없음, PDF 추출 ({pdf_data['word_count']} words)")
            else:
                # PDF도 실패하면 abstract를 폴백으로 채우고 enrichment_method=failed 마커를 단다.
                # 다음 크롤에서 HTML/PDF가 생기면 upsert가 덮어쓴다.
                abstract = (item.get("abstract") or item.get("summary", "")).strip()
                if abstract:
                    item["content_markdown"] = abstract
                    item["word_count"] = len(abstract.split())
                    item["enrichment_method"] = "failed"
                    print(
                        f"    -> HTML/PDF 없음, abstract 폴백 ({item['word_count']} words)"
                    )
                else:
                    item["content_markdown"] = ""
                    item["word_count"] = 0
                    item["enrichment_method"] = "failed"
                    print("    -> HTML/PDF/abstract 모두 없음")

    html_n = sum(
        1
        for it in targets
        if it.get("enrichment_method") is None and it.get("word_count", 0) > 0
    )
    pdf_n = sum(1 for it in targets if it.get("enrichment_method") == "pdf")
    abstract_n = sum(
        1
        for it in targets
        if it.get("enrichment_method") == "failed" and it.get("word_count", 0) > 0
    )
    print(
        f"  -> HTML {html_n}개, PDF {pdf_n}개, abstract 폴백 {abstract_n}개 / 총 {len(targets)}개"
    )
    return items
