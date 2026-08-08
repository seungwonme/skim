"""
@file youtube_history.py
@description 구독 채널의 과거 영상(롱폼) 목록 백필 + 개별 영상 자막 전사

/videos 탭 flat-playlist enumerate라 Shorts는 애초에 제외된다.
목록 행은 본문 없이 저장되고(임베드용 메타데이터만), 자막은 사용자가
요청할 때 transcribe_video()로 채운다 — 데이터 계약의 명시적 예외.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import typer

from .db import get_connection, save_posts
from .enrichment import extract_youtube_transcript
from .feed_config import youtube_videos_url
from .models import Post

# 채널당 연간 300개면 데일리 업로더도 덮는다. 그 이상은 enumerate가 무한정 길어진다.
MAX_ITEMS_PER_YEAR = 300


def list_channel_videos(channel_id: str, channel_name: str, years: int = 1) -> List[Post]:
    """yt-dlp flat-playlist로 채널 /videos 탭에서 최근 N년 영상 목록을 가져온다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=365 * years)
    result = subprocess.run(
        [
            "yt-dlp",
            "--flat-playlist",
            "--extractor-args",
            "youtubetab:approximate_date",
            "--playlist-items",
            f"1-{MAX_ITEMS_PER_YEAR * years}",
            "--print",
            "%(.{id,timestamp,duration,title})j",
            youtube_videos_url(channel_id),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{channel_name}: enumerate 실패 - {result.stderr.strip()[:200]}")

    posts: List[Post] = []
    for line in result.stdout.strip().splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = data.get("id") or ""
        ts = data.get("timestamp")
        if not video_id or not ts:
            continue
        published = datetime.fromtimestamp(ts, tz=timezone.utc)
        if published < cutoff:
            # /videos 탭은 최신순이라 cutoff를 지나면 나머지도 오래된 영상이다.
            break
        posts.append(
            Post(
                platform="youtube",
                author=channel_name,
                title=data.get("title", ""),
                content="",
                timestamp=published.isoformat(),
                url=f"https://www.youtube.com/watch?v={video_id}",
                source=f"youtube/{channel_name}",
                external_id=video_id,
            )
        )
    return posts


def backfill_channel_history(channel: Optional[str], years: int = 1) -> int:
    """tracked_sources의 유튜브 채널(전체 또는 지정 채널) 과거 영상을 DB에 upsert한다."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT display_name, canonical_id FROM tracked_sources "
        "WHERE platform='youtube' AND source_type='channel' AND is_enabled=1"
    ).fetchall()
    conn.close()

    targets = [
        (r["display_name"], r["canonical_id"])
        for r in rows
        if channel is None or channel in (r["display_name"], r["canonical_id"])
    ]
    if not targets:
        raise RuntimeError(f"채널을 찾지 못함: {channel}")

    total = 0
    failures: List[str] = []
    for name, canonical_id in targets:
        typer.echo(f"[{name}] 최근 {years}년 영상 목록 수집...")
        try:
            posts = list_channel_videos(canonical_id, name, years)
        except RuntimeError as exc:
            typer.echo(f"   [!] {exc}")
            failures.append(str(exc))
            continue
        if posts:
            saved = save_posts(posts, "youtube")
            total += saved
            typer.echo(f"   -> {len(posts)}개 중 {saved}개 신규/보강")

    # 한 건도 못 건졌는데 실패가 있으면 조용한 0이 아니라 에러로 올린다.
    # (데스크톱은 종료 코드만 보므로 exit 0이면 "수집 완료"로 오인한다)
    if failures and total == 0:
        raise RuntimeError("; ".join(failures))
    return total


def transcribe_video(url_or_id: str) -> bool:
    """영상 하나의 자막을 전사해 해당 행의 본문으로 저장한다."""
    video_id = url_or_id.rsplit("v=", 1)[-1].rsplit("/", 1)[-1]
    url = f"https://www.youtube.com/watch?v={video_id}"

    data = extract_youtube_transcript(url)
    if not data or not data.get("content_markdown"):
        typer.echo(f"자막 없음: {url}")
        return False

    conn = get_connection()
    cur = conn.execute(
        """UPDATE posts SET content_markdown=?, word_count=?,
               extra=json_set(COALESCE(extra,'{}'), '$.subtitle_lang', ?)
           WHERE platform='youtube' AND url=?""",
        (
            data["content_markdown"],
            data["word_count"],
            data.get("subtitle_lang", ""),
            url,
        ),
    )
    conn.commit()
    conn.close()
    typer.echo(f"전사 완료: {data['word_count']} words ({data.get('subtitle_lang', '')})")
    return cur.rowcount > 0
