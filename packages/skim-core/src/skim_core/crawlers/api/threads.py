"""
@file threads_api.py
@description Threads API 기반 크롤러 (브라우저 없이 동작)

Instagram Private API를 사용하여 Threads 게시글을 수집합니다.
CDP로 추출한 세션 쿠키를 재사용하여 인증합니다.

주요 기능:
1. 브라우저 없이 HTTP 요청으로 Threads 피드 수집
2. For You 타임라인 피드 + 사용자 프로필 피드 지원
3. 스레드(self-reply chain) 내용 합치기
4. 페이지네이션을 통한 대량 수집

@dependencies
- requests: HTTP 클라이언트
- typer: CLI 출력
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import typer

from ...models import Post
from ...paths import SESSIONS_DIR

# Instagram Private API 설정
IG_API_BASE = "https://i.instagram.com/api/v1"
IG_APP_ID = "238260118697367"
IG_USER_AGENT = "Barcelona 289.0.0.77.109 Android"


class ThreadsAPICrawler:
    """
    Threads API 기반 크롤러

    Instagram Private API를 사용하여 브라우저 없이 Threads 게시글을 수집합니다.
    CDP로 추출한 세션 쿠키를 재사용합니다.
    """

    platform = "threads"

    def __init__(self, debug_mode: bool = False):
        self.platform_name = "Threads"
        self.debug_mode = debug_mode
        self.session_path = SESSIONS_DIR / "threads_session.json"
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self) -> None:
        """세션 쿠키 로드 및 HTTP 세션 설정"""
        cookies = self._load_cookies()
        if not cookies:
            typer.echo("세션 쿠키가 없습니다. 먼저 로그인하세요:")
            typer.echo("  uv run skim login threads")
            raise typer.Exit(1)

        self.my_user_id = cookies.get("ds_user_id")

        self.session.cookies.update(cookies)
        self.session.headers.update(
            {
                "User-Agent": IG_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "*/*",
            }
        )

        if self.debug_mode:
            typer.echo(f"쿠키 {len(cookies)}개 로드됨")
            typer.echo(f"내 user_id: {self.my_user_id}")

    def _load_cookies(self) -> Dict[str, str]:
        """세션 파일에서 쿠키 로드"""
        if not self.session_path.exists():
            return {}

        with open(self.session_path, "r", encoding="utf-8") as f:
            storage_state = json.load(f)

        cookies = {}
        for cookie in storage_state.get("cookies", []):
            domain = cookie.get("domain", "")
            if "threads" in domain or "instagram" in domain:
                cookies[cookie["name"]] = cookie["value"]

        return cookies

    async def crawl(self, **options) -> List[Post]:
        count = options.get("count", 5)
        user_id = options.get("user_id")
        return await self._crawl_impl(count, user_id)

    async def _crawl_impl(self, count: int = 5, user_id: Optional[str] = None) -> List[Post]:
        """
        Threads 게시글 크롤링

        각 스레드의 self-reply chain을 하나의 Post로 합칩니다.

        Args:
            count: 수집할 게시글(스레드) 수
            user_id: 특정 사용자 ID (없으면 For You 타임라인)

        Returns:
            크롤링된 게시글 목록
        """
        mode = f"사용자 {user_id}" if user_id else "For You 타임라인"
        typer.echo(f"[API 모드] {self.platform_name} 크롤링 시작 - {mode} (게시글 {count}개)")

        posts: List[Post] = []
        max_id: Optional[str] = None

        # 파싱 불가 스레드만 이어질 때 피드 끝까지 무한정 넘기지 않도록 페이지 상한을 둔다.
        max_pages = 10
        for _ in range(max_pages):
            if len(posts) >= count:
                break
            threads, max_id = self._fetch_feed(user_id=user_id, max_id=max_id)

            if not threads:
                if self.debug_mode:
                    typer.echo("  더 이상 게시글이 없습니다")
                break

            for thread in threads:
                post = self._parse_thread(thread)
                if post:
                    posts.append(post)
                    if self.debug_mode:
                        typer.echo(f"  @{post.author}: {post.content[:60]}...")
                    if len(posts) >= count:
                        break

            if not max_id:
                break

        typer.echo(f"총 {len(posts)}개의 게시글을 추출했습니다.")
        return posts

    def _fetch_feed(
        self,
        user_id: Optional[str] = None,
        max_id: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """피드 데이터 가져오기"""
        try:
            if user_id:
                return self._fetch_user_feed(user_id, max_id)
            else:
                return self._fetch_timeline_feed(max_id)
        except requests.RequestException as e:
            typer.echo(f"API 요청 실패: {e}")
            return [], None

    def _fetch_timeline_feed(
        self, max_id: Optional[str] = None
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """For You 타임라인 피드 (POST 요청)"""
        url = f"{IG_API_BASE}/feed/text_post_app_timeline/"

        data = {"pagination_source": "text_post_feed_threads"}
        if max_id:
            data["max_id"] = max_id

        resp = self.session.post(url, data=data, timeout=15)

        if resp.status_code == 401:
            typer.echo("세션이 만료되었습니다. 재로그인하세요:")
            typer.echo("  uv run skim login threads")
            return [], None

        if resp.status_code != 200:
            # rate limit/서버 오류를 정상 빈 피드처럼 숨기지 않는다.
            typer.echo(f"  [!] 타임라인 API 오류: HTTP {resp.status_code}")
            return [], None

        result = resp.json()

        # 타임라인 응답: feed_items → text_post_app_thread → thread_items
        feed_items = result.get("feed_items", [])
        threads = []
        for item in feed_items:
            thread = item.get("text_post_app_thread")
            if thread:
                threads.append(thread)

        next_max_id = result.get("next_max_id")

        if self.debug_mode:
            typer.echo(
                f"  타임라인: {len(threads)}개 스레드 수신 "
                f"(next_max_id: {'있음' if next_max_id else '없음'})"
            )

        return threads, next_max_id

    def _fetch_user_feed(
        self, user_id: str, max_id: Optional[str] = None
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """특정 사용자 프로필 피드 (GET 요청)"""
        url = f"{IG_API_BASE}/text_feed/{user_id}/profile/"

        params = {}
        if max_id:
            params["max_id"] = max_id

        resp = self.session.get(url, params=params, timeout=15)

        if resp.status_code == 401:
            typer.echo("세션이 만료되었습니다. 재로그인하세요:")
            typer.echo("  uv run skim login threads")
            return [], None

        if resp.status_code != 200:
            # rate limit/서버 오류를 정상 빈 피드처럼 숨기지 않는다.
            typer.echo(f"  [!] 사용자 피드 API 오류: HTTP {resp.status_code}")
            return [], None

        data = resp.json()
        threads = data.get("threads", [])
        next_max_id = data.get("next_max_id")

        if self.debug_mode:
            typer.echo(
                f"  사용자 피드: {len(threads)}개 스레드 수신 "
                f"(next_max_id: {'있음' if next_max_id else '없음'})"
            )

        return threads, next_max_id

    def _parse_thread(self, thread: Dict[str, Any]) -> Optional[Post]:
        """
        스레드 전체를 하나의 Post로 파싱

        같은 작성자의 self-reply chain을 합쳐서 하나의 Post로 반환.
        """
        thread_items = thread.get("thread_items", [])
        if not thread_items:
            return None

        # 첫 번째 아이템에서 메타데이터 추출
        first_post = thread_items[0].get("post", {})
        if not first_post:
            return None

        user = first_post.get("user", {})
        author = user.get("username", "Unknown")

        # 같은 작성자의 self-reply chain 내용 합치기
        contents = []
        for item in thread_items:
            post_data = item.get("post", {})
            item_user = post_data.get("user", {})
            # 다른 작성자의 답글은 제외 (self-reply만 합침)
            if item_user.get("username") != author:
                continue
            caption = post_data.get("caption")
            text = caption.get("text", "") if caption else ""
            if text:
                contents.append(text)

        if not contents:
            return None

        # 여러 self-reply를 구분자로 합침
        content = "\n\n---\n\n".join(contents) if len(contents) > 1 else contents[0]

        # 타임스탬프 (첫 번째 포스트 기준)
        taken_at = first_post.get("taken_at", 0)
        timestamp = (
            datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat(timespec="seconds")
            if taken_at
            else ""
        )

        # URL (첫 번째 포스트 기준)
        code = first_post.get("code", "")
        url = f"https://www.threads.net/@{author}/post/{code}" if code else None
        external_id = code or first_post.get("pk") or first_post.get("id")

        # 상호작용 (첫 번째 포스트 기준)
        text_post_info = first_post.get("text_post_app_info", {})
        like_count = first_post.get("like_count", 0)
        reply_count = text_post_info.get("direct_reply_count", 0) if text_post_info else 0
        repost_count = text_post_info.get("repost_count", 0) if text_post_info else 0

        return Post(
            platform="threads",
            author=author,
            content=content,
            timestamp=timestamp,
            url=url,
            likes=like_count,
            comments=reply_count,
            reposts=repost_count,
            external_id=str(external_id) if external_id else None,
        )
