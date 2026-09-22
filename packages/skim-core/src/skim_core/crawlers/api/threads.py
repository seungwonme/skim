"""
@file threads_api.py
@description Threads 크롤러

For You 타임라인은 실제 브라우저로 받습니다. 2026-09-08부터 Meta가 클라이언트
지문으로 걸러, requests로는 브라우저와 똑같은 요청을 보내도 빈 피드가 옵니다.
나머지 경로(사용자 피드, 게시물 페이지)는 세션 쿠키를 얹은 requests를 씁니다.

주요 기능:
1. 브라우저로 For You 타임라인 수집 (로그인 세션 재사용)
2. 사용자 프로필 피드는 GraphQL persisted query 호출
3. 스레드(self-reply chain) 내용 합치기
4. 페이지네이션을 통한 대량 수집

@dependencies
- playwright: 타임라인 수집용 브라우저
- requests: HTTP 클라이언트
- typer: CLI 출력
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import typer
from bs4 import BeautifulSoup

from ...comments import Comment, append_comment_section, render_comment_section
from ...models import Post
from ...paths import SESSIONS_DIR

# Threads 웹 GraphQL 설정
THREADS_BASE = "https://www.threads.com"
GRAPHQL_URL = f"{THREADS_BASE}/graphql/query"
WEB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

# 타임라인 스크롤 상한. 한 회에 6~7건씩 오므로 50건이면 10회 안쪽에서 채워진다.
MAX_TIMELINE_SCROLLS = 15
# 답글은 문서가 실어 보내는 만큼 전부 담는다. 15개로 자르던 때는 문서에 24개가 와도
# 9개를 버렸다. 상한이 없으므로 인기 게시물은 본문이 길어진다.
MAX_REPLIES = None
# 답글이 0건이라고 보고된 게시물만 건너뛴다. `comments`가 삭제·비공개 답글까지 세는
# 부정확한 값이라 높게 잡으면 멀쩡한 글이 빠진다(3이던 때 답글 1~2개인 12.6%가 통째로 빠졌다).
MIN_REPLIES_FOR_FETCH = 1
# 게시물 페이지는 답글이 담긴 SSR 페이로드를 로그인 없이도 준다. 단 threads.net으로
# 요청하면 리다이렉트 뒤 페이로드가 빠진 셸이 와서, threads.com으로 직접 받아야 한다.
POST_PAGE_HEADERS = {
    "User-Agent": WEB_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

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


def parse_meta_response(text: str) -> Optional[Dict[str, Any]]:
    """Meta 응답을 JSON으로 읽는다. 거부 응답은 `for (;;);` 봉투로 온다.

    봉투를 안 벗기면 거부 사유가 JSONDecodeError로 바뀌어, 어느 계층이 막혔는지
    모르는 채 "API 요청 실패"만 남는다.
    """
    body = text.lstrip()
    if body.startswith("for (;;);"):
        body = body[len("for (;;);") :]
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


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
        # X-IG-App-ID를 붙이지 않는다. Meta가 2026-09-08부터 이 헤더가 달린
        # graphql/query 요청을 error 1357054로 거부한다. 브라우저 웹앱도 Threads
        # 도메인에서는 이 헤더를 보내지 않는다. 세션 기본 헤더에 두면 토큰 추출용
        # HTML GET은 통과하고 GraphQL만 죽어서, 세션 만료처럼 보인다.
        self.session.headers.update(
            {
                "User-Agent": WEB_USER_AGENT,
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
        no_content = options.get("no_content", False)
        try:
            posts = await self._crawl_impl(count, user_id)
            if not no_content:
                self.attach_replies(posts)
            return posts
        finally:
            # 크롤러 인스턴스는 1회성이다. 세션 커넥션 풀을 정리한다.
            self.session.close()

    def attach_replies(self, posts: List[Post]) -> None:
        """타인 답글을 정본 본문 뒤에 잇는다.

        답글이 있다고 보고된 게시물은 전부 조회한다(게시물당 요청 1건, 실측 1~4초).
        `--count`를 크게 주면 그만큼 크롤이 길어진다.
        """
        failures = 0
        attempted = 0
        attached = 0
        for post in posts:
            if (post.comments or 0) < MIN_REPLIES_FOR_FETCH:
                continue
            attempted += 1
            # HTTP 실패는 fetch_reply_section 안에서 조용히 None이 된다. 여기서 잡는 건
            # 상류 SSR 페이로드 구조가 바뀌었을 때의 파싱 실패다. 그게 크롤 루프까지
            # 올라가면 이 회차의 게시물 전량이 저장 0건이 된다.
            try:
                section = self.fetch_reply_section(post.url)
            except Exception as exc:  # noqa: BLE001 - 답글 실패가 게시물 저장을 막지 않는다
                failures += 1
                typer.echo(f"   [!] Threads 답글 파싱 실패: {exc}")
                continue
            if section:
                attached += 1
                post.content_markdown = append_comment_section(
                    post.content_markdown or post.content, section
                )

        if failures:
            typer.echo(f"   [!] Threads 답글 파싱 실패 {failures}건 (본문만 저장)")

        # 개별 실패는 위에서 잡히지만, 상류가 페이로드를 통째로 빼면 예외 없이 전건
        # None이 되어 조용히 넘어간다. 실제로 2026-09-08부터 2주간 그렇게 묻혔다.
        if attempted and not attached:
            typer.echo(
                f"   [!] 답글이 있다고 표시된 {attempted}건에서 답글을 하나도 못 받았습니다."
            )
            typer.echo("       상류 답글 페이로드 구조가 바뀌었는지 확인하세요.")

    def fetch_reply_section(self, url: Optional[str]) -> Optional[str]:
        """게시물 페이지의 SSR 페이로드에서 답글을 뽑아 마크다운 섹션으로 만든다.

        타임라인 GraphQL 응답에는 타인 답글이 오지 않는다. 게시물 문서는 답글까지 담고
        있고 로그인도 필요 없어서, persisted query 좌표(doc_id)를 따로 들지 않아도 된다.
        """
        if not url:
            return None
        # threads.net으로 요청하면 리다이렉트 뒤 페이로드가 빠진 셸이 온다.
        page_url = url.replace("threads.net", "threads.com")

        # 같은 URL이라도 답글 페이로드가 빠진 문서가 간헐적으로 온다(실측). 한 번 더 받아본다.
        root_author, threads = "", []
        for _ in range(2):
            try:
                response = requests.get(page_url, headers=POST_PAGE_HEADERS, timeout=25)
                response.raise_for_status()
            except Exception:  # noqa: BLE001 - 답글 실패가 게시물 저장을 막지 않는다
                return None
            root_author, threads = self._extract_reply_threads(response.text)
            if threads:
                break

        collected: List[Comment] = []
        for thread in threads:
            items = thread.get("thread_items") or []
            if not items:
                continue
            # 작성자가 스스로 시작한 스레드는 self-reply 연작이라 이미 본문에 담겨 있다.
            # 대화 도중 작성자가 남의 답글에 단 답변은 depth>0이라 그대로 살아남는다.
            starter = ((items[0].get("post") or {}).get("user") or {}).get("username")
            if root_author and starter == root_author:
                continue
            for depth, item in enumerate(items):
                post_data = item.get("post") or {}
                text = ((post_data.get("caption") or {}) or {}).get("text") or ""
                if not text:
                    continue
                user = (post_data.get("user") or {}).get("username") or "unknown"
                taken_at = post_data.get("taken_at")
                collected.append(
                    Comment(
                        author=f"@{user}",
                        text=text,
                        score=post_data.get("like_count"),
                        created=(
                            datetime.fromtimestamp(taken_at, tz=timezone.utc).strftime(
                                "%Y-%m-%d %H:%M UTC"
                            )
                            if taken_at
                            else None
                        ),
                        depth=min(depth, 1),
                    )
                )

        return render_comment_section(
            "Threads Replies", collected, max_comments=MAX_REPLIES, score_unit="like"
        )

    @staticmethod
    def _extract_reply_threads(html: str) -> tuple[str, List[Dict[str, Any]]]:
        """문서에 심긴 JSON에서 (루트 작성자, 답글 스레드 목록)을 찾는다.

        페이로드는 `data.data.edges`에 [루트 게시물, 답글, 답글...] 순으로 담긴다.
        같은 문서의 `relatedPosts`도 thread_items를 갖지만 그건 무관한 추천 게시물이라,
        edges 배열만 집어 첫 항목(루트)을 버린다.
        """
        found: List[Dict[str, Any]] = []
        root_author = ""

        def thread_author(thread: Dict[str, Any]) -> str:
            items = thread.get("thread_items") or []
            if not items:
                return ""
            return ((items[0].get("post") or {}).get("user") or {}).get(
                "username"
            ) or ""

        def walk(node: Any) -> None:
            nonlocal root_author
            if isinstance(node, dict):
                edges = node.get("edges")
                if isinstance(edges, list) and len(edges) > 1:
                    threads = [
                        edge.get("node")
                        for edge in edges
                        if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
                    ]
                    if threads and all(
                        t.get("thread_items") is not None for t in threads
                    ):
                        if not root_author:
                            root_author = thread_author(threads[0])
                        found.extend(threads[1:])  # [0]은 본문에 이미 담긴 루트 게시물
                        return
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("script", attrs={"type": "application/json"}):
            raw = tag.string or ""
            if "thread_items" not in raw:
                continue
            try:
                walk(json.loads(raw))
            except ValueError:
                continue
        return root_author, found

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

        if not user_id:
            # For You 타임라인은 requests로 받을 수 없다. 아래 메서드 주석 참고.
            return self._parse_threads(
                await self._collect_timeline_threads(count), count
            )

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

    def _parse_threads(self, threads: List[Dict[str, Any]], count: int) -> List[Post]:
        """스레드 노드를 Post로 바꾼다. 같은 게시물이 두 번 오면 한 번만 센다."""
        posts: List[Post] = []
        seen: set[str] = set()
        for thread in threads:
            post = self._parse_thread(thread)
            if not post:
                continue
            key = post.external_id or post.url or ""
            if key in seen:
                continue
            seen.add(key)
            posts.append(post)
            if self.debug_mode:
                typer.echo(f"  @{post.author}: {post.content[:60]}...")
            if len(posts) >= count:
                break
        typer.echo(f"총 {len(posts)}개의 게시글을 추출했습니다.")
        return posts

    async def _collect_timeline_threads(self, count: int) -> List[Dict[str, Any]]:
        """실제 브라우저로 For You 타임라인을 받아 스레드 노드를 모은다.

        requests로는 2026-09-08부터 빈 피드만 온다. 브라우저가 방금 7건을 받은
        요청을 payload와 헤더까지 그대로 즉시 재전송해도 edges가 0으로 오고 오류도
        없다(2026-09-22 실측). 클라이언트 지문 단계에서 걸러지는 것이라 요청을
        흉내내는 방향으로는 못 고친다. 그래서 타임라인만 브라우저를 태운다.

        게시물 페이지(답글)와 사용자 피드는 여전히 requests로 받는다.
        """
        # pylint: disable=import-outside-toplevel
        # REGISTRY가 이 모듈을 항상 import하므로, playwright를 최상위에서 끌어오면
        # 다른 플랫폼 크롤만 돌릴 때까지 브라우저 스택 로딩 비용을 물게 된다.
        from playwright.async_api import async_playwright

        collected: List[Dict[str, Any]] = []

        def take(payload: Dict[str, Any]) -> None:
            data = payload.get("data") or {}
            connection = next(iter(data.values()), None) or {}
            for edge in connection.get("edges") or []:
                thread = (edge.get("node") or {}).get("text_post_app_thread")
                if thread:
                    collected.append(thread)

        async with async_playwright() as pw:
            # 번들 chromium 대신 시스템 Chrome을 쓴다. 로그인 경로와 같은 브라우저이고,
            # `playwright install` 상태에 수집이 묶이지 않는다.
            browser = await pw.chromium.launch(channel="chrome", headless=True)
            try:
                context = await browser.new_context(
                    storage_state=str(self.session_path),
                    user_agent=WEB_USER_AGENT,
                )
                page = await context.new_page()

                async def on_response(response: Any) -> None:
                    if "graphql/query" not in response.url:
                        return
                    if TIMELINE_QUERY["friendly_name"] not in (
                        response.request.post_data or ""
                    ):
                        return
                    try:
                        body = parse_meta_response(await response.text())
                    except Exception:  # noqa: BLE001 - 응답 본문을 못 읽어도 수집은 이어간다
                        return
                    if body:
                        take(body)

                page.on("response", lambda r: asyncio.create_task(on_response(r)))

                await page.goto(
                    THREADS_BASE + "/", wait_until="domcontentloaded", timeout=60000
                )
                await page.wait_for_timeout(5000)

                # 첫 화면은 서버 렌더링이라 GraphQL을 타지 않는다. 스크롤해야 피드가 온다.
                for _ in range(MAX_TIMELINE_SCROLLS):
                    if len(collected) >= count:
                        break
                    await page.mouse.wheel(0, 6000)
                    await page.wait_for_timeout(2500)
            finally:
                await browser.close()

        if not collected:
            typer.echo("  [!] 타임라인이 비어 있습니다. 세션이 만료됐는지 확인하세요:")
            typer.echo("      uv run skim login threads")
        return collected

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

        body = parse_meta_response(resp.text)
        if body is None:
            typer.echo(f"  [!] {label} 응답을 해석하지 못했습니다 (HTTP 200, 비JSON).")
            return [], None

        if body.get("errorSummary") or body.get("error"):
            # Meta는 거부 사유를 GraphQL errors가 아니라 이 봉투로 돌려준다.
            # 2026-09-08~09-21 동안 error 1357054가 매일 여기로 왔는데, 봉투를
            # 안 벗겨서 JSONDecodeError로만 보였다.
            code = body.get("error", "unknown")
            summary = body.get("errorSummary") or body.get("errorDescription", "")
            typer.echo(f"  [!] {label} 요청 거부: error {code} {summary}")
            typer.echo("      헤더/토큰이 최신 웹앱과 어긋났을 수 있습니다.")
            return [], None

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

        # 이미지만 올린 게시물은 본문이 비지만 버리면 행 자체가 안 생겨,
        # 그날 그 계정이 무엇을 올렸는지가 통째로 사라진다. x가 이미 쓰는
        # 사다리(본문 -> 미디어 링크 -> 버림)를 따른다.
        content_status = None
        if contents:
            # 여러 self-reply를 구분자로 합침
            content = "\n\n---\n\n".join(contents) if len(contents) > 1 else contents[0]
        elif image_urls:
            content = "\n".join(dict.fromkeys(image_urls))
            content_status = "media_link"
        else:
            return None

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
            **({"content_status": content_status} if content_status else {}),
        )
