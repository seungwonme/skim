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
from typing import Any, List, Optional, Tuple

from ...db import get_connection
from ...enrichment import enrich_with_content
from ...feed_config import YOUTUBE_CHANNELS, youtube_videos_url
from ...feed_utils import fetch_feed
from ...models import Post
from ...youtube_history import normalize_tracked_channels


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


def tracked_youtube_channels() -> List[Tuple[str, str]]:
    """수집 대상 채널 목록 (display_name, canonical_id).

    구독의 정본은 tracked_sources다. 데스크톱에서 채널을 추가해도 크롤러가
    feed_config만 보면 그 채널은 데일리 수집에서 영영 빠진다. DB를 못 읽거나
    비어 있을 때만 feed_config 기본 목록으로 폴백한다.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT display_name, canonical_id FROM tracked_sources "
            "WHERE platform='youtube' AND source_type='channel' AND is_enabled=1 "
            "ORDER BY display_name COLLATE NOCASE"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return list(YOUTUBE_CHANNELS.items())

    channels = [(r["display_name"], r["canonical_id"]) for r in rows]
    return channels or list(YOUTUBE_CHANNELS.items())


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
        views=item.get("views"),
        external_id=youtube_video_id(item.get("url", "")),
    )


def _fetch_via_ytdlp(  # pylint: disable=unused-argument
    channel_name: str, channel_id: str, since: datetime
) -> List[dict]:
    """yt-dlp --flat-playlist로 채널 최신 영상 목록을 가져옵니다 (RSS fallback).

    최근 3개만 가져와서 DB 중복 제거에 의존합니다.
    대부분의 채널은 하루 0~1개 업로드하므로 3개면 충분.

    approximate_date를 켜면 flat-playlist에서도 발행 시각이 붙는다. 이게 없으면
    published가 빈 문자열로 저장돼 읽기 쪽이 crawled_at으로 폴백한다. 핸들 구독은
    RSS가 없어 항상 이 경로라 timestamp 공백이 매일 쌓인다.
    since는 여기서 거르지 않는다. 수집이 며칠 밀렸을 때 그 구간을 통째로
    놓치기 때문이고, 중복은 _drop_known_urls가 잡는다.
    """
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--extractor-args",
                "youtubetab:approximate_date",
                "--playlist-items=1-3",
                "--dump-json",
                youtube_videos_url(channel_id),
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

        ts = d.get("timestamp")
        items.append(
            {
                "platform": f"youtube/{channel_name}",
                "title": d.get("title", ""),
                "url": url,
                "author": channel_name,
                "published": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else "",
                "summary": d.get("description", "")[:300] if d.get("description") else "",
                # flat-playlist도 view_count는 준다. 추가 요청 없이 조회수가 확보된다.
                "views": d.get("view_count"),
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
        handle_only = 0
        # 핸들과 채널 ID로 이중 등록된 구독은 같은 채널을 두 번 크롤한다.
        # 매 수집 앞에서 하나로 모은다 (핸들 행이 없으면 아무 일도 하지 않는다).
        normalize_tracked_channels()
        channels = tracked_youtube_channels()

        for name, channel_id in channels:
            results: List[dict] = []
            longform: List[dict] = []

            # 1차: RSS (quiet=True로 에러 메시지 억제).
            # 핸들 구독은 channel_id가 없어 RSS 주소를 만들 수 없다.
            is_handle = channel_id.startswith("@")
            if not is_handle:
                url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
                results = fetch_feed(url, f"youtube/{name}", since, quiet=True)
                longform = [r for r in results if "/shorts/" not in r.get("url", "")]

            # 2차: RSS 실패(또는 핸들) 시 yt-dlp fallback
            if not results:
                if is_handle:
                    handle_only += 1
                else:
                    rss_failed += 1
                    if rss_failed == 1 and debug:
                        print("  RSS 실패 - yt-dlp fallback 사용")
                longform = _drop_known_urls(_fetch_via_ytdlp(name, channel_id, since))

            if longform:
                print(f"  -> {name}: {len(longform)}개 새 영상")
            all_items.extend(longform)
            time.sleep(0.3)

        if rss_failed or handle_only:
            print(
                f"  (yt-dlp 경로 {rss_failed + handle_only}/{len(channels)}개"
                f" - RSS 실패 {rss_failed}, 핸들 구독 {handle_only})"
            )

        print(f"  -> 총 {len(all_items)}개 영상 (롱폼만)")

        if not all_items:
            return []

        all_items.sort(key=lambda x: x.get("published", ""), reverse=True)

        if not no_content:
            enrich_with_content(all_items)

        return [_item_to_post(item) for item in all_items]
