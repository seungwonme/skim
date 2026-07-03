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
from pathlib import Path
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura  # pylint: disable=import-error
except ImportError:  # pragma: no cover — optional dependency
    trafilatura = None  # type: ignore[assignment]

try:
    import fitz  # PyMuPDF  # pylint: disable=import-error
except ImportError:  # pragma: no cover — optional dependency
    fitz = None  # type: ignore[assignment]

from .paths import workspace_root

SRT_TO_TXT = str(workspace_root() / "scripts" / "srt_to_txt.sh")


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
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
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

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join()
    return result["html"]


_UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
}

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


def _trafilatura_extract(html: str, url: str) -> Optional[dict]:
    """trafilatura로 HTML → markdown 본문 추출. subprocess 없이 Python 내부 처리."""
    if trafilatura is None:
        return None
    try:
        md = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            no_fallback=False,
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
        resp = requests.get(url, headers=_UA_HEADERS, timeout=timeout)
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
    return bool(_PLACEHOLDER_START.search(normalized) or _PLACEHOLDER_ANY.search(normalized))


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
    if word_count < min_words:
        return False
    title_clean = (title or "").strip()
    if title_clean and content == title_clean:
        return False
    return True


def extract_article_content(url: str, title: str) -> tuple[Optional[dict], str, Optional[str]]:
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
        else "trafilatura empty" if not data else f"thin (words={data.get('word_count', 0)})"
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
    best_method = "playwright+trafilatura" if rendered_data else "trafilatura" if data else "failed"
    return fallback, best_method, f"{thin_reason_1}; {thin_reason_2}"


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

    if any(code == "en-orig" or code.startswith("en-orig-") for code in available_codes):
        prefixes = ["en-orig", "en", "ko"]
    elif any(code == "ko-orig" or code.startswith("ko-orig-") for code in available_codes):
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


def _geeknews_topic_body_from_html(html: str) -> Optional[str]:
    """토픽 페이지 HTML에서 큐레이터 요약(.topic_contents)을 마크다운으로 추출"""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(".topic_contents")
    if not node:
        return None
    lines = []
    for el in node.find_all(["p", "li", "h1", "h2", "h3", "blockquote"]):
        text = el.get_text(" ", strip=True)
        if text:
            lines.append(f"- {text}" if el.name == "li" else text)
    body = "\n".join(dict.fromkeys(lines)) if lines else node.get_text(" ", strip=True)
    return body.strip() or None


def fetch_geeknews_topic_body(topic_url: str) -> Optional[str]:
    """긱뉴스 토픽 페이지에서 한국어 요약 본문을 가져온다"""
    try:
        r = requests.get(topic_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return _geeknews_topic_body_from_html(r.text)
    except Exception:
        return None


def resolve_geeknews_original_url(topic_url: str) -> Optional[str]:
    """긱뉴스 토픽 페이지에서 원문 URL을 추출"""
    try:
        r = requests.get(topic_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        title_a = soup.select_one(".topictitle a")
        if title_a:
            href = title_a.get("href", "")
            if href and not href.startswith("/") and "news.hada.io" not in href:
                return href
    except Exception:
        pass
    return None


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
            continue

        # GeekNews: 토픽 페이지의 한국어 요약을 1차 본문으로 삼고, 원문 추출은 뒤에 붙인다.
        # 원문이 랜딩/디렉터리 페이지라 내비게이션 잡문이 추출돼도 본문이 무너지지 않는다.
        if item["platform"] == "geeknews" and "news.hada.io/topic" in url:
            topic_body = fetch_geeknews_topic_body(url)
            original_url = resolve_geeknews_original_url(url)
            data = None
            if original_url:
                item["original_url"] = original_url
                print(f"    -> 원문: {original_url[:60]}")
                data = defuddle(original_url)
                if not _is_content_usable(data, item.get("title", ""), min_words=3):
                    data, method, error = extract_article_content(
                        original_url, item.get("title", "")
                    )
                    item["enrichment_method"] = method
                    if error:
                        item["enrichment_error"] = error
            elif not topic_body:
                data = defuddle(url)

            if topic_body:
                original_md = (data or {}).get("content_markdown", "").strip()
                merged = topic_body
                if original_md:
                    merged += "\n\n---\n\n## 원문 전문\n\n" + original_md
                data = dict(data or {})
                data["content_markdown"] = merged
                data["word_count"] = len(merged.split())
        elif (
            item["platform"].startswith("ailabs")
            or item["platform"].startswith("blogs")
            or item["platform"] == "producthunt"
        ):
            # ailabs/blogs: trafilatura 기반 파이썬 본문 추출 (defuddle hang 회피)
            # producthunt: /products SPA(403) 대신 제품 외부 사이트 리다이렉트를 추출 대상으로 사용
            target_url = item.get("enrich_url") or url
            data, method, error = extract_article_content(target_url, item.get("title", ""))
            if error:
                item["enrichment_error"] = error
                print(f"    [!] enrichment 경고: {error}")
            # 기본 품질 게이트를 통과 못하면 본문을 채우지 않고
            # enrichment_method="failed"로 표시. DB upsert는 이 마커를 "재시도 가능"으로
            # 해석해 다음 크롤링에서 더 좋은 본문이 오면 덮어쓴다.
            if not _is_content_usable(data, item.get("title", "")):
                data = None
                method = "failed"
                item.setdefault("enrichment_error", "content not usable")
            item["enrichment_method"] = method
            print(f"    -> method={method}")
        else:
            data = defuddle(url)

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
    return "\n".join(b[4].strip() for b in (left + right))


def extract_pdf_text(pdf_url: str, timeout: int = 30, min_words: int = 100) -> Optional[dict]:
    """arXiv PDF를 내려받아 본문 텍스트를 추출 (2단 레이아웃 대응)."""
    if fitz is None:
        return None
    try:
        resp = requests.get(pdf_url, headers=_UA_HEADERS, timeout=timeout)
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
                    print(f"    -> HTML/PDF 없음, abstract 폴백 ({item['word_count']} words)")
                else:
                    item["content_markdown"] = ""
                    item["word_count"] = 0
                    item["enrichment_method"] = "failed"
                    print("    -> HTML/PDF/abstract 모두 없음")

    html_n = sum(1 for it in targets if it.get("enrichment_method") is None and it.get("word_count", 0) > 0)
    pdf_n = sum(1 for it in targets if it.get("enrichment_method") == "pdf")
    abstract_n = sum(
        1 for it in targets if it.get("enrichment_method") == "failed" and it.get("word_count", 0) > 0
    )
    print(f"  -> HTML {html_n}개, PDF {pdf_n}개, abstract 폴백 {abstract_n}개 / 총 {len(targets)}개")
    return items
