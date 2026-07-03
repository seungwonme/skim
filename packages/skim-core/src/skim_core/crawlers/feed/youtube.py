"""
@file youtube.py
@description YouTube 채널 크롤러 (RSS → yt-dlp fallback + transcript)
"""

import json
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from ...db import get_connection
from ...enrichment import enrich_with_content
from ...feed_config import YOUTUBE_CHANNELS
from ...feed_utils import fetch_feed
from ...models import Post


def _drop_known_urls(items: List[dict]) -> List[dict]:
    """yt-dlp fallback은 날짜 필터가 불가능해 매번 같은 최신 영상을 되돌려준다.
    이미 저장된 영상은 제외해 transcript 재추출 낭비를 막는다."""
    if not items:
        return items
    try:
        conn = get_connection()
        placeholders = ",".join("?" for _ in items)
        rows = conn.execute(
            f"SELECT url FROM posts WHERE platform='youtube' AND url IN ({placeholders})",
            [it.get("url", "") for it in items],
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return items
    known = {r["url"] for r in rows}
    return [it for it in items if it.get("url", "") not in known]


_VIDEO_ID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/|/embed/)([\w-]{11})")


def youtube_video_id(url: str) -> Optional[str]:
    """URL에서 YouTube video ID를 추출한다. 제목이 바뀌어도 불변인 정체성."""
    match = _VIDEO_ID_RE.search(url or "")
    return match.group(1) if match else None


def _item_to_post(item: dict) -> Post:
    """피드 항목을 Post 객체로 변환"""
    return Post(
        platform="youtube",
        author=item.get("author", ""),
        title=item.get("title", ""),
        content="",
        timestamp=item.get("published", ""),
        url=item.get("url", ""),
        summary=item.get("summary", ""),
        source=item.get("platform", ""),
        content_markdown=item.get("content_markdown", ""),
        word_count=item.get("word_count"),
        external_id=youtube_video_id(item.get("url", "")),
    )


def _fetch_via_ytdlp(  # pylint: disable=unused-argument
    channel_name: str, channel_id: str, since: datetime
) -> List[dict]:
    """yt-dlp --flat-playlist로 채널 최신 영상 목록을 가져옵니다 (RSS fallback).

    --flat-playlist는 빠르지만 upload_date가 없어 날짜 필터링이 불가.
    최근 3개만 가져와서 DB 중복 제거에 의존합니다.
    대부분의 채널은 하루 0~1개 업로드하므로 3개면 충분.
    """
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--playlist-items=1-3",
                "--dump-json",
                f"https://www.youtube.com/channel/{channel_id}/videos",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return []
    except Exception:
        return []

    items = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue

        vid_id = d.get("id", "")
        url = d.get("url") or f"https://www.youtube.com/watch?v={vid_id}"

        # Shorts 제외
        if "/shorts/" in url or "/shorts/" in vid_id:
            continue

        items.append(
            {
                "platform": f"youtube/{channel_name}",
                "title": d.get("title", ""),
                "url": url,
                "author": channel_name,
                "published": "",
                "summary": d.get("description", "")[:300] if d.get("description") else "",
            }
        )

    return items


class YouTubeCrawler:
    """YouTube 채널 크롤러 (RSS → yt-dlp fallback)"""

    platform: str = "youtube"

    async def crawl(self, **options: Any) -> List[Post]:
        """
        YouTube 채널 피드를 수집합니다.
        RSS 실패 시 yt-dlp로 fallback합니다.

        Options:
            since (datetime): 이 시점 이후의 영상만 수집
            no_content (bool): transcript 추출 스킵
            debug (bool): 디버그 모드
        """
        since: datetime = options.get(
            "since",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        no_content: bool = options.get("no_content", False)
        debug: bool = options.get("debug", False)

        all_items: List[dict] = []
        rss_failed = 0

        for name, channel_id in YOUTUBE_CHANNELS.items():
            # 1차: RSS (quiet=True로 에러 메시지 억제)
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            results = fetch_feed(url, f"youtube/{name}", since, quiet=True)
            longform = [r for r in results if "/shorts/" not in r.get("url", "")]

            # 2차: RSS 실패 시 yt-dlp fallback
            if not results:
                rss_failed += 1
                if rss_failed == 1 and debug:
                    print("  RSS 실패 — yt-dlp fallback 사용")
                longform = _drop_known_urls(_fetch_via_ytdlp(name, channel_id, since))

            if longform:
                print(f"  -> {name}: {len(longform)}개 새 영상")
            all_items.extend(longform)
            time.sleep(0.3)

        if rss_failed > 0:
            print(f"  (RSS {rss_failed}/{len(YOUTUBE_CHANNELS)}개 실패, yt-dlp fallback 사용)")

        print(f"  -> 총 {len(all_items)}개 영상 (롱폼만)")

        if not all_items:
            return []

        all_items.sort(key=lambda x: x.get("published", ""), reverse=True)

        if not no_content:
            enrich_with_content(all_items)

        return [_item_to_post(item) for item in all_items]
