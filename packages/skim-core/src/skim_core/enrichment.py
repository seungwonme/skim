"""
@file enrichment.py
@description 콘텐츠 enrichment 유틸리티 (defuddle, YouTube transcript, etc.)
"""

import json
import subprocess
from pathlib import Path
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from .paths import workspace_root

SRT_TO_TXT = str(workspace_root() / "tooling" / "scripts" / "srt_to_txt.sh")


def defuddle(url: str, timeout: int = 15) -> Optional[dict]:
    """defuddle CLI로 URL의 본문 콘텐츠를 추출"""
    try:
        result = subprocess.run(
            ["bunx", "defuddle", "parse", url, "--json", "--markdown"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
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


def extract_youtube_transcript(  # pylint: disable=import-outside-toplevel
    url: str, timeout: int = 60
) -> Optional[dict]:
    """yt-dlp로 YouTube 자막을 추출하고 srt_to_txt.sh로 정리"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1) 자막 목록 확인 → 수동 자막 시도 → 자동 자막 폴백
        subs_info = subprocess.run(
            ["yt-dlp", "--list-subs", "--skip-download", url],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        # 언어 우선순위 결정
        info = subs_info.stdout + subs_info.stderr
        if "en-orig" in info:
            lang = "en-orig,en,ko"
        elif "ko-orig" in info:
            lang = "ko-orig,ko,en"
        else:
            lang = "en,ko"

        # 수동 자막 시도
        subprocess.run(
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
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        # 수동 자막 파일 찾기
        srt_files = list(Path(tmpdir).glob("*.srt"))

        # 없으면 자동 자막 폴백
        if not srt_files:
            subprocess.run(
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
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            srt_files = list(Path(tmpdir).glob("*.srt"))

        if not srt_files:
            return None

        srt_file = srt_files[0]
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
        url = item["url"]
        title_short = item["title"][:50]
        print(f"  [{i + 1}/{len(targets)}] {title_short}...")

        # YouTube 영상: yt-dlp로 자막 추출
        if item["platform"].startswith("youtube/"):
            try:
                data = extract_youtube_transcript(url)
                if data:
                    item["content_markdown"] = data["content_markdown"]
                    item["word_count"] = data["word_count"]
                    item["subtitle_lang"] = data.get("subtitle_lang", "")
                    print(
                        f"    -> 자막: {data['word_count']} words ({data.get('subtitle_lang', '')})"
                    )
                else:
                    item["content_markdown"] = ""
                    item["word_count"] = 0
                    print("    -> 자막 없음")
            except Exception as e:
                item["content_markdown"] = ""
                item["word_count"] = 0
                print(f"    [!] 자막 추출 실패: {e}")
            continue

        # GeekNews: 토픽 페이지에서 원문 URL을 추출한 뒤 원문에 defuddle
        if item["platform"] == "geeknews" and "news.hada.io/topic" in url:
            original_url = resolve_geeknews_original_url(url)
            if original_url:
                item["original_url"] = original_url
                print(f"    -> 원문: {original_url[:60]}")
                data = defuddle(original_url)
            else:
                data = defuddle(url)
        else:
            data = defuddle(url)

        if data:
            item["content_markdown"] = data["content_markdown"]
            item["word_count"] = data["word_count"]
            item["description"] = data.get("description", "")
            item["image"] = data.get("image", "")
        else:
            item["content_markdown"] = ""
            item["word_count"] = 0

    extracted = sum(1 for it in targets if it.get("word_count", 0) > 0)
    print(f"  -> {extracted}/{len(targets)}개 콘텐츠 추출 성공")
    return items


def enrich_papers_with_content(items: List[dict]) -> List[dict]:
    """논문 항목에 arXiv HTML 전문을 defuddle로 추출"""
    targets = list(items)
    if not targets:
        return items

    print(f"\n[논문 전문] {len(targets)}개 논문의 전문을 추출합니다...")

    for i, item in enumerate(targets):
        title_short = item["title"][:50]
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
            item["content_markdown"] = ""
            item["word_count"] = 0
            print("    -> HTML 버전 없음 (abstract만 저장)")

    extracted = sum(1 for it in targets if it.get("word_count", 0) > 0)
    print(f"  -> {extracted}/{len(targets)}개 전문 추출 성공")
    return items
