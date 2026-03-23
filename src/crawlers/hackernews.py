"""
@file hackernews.py
@description Hacker News 크롤러 (공개 API 사용, Playwright 불필요)
"""

from typing import List

import requests
import typer

from ..models import Post

HN_API_BASE = "https://hacker-news.firebaseio.com/v0"


def fetch_hackernews(count: int = 10) -> List[Post]:
    """
    Hacker News Top Stories를 가져옵니다.

    Args:
        count: 가져올 게시글 수

    Returns:
        Post 리스트
    """
    typer.echo(f"🔄 Hacker News에서 상위 {count}개 스토리를 가져옵니다...")

    # 1. Top story IDs
    resp = requests.get(f"{HN_API_BASE}/topstories.json", timeout=10)
    resp.raise_for_status()
    story_ids = resp.json()[:count]

    # 2. 각 스토리 상세 정보
    posts: List[Post] = []
    for i, story_id in enumerate(story_ids):
        try:
            item_resp = requests.get(f"{HN_API_BASE}/item/{story_id}.json", timeout=10)
            item_resp.raise_for_status()
            item = item_resp.json()

            if not item or item.get("type") != "story":
                continue

            post = Post(
                platform="hackernews",
                author=item.get("by", "unknown"),
                content=item.get("title", ""),
                timestamp=str(item.get("time", "")),
                url=item.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                likes=item.get("score", 0),
                comments=item.get("descendants", 0),
            )
            posts.append(post)
            typer.echo(f"   [{i + 1}/{count}] {post.content[:60]}")
        except Exception as e:
            typer.echo(f"   [{i + 1}/{count}] 스킵 (에러: {e})")

    return posts
