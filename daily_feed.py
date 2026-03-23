# pylint: disable=subprocess-run-check
"""
@file daily_feed.py
@description 일일 RSS 피드 수집 스크립트

매일 자동 실행되어 전날 올라온 게시물을 수집합니다.
각 항목의 원문 콘텐츠를 defuddle로 추출하여 함께 저장합니다.

사용법:
    python daily_feed.py hn                # Hacker News만
    python daily_feed.py gn                # GeekNews만
    python daily_feed.py yt                # YouTube만
    python daily_feed.py all               # 전체 (기본)

    python daily_feed.py hn --days 3       # 최근 3일
    python daily_feed.py gn --dry-run      # 미리보기
    python daily_feed.py hn --no-content   # 콘텐츠 추출 없이
"""

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from feed_config import (
    ARXIV_API_URL,
    EVERY_TO_FEEDS,
    GEEKNEWS_RSS,
    HACKERNEWS_RSS,
    HUGGINGFACE_PAPERS_URL,
    PRODUCTHUNT_RSS,
    YOUTUBE_CHANNELS,
)

KST = timezone(timedelta(hours=9))


# === 공통 유틸 ===


def parse_entry_date(entry) -> Optional[datetime]:
    """피드 엔트리에서 datetime 객체 추출 (UTC 변환)"""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def is_within_range(entry_dt: Optional[datetime], since: datetime) -> bool:
    if not entry_dt:
        return False
    return entry_dt >= since


SRT_TO_TXT = str(Path(__file__).parent / "scripts" / "srt_to_txt.sh")


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


def defuddle(url: str, timeout: int = 15) -> Optional[dict]:
    """defuddle CLI로 URL의 본문 콘텐츠를 추출"""
    try:
        result = subprocess.run(
            ["bunx", "defuddle", "parse", url, "--json", "--markdown"],
            capture_output=True,
            text=True,
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


def fetch_feed(url: str, source_name: str, since: datetime) -> List[dict]:
    """RSS/Atom 피드를 가져와서 since 이후 항목만 반환"""
    feed = feedparser.parse(url)

    if feed.bozo and not feed.entries:
        print(f"  [!] {source_name}: 피드 파싱 실패 - {feed.bozo_exception}")
        return []

    results = []
    for entry in feed.entries:
        entry_dt = parse_entry_date(entry)
        if not is_within_range(entry_dt, since):
            continue

        results.append(
            {
                "platform": source_name,
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "author": (
                    entry.get("author", "")
                    or (
                        entry.get("authors", [{}])[0].get("name", "")
                        if entry.get("authors")
                        else ""
                    )
                ),
                "published": entry_dt.astimezone(KST).isoformat() if entry_dt else "",
                "summary": (
                    re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", entry.get("summary") or "")).strip()[
                        :300
                    ]
                ),
            }
        )

    return results


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


def save_results(items: List[dict], platform: str, date_str: str, dry_run: bool = False):
    """결과를 플랫폼별 JSON 파일로 저장"""
    if dry_run:
        print(f"\n[미리보기] {platform} — {len(items)}개 항목:")
        for item in items:
            wc = item.get("word_count", 0)
            wc_str = f" ({wc} words)" if wc else ""
            print(f"  [{item['platform']}] {item['title'][:60]}{wc_str}")
            print(f"    {item['url']}")
        return

    output_dir = Path(f"data/daily/{platform}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{date_str}.json"

    data = {
        "metadata": {
            "date": date_str,
            "platform": platform,
            "collected_at": datetime.now(KST).isoformat(),
            "total_items": len(items),
        },
        "items": items,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    with_content = sum(1 for it in items if it.get("word_count", 0) > 0)
    total_words = sum(it.get("word_count", 0) for it in items)
    print(f"\n[저장] {output_file}")
    print(f"  - 항목: {len(items)}개")
    if with_content:
        print(f"  - 콘텐츠: {with_content}개 ({total_words:,} words)")


# === 플랫폼별 수집 ===


def run_hackernews(since: datetime, dry_run: bool = False, no_content: bool = False):
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    print("[HN] Hacker News 피드 수집 중...")
    items = fetch_feed(HACKERNEWS_RSS, "hackernews", since)
    print(f"  -> {len(items)}개 항목")
    if not items:
        return
    items.sort(key=lambda x: x.get("published", ""), reverse=True)
    if not no_content and not dry_run:
        enrich_with_content(items)
    save_results(items, "hackernews", date_str, dry_run)


def run_geeknews(since: datetime, dry_run: bool = False, no_content: bool = False):
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    print("[GN] GeekNews 피드 수집 중...")
    items = fetch_feed(GEEKNEWS_RSS, "geeknews", since)
    print(f"  -> {len(items)}개 항목")
    if not items:
        return
    items.sort(key=lambda x: x.get("published", ""), reverse=True)
    if not no_content and not dry_run:
        enrich_with_content(items)
    save_results(items, "geeknews", date_str, dry_run)


def run_youtube(since: datetime, dry_run: bool = False, no_content: bool = False):
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    print(f"[YT] YouTube {len(YOUTUBE_CHANNELS)}개 채널 피드 수집 중...")
    all_items = []

    for name, channel_id in YOUTUBE_CHANNELS.items():
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        results = fetch_feed(url, f"youtube/{name}", since)
        # Shorts 제외 (롱폼만 수집)
        longform = [r for r in results if "/shorts/" not in r.get("url", "")]
        if longform:
            print(f"  -> {name}: {len(longform)}개 새 영상")
        all_items.extend(longform)
        time.sleep(0.3)

    print(f"  -> 총 {len(all_items)}개 영상 (롱폼만)")
    if not all_items:
        return
    all_items.sort(key=lambda x: x.get("published", ""), reverse=True)
    if not no_content and not dry_run:
        enrich_with_content(all_items)
    save_results(all_items, "youtube", date_str, dry_run)


def run_producthunt(since: datetime, dry_run: bool = False, no_content: bool = False):
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    print("[PH] Product Hunt 피드 수집 중...")
    items = fetch_feed(PRODUCTHUNT_RSS, "producthunt", since)
    print(f"  -> {len(items)}개 항목")
    if not items:
        return
    items.sort(key=lambda x: x.get("published", ""), reverse=True)
    if not no_content and not dry_run:
        enrich_with_content(items)
    save_results(items, "producthunt", date_str, dry_run)


def run_everyto(since: datetime, dry_run: bool = False, no_content: bool = False):
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    print(f"[Every] Every.to {len(EVERY_TO_FEEDS)}개 칼럼 피드 수집 중...")
    all_items = []

    for name, feed_url in EVERY_TO_FEEDS.items():
        results = fetch_feed(feed_url, f"every.to/{name}", since)
        if results:
            print(f"  -> {name}: {len(results)}개")
        all_items.extend(results)

    print(f"  -> 총 {len(all_items)}개 글")
    if not all_items:
        return
    all_items.sort(key=lambda x: x.get("published", ""), reverse=True)
    if not no_content and not dry_run:
        enrich_with_content(all_items)
    save_results(all_items, "everyto", date_str, dry_run)


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


def run_arxiv(since: datetime, dry_run: bool = False, no_content: bool = False):
    """arXiv cs.AI 논문 수집 (Atom API)"""
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    print("[arXiv] cs.AI 논문 수집 중...")
    feed = feedparser.parse(ARXIV_API_URL)

    items = []
    for entry in feed.entries:
        # arXiv published 형식: 2026-03-21T17:59:59Z
        pub = entry.get("published", "")
        try:
            entry_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if not is_within_range(entry_dt, since):
            continue

        authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
        items.append(
            {
                "platform": "arxiv",
                "title": re.sub(r"\s+", " ", entry.get("title", "")).strip(),
                "url": entry.get("link", ""),
                "author": authors,
                "published": entry_dt.astimezone(KST).isoformat(),
                "summary": re.sub(r"\s+", " ", entry.get("summary", "")).strip()[:500],
            }
        )

    print(f"  -> {len(items)}개 논문")
    if not items:
        return
    items.sort(key=lambda x: x.get("published", ""), reverse=True)
    if not no_content and not dry_run:
        enrich_papers_with_content(items)
    save_results(items, "arxiv", date_str, dry_run)


def run_huggingface(  # pylint: disable=unused-argument
    since: datetime, dry_run: bool = False, no_content: bool = False
):
    """HuggingFace Daily Papers 수집 (JSON API) — 매일 큐레이션된 목록 전체를 가져옴"""
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    print("[HF] HuggingFace Daily Papers 수집 중...")

    try:
        resp = requests.get(HUGGINGFACE_PAPERS_URL, timeout=15)
        resp.raise_for_status()
        papers = resp.json()
    except Exception as e:
        print(f"  [!] API 요청 실패: {e}")
        return

    items = []
    now = datetime.now(KST)
    for p in papers:
        paper_id = p.get("paper", {}).get("id", "")
        authors = ", ".join(a.get("name", "") for a in p.get("paper", {}).get("authors", [])[:5])
        if len(p.get("paper", {}).get("authors", [])) > 5:
            authors += " et al."

        pub = p.get("publishedAt", "")
        try:
            entry_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            published = entry_dt.astimezone(KST).isoformat()
        except (ValueError, AttributeError):
            published = now.isoformat()

        items.append(
            {
                "platform": "huggingface",
                "title": p.get("title", ""),
                "url": f"https://huggingface.co/papers/{paper_id}" if paper_id else "",
                "author": authors,
                "published": published,
                "summary": re.sub(r"\s+", " ", p.get("summary", "")).strip()[:500],
                "thumbnail": p.get("thumbnail", ""),
                "num_comments": p.get("numComments", 0),
            }
        )

    print(f"  -> {len(items)}개 논문")
    if not items:
        return
    if not no_content and not dry_run:
        enrich_papers_with_content(items)
    save_results(items, "huggingface", date_str, dry_run)


# === CLI ===

PLATFORMS = {
    "hn": ("Hacker News", run_hackernews),
    "gn": ("GeekNews", run_geeknews),
    "yt": ("YouTube", run_youtube),
    "ph": ("Product Hunt", run_producthunt),
    "every": ("Every.to", run_everyto),
    "arxiv": ("arXiv cs.AI", run_arxiv),
    "hf": ("HuggingFace Papers", run_huggingface),
}


def main():
    parser = argparse.ArgumentParser(description="일일 RSS 피드 수집")
    parser.add_argument(
        "platform",
        nargs="?",
        default="all",
        choices=["hn", "gn", "yt", "ph", "every", "arxiv", "hf", "all"],
        help="hn/gn/yt/ph/every/arxiv/hf/all",
    )
    parser.add_argument("--days", type=int, default=1, help="수집할 일수 (기본: 1 = 어제)")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 미리보기")
    parser.add_argument("--no-content", action="store_true", help="콘텐츠 추출 없이 제목만 수집")
    args = parser.parse_args()

    now = datetime.now(KST)
    since = (now - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0)

    print(
        f"=== 일일 피드 수집 ({since.strftime('%m/%d %H:%M')} ~ {now.strftime('%m/%d %H:%M')} KST) ===\n"
    )

    if args.platform == "all":
        targets = list(PLATFORMS.keys())
    else:
        targets = [args.platform]

    for key in targets:
        runner = PLATFORMS[key][1]
        runner(since, dry_run=args.dry_run, no_content=args.no_content)
        if key != targets[-1]:
            print()


if __name__ == "__main__":
    main()
