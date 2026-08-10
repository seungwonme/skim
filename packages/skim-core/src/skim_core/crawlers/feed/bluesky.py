"""
@file bluesky.py
@description Bluesky 크롤러 (무인증 공개 XRPC)

threads/x/linkedin과 달리 로그인이 필요 없다. `public.api.bsky.app`이 무인증으로
authorFeed와 postThread를 내주므로 계정이 노출되지 않는다. 대신 계정 팔로우가
소스 목록을 소유하지 않으므로 볼 계정을 `BLUESKY_ACCOUNTS`에서 관리한다
(searchPosts는 403이라 키워드 수집은 못 한다).
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import typer

from ...comments import Comment, append_comment_section, render_comment_section
from ...feed_config import (
    BLUESKY_ACCOUNTS,
    BLUESKY_AUTHOR_FEED_URL,
    BLUESKY_POST_THREAD_URL,
)
from ...feed_utils import make_retrying_session
from ...models import Post

MAX_COMMENTS = 15
MIN_REPLIES_FOR_FETCH = 1
REQUEST_INTERVAL_SECONDS = 0.5

_SESSION = make_retrying_session({"Accept": "application/json"})


def _parse_created(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def post_web_url(uri: str, handle: str) -> str:
    """at:// URI를 사람이 여는 주소로 바꾼다.

    at://did:plc:xxx/app.bsky.feed.post/3mse... -> https://bsky.app/profile/{handle}/post/3mse...
    """
    rkey = (uri or "").rsplit("/", 1)[-1]
    if not rkey or not handle:
        return ""
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


class BlueskyCrawler:
    """Bluesky 공개 API 크롤러."""

    platform = "bluesky"

    async def crawl(self, **options: Any) -> List[Post]:
        since: datetime = options.get(
            "since", datetime.now(timezone.utc) - timedelta(days=1)
        )
        count = options.get("count")
        no_content: bool = options.get("no_content", False)

        accounts = options.get("accounts") or BLUESKY_ACCOUNTS
        posts: List[Post] = []
        for index, actor in enumerate(accounts):
            if index:
                time.sleep(REQUEST_INTERVAL_SECONDS)
            try:
                posts.extend(self.fetch_author_feed(actor, since))
            except Exception as exc:  # noqa: BLE001 - 한 계정 실패가 나머지를 막지 않는다
                typer.echo(f"   [!] Bluesky {actor} 수집 실패: {exc}")

        posts.sort(key=lambda p: p.timestamp, reverse=True)
        if count is not None:
            posts = posts[:count]

        if not no_content:
            self.attach_replies(posts)
        return posts

    def fetch_author_feed(self, actor: str, since: datetime) -> List[Post]:
        """한 계정의 최근 게시물. 리포스트는 제외한다."""
        resp = _SESSION.get(
            BLUESKY_AUTHOR_FEED_URL,
            params={"actor": actor, "limit": 50, "filter": "posts_no_replies"},
            timeout=20,
        )
        resp.raise_for_status()
        feed = resp.json().get("feed") or []

        results: List[Post] = []
        for entry in feed:
            # reason이 있으면 남의 글을 올린 리포스트다. 원저자 타임라인에서 받는다.
            if entry.get("reason"):
                continue
            post = self._entry_to_post(entry, actor)
            if post is None:
                continue
            created = _parse_created(post.timestamp)
            if created and created < since:
                continue
            results.append(post)
        return results

    def attach_replies(self, posts: List[Post]) -> None:
        """답글을 정본 본문 뒤에 잇는다. 게시물당 요청 1건."""
        failures = 0
        for index, post in enumerate(posts):
            if (post.comments or 0) < MIN_REPLIES_FOR_FETCH:
                continue
            if index:
                time.sleep(REQUEST_INTERVAL_SECONDS)
            uri = getattr(post, "at_uri", "")
            if not uri:
                continue
            try:
                section = self.fetch_reply_section(uri)
            except Exception as exc:  # noqa: BLE001 - 답글 실패가 게시물 저장을 막지 않는다
                failures += 1
                typer.echo(f"   [!] Bluesky 답글 수집 실패: {exc}")
                continue
            if section:
                post.content_markdown = append_comment_section(
                    post.content_markdown or post.content, section
                )

        if failures:
            typer.echo(f"   [!] Bluesky 답글 수집 실패 {failures}건 (본문만 저장)")

    def fetch_reply_section(self, uri: str) -> Optional[str]:
        """postThread에서 직계 답글만 뽑는다. 작성자 self-thread는 건너뛴다."""
        resp = _SESSION.get(
            BLUESKY_POST_THREAD_URL,
            params={"uri": uri, "depth": 1},
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        thread = resp.json().get("thread") or {}
        root_handle = ((thread.get("post") or {}).get("author") or {}).get(
            "handle"
        ) or ""

        collected: List[Comment] = []
        for reply in thread.get("replies") or []:
            reply_post = reply.get("post") or {}
            author = (reply_post.get("author") or {}).get("handle") or "unknown"
            # 작성자 self-reply 연작은 이미 본문에 담기는 흐름이라 뺀다.
            if author and author == root_handle:
                continue
            text = ((reply_post.get("record") or {}).get("text") or "").strip()
            if not text:
                continue
            created = (reply_post.get("record") or {}).get("createdAt") or ""
            collected.append(
                Comment(
                    author=f"@{author}",
                    text=text,
                    score=reply_post.get("likeCount"),
                    created=created[:16].replace("T", " ") or None,
                )
            )

        return render_comment_section(
            "Bluesky Replies", collected, max_comments=MAX_COMMENTS, score_unit="like"
        )

    def _entry_to_post(self, entry: dict, source: str) -> Optional[Post]:
        raw = entry.get("post") or {}
        record = raw.get("record") or {}
        text = (record.get("text") or "").strip()
        if not text:
            return None
        author = raw.get("author") or {}
        handle = author.get("handle") or ""
        created = _parse_created(record.get("createdAt", ""))

        return Post(
            platform=self.platform,
            author=author.get("displayName") or handle or "unknown",
            content=text,
            content_markdown=text,
            timestamp=created.astimezone(timezone.utc).isoformat() if created else "",
            url=post_web_url(raw.get("uri", ""), handle),
            likes=raw.get("likeCount"),
            comments=raw.get("replyCount"),
            reposts=raw.get("repostCount"),
            source=source,
            external_id=raw.get("uri", "") or None,
            at_uri=raw.get("uri", ""),
        )
