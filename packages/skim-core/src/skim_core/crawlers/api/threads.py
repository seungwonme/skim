"""
@file threads_api.py
@description Threads API 기반 크롤러 (브라우저 없이 동작)

Threads 웹앱이 쓰는 GraphQL persisted query를 그대로 호출합니다.
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
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import typer

from ...models import Post
from ...paths import SESSIONS_DIR

# Threads 웹 GraphQL 설정
THREADS_BASE = "https://www.threads.com"
GRAPHQL_URL = f"{THREADS_BASE}/graphql/query"
IG_APP_ID = "238260118697367"
WEB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

# persisted query 좌표. Meta가 웹앱을 재배포하면 doc_id가 바뀌어 execution error가 난다.
# 갱신하려면 브라우저로 threads.com을 열어 graphql/query 요청의 doc_id를 다시 읽는다.
TIMELINE_QUERY = {
    "doc_id": "29069292379337431",
    "friendly_name": "BarcelonaFeedPaginationDirectQuery",
    "root_field": "xdt_api__v1__feed__text_post_app_timeline__connection",
}
PROFILE_QUERY = {
    "doc_id": "27764675746529586",
    "friendly_name": "BarcelonaProfileThreadsTabRefetchableDirectQuery",
    "root_field": "xdt_api__v1__text_feed__user_id__profile__connection",
}

# 웹앱이 함께 보내는 Relay provider 플래그. 빠지면 서버가 execution error로 응답한다.
# 타임라인과 프로필의 합집합이며 두 쿼리 사이에 값이 충돌하는 항목은 없다.
_PROVIDERS_ON = (
    "BarcelonaIsLoggedIn",
    "BarcelonaHasDearAlgoConsumption",
    "BarcelonaHasCommunities",
    "BarcelonaHasGameScoreShare",
    "BarcelonaHasPublicViewCountCard",
    "BarcelonaHasCommunityEntityCard",
    "BarcelonaHasScorecardCommunity",
    "BarcelonaHasSportTeamAllegianceCard",
    "BarcelonaHasMusic",
    "BarcelonaHasMessaging",
    "BarcelonaShouldFulfillLightboxQuery",
    "BarcelonaHasViewerReplied",
    "BarcelonaOptionalCookiesEnabled",
    "BarcelonaShouldShowFediverseM075Features",
    "BarcelonaHasProfileSelfReplyContext",
)
_PROVIDERS_OFF = (
    "BarcelonaHasEventBadge",
    "BarcelonaGenAIRepliesEnabled",
    "BarcelonaIsSearchDiscoveryEnabled",
    "BarcelonaHasNewspaperLinkStyle",
    "BarcelonaHasPodcastV2Consumption",
    "BarcelonaHasPodcastTranscriptConsumption",
    "BarcelonaHasPrivateRepliesDeprecation",
    "BarcelonaHasGhostPostEmojiActivation",
    "BarcelonaHasDearAlgoWebProduction",
    "BarcelonaHasWebFavicons",
    "BarcelonaIsCrawler",
    "BarcelonaHasCommunityTopContributors",
    "BarcelonaCanSeeSponsoredContent",
    "BarcelonaIsInternalUser",
)
RELAY_PROVIDERS: Dict[str, bool] = {
    f"__relay_internal__pv__{name}relayprovider": True for name in _PROVIDERS_ON
}
RELAY_PROVIDERS.update(
    {f"__relay_internal__pv__{name}relayprovider": False for name in _PROVIDERS_OFF}
)


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
        self.csrf_token = cookies.get("csrftoken", "")
        self._tokens: Optional[Dict[str, str]] = None

        self.session.cookies.update(cookies)
        self.session.headers.update(
            {
                "User-Agent": WEB_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "Accept-Language": "en-US,en;q=0.9",
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
        try:
            return await self._crawl_impl(count, user_id)
        finally:
            # 크롤러 인스턴스는 1회성이다. 세션 커넥션 풀을 정리한다.
            self.session.close()

    async def _crawl_impl(
        self, count: int = 5, user_id: Optional[str] = None
    ) -> List[Post]:
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
        typer.echo(
            f"[API 모드] {self.platform_name} 크롤링 시작 - {mode} (게시글 {count}개)"
        )

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

    def _fetch_tokens(self) -> Dict[str, str]:
        """threads.com HTML에서 GraphQL 호출에 필요한 요청 토큰을 뽑는다."""
        if self._tokens is not None:
            return self._tokens

        resp = self.session.get(THREADS_BASE + "/", timeout=20)
        resp.raise_for_status()
        html = resp.text

        lsd = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
        dtsg = re.search(r'"DTSGInitialData",\[\],\{"token":"([^"]+)"', html)
        if not lsd or not dtsg:
            typer.echo("세션이 만료되었습니다. 재로그인하세요:")
            typer.echo("  uv run skim login threads")
            self._tokens = {}
            return self._tokens

        self._tokens = {"lsd": lsd.group(1), "fb_dtsg": dtsg.group(1)}
        if self.debug_mode:
            typer.echo("  요청 토큰 확보 (lsd/fb_dtsg)")
        return self._tokens

    def _graphql(
        self, query: Dict[str, str], variables: Dict[str, Any], label: str
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """persisted query를 호출하고 (edges, next_cursor)를 돌려준다."""
        tokens = self._fetch_tokens()
        if not tokens:
            return [], None

        payload = {
            "lsd": tokens["lsd"],
            "fb_dtsg": tokens["fb_dtsg"],
            "__a": "1",
            "__comet_req": "29",
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": query["friendly_name"],
            "server_timestamps": "true",
            "variables": json.dumps({**variables, **RELAY_PROVIDERS}),
            "doc_id": query["doc_id"],
        }
        headers = {
            "x-fb-friendly-name": query["friendly_name"],
            "x-fb-lsd": tokens["lsd"],
            "x-root-field-name": query["root_field"],
            "x-csrftoken": self.csrf_token,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": THREADS_BASE,
            "Referer": THREADS_BASE + "/",
        }

        resp = self.session.post(GRAPHQL_URL, data=payload, headers=headers, timeout=25)

        if resp.status_code in (401, 403):
            typer.echo("세션이 만료되었습니다. 재로그인하세요:")
            typer.echo("  uv run skim login threads")
            return [], None

        if resp.status_code != 200:
            # rate limit/서버 오류를 정상 빈 피드처럼 숨기지 않는다.
            typer.echo(f"  [!] {label} API 오류: HTTP {resp.status_code}")
            return [], None

        body = resp.json()
        if body.get("errors"):
            # 웹앱 재배포로 doc_id가 낡으면 200 + errors로 온다. 빈 피드로 숨기지 않는다.
            message = body["errors"][0].get("message", "unknown")
            typer.echo(f"  [!] {label} GraphQL 오류: {message}")
            typer.echo(f"      doc_id({query['doc_id']}) 갱신이 필요할 수 있습니다.")
            return [], None

        data = body.get("data") or {}
        if not data:
            return [], None

        # 응답 alias(feedData/mediaData)는 쿼리마다 달라 유일한 최상위 키를 그대로 쓴다.
        connection = next(iter(data.values())) or {}
        edges = connection.get("edges") or []
        page_info = connection.get("page_info") or {}
        next_cursor = (
            page_info.get("end_cursor") if page_info.get("has_next_page") else None
        )

        if self.debug_mode:
            typer.echo(
                f"  {label}: {len(edges)}개 edge 수신 "
                f"(next_cursor: {'있음' if next_cursor else '없음'})"
            )

        return edges, next_cursor

    def _fetch_timeline_feed(
        self, max_id: Optional[str] = None
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """For You 타임라인 피드"""
        variables = {
            "after": max_id,
            "before": None,
            "data": {
                "feed_view_info": "[]",
                "pagination_source": "text_post_feed_threads",
                "reason": "pagination" if max_id else "cold_start_fetch",
            },
            "first": 10,
            "last": None,
            "sort_by": None,
            "variant": "for_you",
        }
        edges, next_cursor = self._graphql(TIMELINE_QUERY, variables, "타임라인")

        # 타임라인 edge는 추천 사용자 슬롯도 섞여 오므로 스레드가 실린 노드만 남긴다.
        threads = []
        for edge in edges:
            thread = (edge.get("node") or {}).get("text_post_app_thread")
            if thread:
                threads.append(thread)

        return threads, next_cursor

    def _fetch_user_feed(
        self, user_id: str, max_id: Optional[str] = None
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """특정 사용자 프로필 피드"""
        variables = {
            "after": max_id,
            "allow_page_info_for_lox_user": False,
            "before": None,
            "first": 10,
            "last": None,
            "userID": str(user_id),
        }
        edges, next_cursor = self._graphql(PROFILE_QUERY, variables, "사용자 피드")

        # 프로필 응답은 node 자체가 스레드다.
        threads = [edge["node"] for edge in edges if edge.get("node")]

        return threads, next_cursor

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

        # 같은 작성자의 self-reply chain 내용 합치기 + 첨부 이미지 CDN URL 수집
        contents = []
        image_urls = []
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
            for media in (post_data, *(post_data.get("carousel_media") or [])):
                candidates = (media.get("image_versions2") or {}).get(
                    "candidates"
                ) or []
                if candidates and candidates[0].get("url"):
                    image_urls.append(candidates[0]["url"])

        if not contents:
            return None

        # 여러 self-reply를 구분자로 합침
        content = "\n\n---\n\n".join(contents) if len(contents) > 1 else contents[0]

        # 타임스탬프 (첫 번째 포스트 기준)
        taken_at = first_post.get("taken_at", 0)
        timestamp = (
            datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
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
        reply_count = (
            text_post_info.get("direct_reply_count", 0) if text_post_info else 0
        )
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
            **({"images": list(dict.fromkeys(image_urls))} if image_urls else {}),
        )
