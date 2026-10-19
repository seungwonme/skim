"""
@file threads_api.py
@description Threads 크롤러

`skim login`이 로그인해 둔 전용 Chrome 프로필을 열어 웹앱이 스스로 보내는 요청의 응답을
읽습니다(#105). 쿠키를 꺼내 다른 환경에서 쓰지 않습니다. 2026-09-08부터 Meta가 클라이언트
지문으로 걸러, 브라우저가 보낸 요청을 헤더까지 그대로 재전송해도 빈 결과가 옵니다.

주요 기능:
1. For You 타임라인: 홈을 열고 스크롤할 때 오는 타임라인 쿼리 응답
2. 사용자 프로필 피드(--user-id): 프로필 페이지를 스크롤할 때 오는 프로필 쿼리 응답
3. 스레드(self-reply chain) 내용 합치기
4. 답글: 같은 브라우저에서 게시물 페이지를 열고 웹앱의 네트워크 계층으로 부름

@dependencies
- playwright: 전용 프로필 Chrome에 CDP로 붙는다
- typer: CLI 출력
"""

import asyncio
import contextlib
import json
from datetime import datetime, timezone
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
)
from urllib.parse import parse_qs

import typer

from ...comments import Comment, append_comment_section, render_comment_section
from ...models import Post
from ..browser_profile import open_profile

THREADS_BASE = "https://www.threads.com"

# 피드 스크롤 상한. 타임라인은 한 회에 6~7건씩 오므로 50건이면 10회 안쪽에서 채워진다.
MAX_TIMELINE_SCROLLS = 15
# 받은 답글은 전부 담는다. 15개로 자르던 때는 24개가 와도 9개를 버렸다.
# 상한이 없으므로 인기 게시물은 본문이 길어진다.
MAX_REPLIES = None
# 답글이 0건이라고 보고된 게시물만 건너뛴다. `comments`가 삭제·비공개 답글까지 세는
# 부정확한 값이라 높게 잡으면 멀쩡한 글이 빠진다(3이던 때 답글 1~2개인 12.6%가 통째로 빠졌다).
MIN_REPLIES_FOR_FETCH = 1

# 답글은 게시물 페이지의 웹앱이 쓰는 쿼리로 받는다. 모듈 이름으로 찾으므로 doc_id와
# Relay provider 플래그(2026-09-30 기준 35개)는 웹앱이 재배포돼도 웹앱이 맞춘다.
REPLIES_QUERY_MODULE = "BarcelonaPostPageDirectRepliesRefetchQuery.graphql"
# 요청 하나에 받는 최상위 답글 스레드 수. 웹앱은 스크롤마다 4개씩 받지만 서버는 큰 값도
# 받아 준다. 답글 54개(스레드 17개) 게시물이 요청 1건에 끝났다(2026-09-30 실측).
REPLY_PAGE_SIZE = 25
# 게시물당 답글 요청 상한. 25개씩 4번이면 최상위 스레드 100개다.
MAX_REPLY_PAGES = 4
# 답글 요청 사이 간격(초). 계정으로 식별되는 요청이라 몰아서 보내지 않는다.
REPLY_REQUEST_INTERVAL = 1.0
# 게시물 페이지를 연 뒤 웹앱이 Relay 환경과 답글 쿼리 모듈을 올리기까지 기다리는 시간(초).
REPLY_BOOT_TIMEOUT = 30
# 답글 요청 하나의 응답 대기 상한(초).
REPLY_FETCH_TIMEOUT = 30
# 게시물 URL의 shortcode는 숫자 게시물 ID를 이 알파벳의 64진수로 쓴 것이다.
SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

# 웹앱의 Relay 환경과 답글 쿼리 모듈을 찾아 window에 걸어 둔다. 환경은 React fiber를
# 루트 쪽으로 올라가며 getNetwork와 getStore를 가진 Provider 값을 찾는다. 준비됐으면
# "ok", 아니면 아직 없는 쪽의 이름을 돌려준다.
_BOOT_REPLIES_JS = """
(moduleName) => {
  let query;
  try {
    query = require(moduleName);
  } catch (e) {
    return "module";
  }
  const isEnv = (v) =>
    v && typeof v.getNetwork === "function" && typeof v.getStore === "function";
  const nodes = [
    ...document.querySelectorAll("[data-pressable-container]"),
    ...document.querySelectorAll("body *"),
  ];
  for (const node of nodes.slice(0, 5000)) {
    const key = Object.keys(node).find((k) => k.startsWith("__reactFiber$"));
    for (let fiber = key && node[key]; fiber; fiber = fiber.return) {
      const value = fiber.memoizedProps && fiber.memoizedProps.value;
      const env = isEnv(value) ? value : value && isEnv(value.environment) ? value.environment : null;
      if (env) {
        window.__skimReplies = {env, query};
        return "ok";
      }
    }
  }
  return "relay environment";
}
"""

# 웹앱 네트워크 계층으로 답글 쿼리를 부른다. provider 플래그는 쿼리 모듈이 그 자리에서
# 계산한다. 같은 doc_id, 변수, 플래그, 웹앱 폼 필드로 손으로 만든 요청은 파이썬에서든
# 페이지 안 fetch에서든 오류 없이 direct_replies가 null로 온다(2026-09-30 실측).
_FETCH_REPLIES_JS = """
async ({postID, after, first}) => {
  const {env, query} = window.__skimReplies;
  const variables = {after, filterType: null, first, postID, sortOrder: "TOP"};
  for (const [name, provider] of Object.entries(query.params.providedVariables || {})) {
    variables[name] = provider.get();
  }
  const payloads = [];
  await new Promise((resolve, reject) => {
    env.getNetwork().execute(query.params, variables, {force: true}).subscribe({
      next: (payload) => payloads.push(payload),
      error: reject,
      complete: resolve,
    });
  });
  return payloads;
}
"""

ReplyPageFetcher = Callable[[str, Optional[str]], Awaitable[List[Dict[str, Any]]]]

# 웹앱이 피드를 넘길 때 보내는 쿼리 이름. 응답을 이 이름으로 골라 읽는다.
# 프로필 쿼리는 2026-10-20 프로필 페이지 스크롤에서 확인했다.
TIMELINE_QUERY_NAME = "BarcelonaFeedPaginationDirectQuery"
PROFILE_QUERY_NAME = "BarcelonaProfileThreadsTabRefetchableDirectQuery"


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


def request_query_name(
    post_data: Optional[str], headers: Mapping[str, str]
) -> Optional[str]:
    """브라우저 요청의 GraphQL 쿼리 이름.

    정확한 URL 경로로 거르지 않는다. 웹앱이 2026-10 초에 같은 쿼리를
    `/graphql/query`에서 `/api/graphql`로 옮겼을 때, `graphql/query`로 거르던 수집이
    응답을 전부 버렸다 (#74).
    """
    name = headers.get("x-fb-friendly-name")
    if not name and post_data:
        name = (parse_qs(post_data).get("fb_api_req_friendly_name") or [None])[0]
    return name


def _connection_edges(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """응답의 유일한 최상위 연결에서 edges를 꺼낸다. 루트 필드 이름은 별칭이라 보지 않는다."""
    data = payload.get("data") or {}
    connection = next(iter(data.values()), None) or {}
    return connection.get("edges") or []


def timeline_threads(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """타임라인 응답에서 스레드 노드만 꺼낸다. 추천 사용자 슬롯이 섞여 온다."""
    threads = []
    for edge in _connection_edges(payload):
        thread = (edge.get("node") or {}).get("text_post_app_thread")
        if thread:
            threads.append(thread)
    return threads


def profile_threads(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """프로필 응답에서 스레드를 꺼낸다. 프로필은 node 자체가 스레드다."""
    return [
        edge["node"]
        for edge in _connection_edges(payload)
        if (edge.get("node") or {}).get("thread_items")
    ]


def profile_handle(user_id: str) -> str:
    """`--user-id`로 받은 Threads 사용자 이름. 숫자 ID는 프로필 주소로 열 수 없다."""
    handle = user_id.strip().lstrip("@")
    if not handle or handle.isdigit():
        raise ValueError(
            "threads --user-id는 사용자 이름을 받습니다 (예: --user-id zuck). "
            "숫자 ID로는 프로필 페이지를 열 수 없습니다"
        )
    return handle


def shortcode_to_post_id(code: str) -> Optional[str]:
    """게시물 URL의 shortcode를 GraphQL이 받는 숫자 게시물 ID로 바꾼다."""
    if not code:
        return None
    post_id = 0
    for char in code:
        digit = SHORTCODE_ALPHABET.find(char)
        if digit < 0:
            return None
        post_id = post_id * 64 + digit
    return str(post_id)


def reply_post_id(post: Post) -> Optional[str]:
    """답글 쿼리에 넘길 게시물 ID. external_id는 shortcode이고, shortcode가 없던 글만 숫자 pk다."""
    external_id = post.external_id or ""
    # shortcode는 11자 안팎이고 pk는 19자리 숫자다.
    if external_id.isdigit() and len(external_id) > 15:
        return external_id
    return shortcode_to_post_id(external_id)


def parse_reply_page(
    payload: Dict[str, Any], root_author: str
) -> tuple[List[Comment], Optional[str]]:
    """답글 쿼리 응답 하나에서 (답글 목록, 다음 페이지 커서)를 뽑는다.

    응답은 `data.media.text_post_app_info.direct_replies`에 최상위 답글 스레드를 담고,
    스레드마다 `posts.edges`에 [답글, 그 답글에 이어진 대화...]가 순서대로 온다.
    """
    info = ((payload.get("data") or {}).get("media") or {}).get(
        "text_post_app_info"
    ) or {}
    connection = info.get("direct_replies") or {}

    collected: List[Comment] = []
    for edge in connection.get("edges") or []:
        posts = ((edge.get("node") or {}).get("posts") or {}).get("edges") or []
        chain = [post_edge.get("node") or {} for post_edge in posts]
        if not chain:
            continue
        # 작성자가 스스로 시작한 스레드는 self-reply 연작이라 이미 본문에 담겨 있다.
        # 대화 도중 작성자가 남의 답글에 단 답변은 depth>0이라 그대로 살아남는다.
        starter = (chain[0].get("user") or {}).get("username")
        if root_author and starter == root_author:
            continue
        for depth, reply in enumerate(chain):
            text = (reply.get("caption") or {}).get("text") or ""
            if not text:
                continue
            user = (reply.get("user") or {}).get("username") or "unknown"
            taken_at = reply.get("taken_at")
            collected.append(
                Comment(
                    author=f"@{user}",
                    text=text,
                    score=reply.get("like_count"),
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

    page_info = connection.get("page_info") or {}
    next_cursor = (
        page_info.get("end_cursor") if page_info.get("has_next_page") else None
    )
    return collected, next_cursor


class ThreadsAPICrawler:
    """
    Threads 크롤러

    전용 Chrome 프로필을 한 번 띄워 타임라인(또는 프로필 피드)과 답글을 그 안에서 받는다.
    """

    platform = "threads"
    # crawl()이 연 전용 프로필 브라우저의 컨텍스트. 크롤 한 번 동안만 산다.
    # 클래스 속성으로 둬야 생성자를 우회해 만든 인스턴스(테스트)에서도 정의돼 있다.
    _context: Any = None

    def __init__(self, debug_mode: bool = False):
        self.platform_name = "Threads"
        self.debug_mode = debug_mode

    async def crawl(self, **options) -> List[Post]:
        count = options.get("count", 5)
        user_id = options.get("user_id")
        no_content = options.get("no_content", False)
        # 브라우저를 띄우기 전에 입력부터 확인한다.
        handle = profile_handle(user_id) if user_id else None
        async with open_profile() as context:
            self._context = context
            try:
                posts = await self._crawl_impl(count, handle)
                if not no_content:
                    await self.attach_replies(posts)
            finally:
                self._context = None
        return posts

    async def attach_replies(self, posts: List[Post]) -> None:
        """타인 답글을 정본 본문 뒤에 잇는다.

        답글이 있다고 보고된 게시물은 전부 조회한다. 브라우저를 한 번 띄워 게시물
        페이지를 열고, 그 웹앱으로 게시물마다 답글 쿼리를 부른다(요청 1건에 실측 약
        1초, 요청 사이 1초 간격). 로그인 세션으로 나가는 요청이라 계정으로 식별된다.
        `--count`를 크게 주면 그만큼 크롤이 길어진다.
        """
        targets = [
            (post, post_id)
            for post in posts
            if (post.comments or 0) >= MIN_REPLIES_FOR_FETCH
            and (post_id := reply_post_id(post))
        ]
        if not targets:
            return

        failures = 0
        attached = 0
        try:
            async with self._reply_fetcher([post for post, _ in targets]) as fetch:
                for index, (post, post_id) in enumerate(targets):
                    if index:
                        await asyncio.sleep(REPLY_REQUEST_INTERVAL)
                    # 요청 실패와 응답 구조 변경을 여기서 잡는다. 크롤 루프까지 올라가면
                    # 이 회차의 게시물 전량이 저장 0건이 된다.
                    try:
                        section = await self._reply_section(fetch, post_id, post.author)
                    except Exception as exc:  # noqa: BLE001 - 답글 실패가 게시물 저장을 막지 않는다
                        failures += 1
                        typer.echo(f"   [!] Threads 답글 수집 실패: {exc}")
                        continue
                    if section:
                        attached += 1
                        post.content_markdown = append_comment_section(
                            post.content_markdown or post.content, section
                        )
        except Exception as exc:  # noqa: BLE001 - 브라우저나 웹앱을 못 띄워도 본문은 저장한다
            typer.echo(
                f"   [!] Threads 답글 수집 중단 ({attached}건만 붙이고 본문 저장): {exc}"
            )
            return

        if failures:
            typer.echo(f"   [!] Threads 답글 수집 실패 {failures}건 (본문만 저장)")

        # 개별 실패는 위에서 잡히지만, 상류가 응답 구조를 통째로 바꾸면 예외 없이 전건
        # 빈 결과가 되어 조용히 넘어간다. 실제로 2026-09-08부터 2주간 그렇게 묻혔다.
        if not attached:
            typer.echo(
                f"   [!] 답글이 있다고 표시된 {len(targets)}건에서 답글을 하나도 못 받았습니다."
            )
            typer.echo("       상류 답글 응답 구조가 바뀌었는지 확인하세요.")

    async def _reply_section(
        self, fetch: ReplyPageFetcher, post_id: str, root_author: str
    ) -> Optional[str]:
        """게시물 하나의 답글을 페이지를 넘기며 모아 마크다운 섹션으로 만든다."""
        collected: List[Comment] = []
        after: Optional[str] = None
        for page in range(MAX_REPLY_PAGES):
            if page:
                await asyncio.sleep(REPLY_REQUEST_INTERVAL)
            cursor = None
            for payload in await fetch(post_id, after):
                comments, next_cursor = parse_reply_page(payload, root_author)
                collected.extend(comments)
                cursor = next_cursor or cursor
            if not cursor:
                break
            after = cursor

        return render_comment_section(
            "Threads Replies", collected, max_comments=MAX_REPLIES, score_unit="like"
        )

    @contextlib.asynccontextmanager
    async def _reply_fetcher(
        self, posts: List[Post]
    ) -> AsyncIterator[ReplyPageFetcher]:
        """답글 쿼리를 부르는 함수를 내준다. 블록이 끝나면 답글용 탭을 닫는다.

        crawl()이 연 브라우저에 탭을 하나 더 연다. 게시물 페이지를 열어 웹앱이 Relay
        환경과 답글 쿼리 모듈을 올리게 한다. 첫 게시물이 지워졌으면 모듈이 안 올라올 수
        있어 다음 게시물로 한 번 더 연다.
        """
        page = await self._context.new_page()
        try:
            status = await self._boot_first_ready(page, [p.url for p in posts if p.url])
            if status != "ok":
                raise RuntimeError(f"웹앱 답글 쿼리를 준비하지 못했습니다 ({status})")

            async def fetch(post_id: str, after: Optional[str]) -> List[Dict[str, Any]]:
                return await asyncio.wait_for(
                    page.evaluate(
                        _FETCH_REPLIES_JS,
                        {"postID": post_id, "after": after, "first": REPLY_PAGE_SIZE},
                    ),
                    timeout=REPLY_FETCH_TIMEOUT,
                )

            yield fetch
        finally:
            await page.close()

    @classmethod
    async def _boot_first_ready(cls, page: Any, urls: List[str]) -> str:
        """앞쪽 게시물 두 개까지 열어 보고, 답글 쿼리가 준비된 첫 페이지에서 멈춘다.

        페이지 열기 예외도 그 게시물의 실패로 보고 다음 게시물로 넘어간다. 맥이
        잠들면 `goto`가 시간 초과 예외로 끝나는데, 예전에는 이 예외가 재시도를
        건너뛰고 답글 단계 전체를 접었다 (#76).
        """
        status = "게시물 URL 없음"
        for url in urls[:2]:
            try:
                status = await cls._boot_reply_page(page, url)
            except Exception as exc:  # noqa: BLE001 - 다음 게시물로 다시 연다
                # playwright 예외는 호출 기록이 여러 줄 붙어 첫 줄만 남긴다.
                first_line = (str(exc).splitlines() or [type(exc).__name__])[0]
                status = f"페이지 열기 실패: {first_line}"
                continue
            if status == "ok":
                break
        return status

    @staticmethod
    async def _boot_reply_page(page: Any, url: str) -> str:
        """게시물 페이지를 열고 답글 쿼리를 부를 준비가 될 때까지 기다린다."""
        # threads.net으로 열면 리다이렉트를 한 번 더 탄다.
        await page.goto(
            url.replace("threads.net", "threads.com"),
            wait_until="domcontentloaded",
            timeout=60000,
        )
        status = "timeout"
        for _ in range(int(REPLY_BOOT_TIMEOUT * 2)):
            status = await page.evaluate(_BOOT_REPLIES_JS, REPLIES_QUERY_MODULE)
            if status == "ok":
                break
            await page.wait_for_timeout(500)
        return status

    async def _crawl_impl(
        self, count: int = 5, handle: Optional[str] = None
    ) -> List[Post]:
        """타임라인이나 사용자 프로필 피드를 받아 스레드마다 Post 하나로 합친다."""
        mode = f"사용자 @{handle}" if handle else "For You 타임라인"
        typer.echo(
            f"[브라우저] {self.platform_name} 크롤링 시작 - {mode} (게시글 {count}개)"
        )
        if handle:
            threads = await self._capture_threads(
                f"{THREADS_BASE}/@{handle}", PROFILE_QUERY_NAME, profile_threads, count
            )
        else:
            threads = await self._capture_threads(
                THREADS_BASE + "/", TIMELINE_QUERY_NAME, timeline_threads, count
            )
        return self._parse_threads(threads, count)

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

    async def _capture_threads(
        self,
        url: str,
        query_name: str,
        extract: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
        count: int,
    ) -> List[Dict[str, Any]]:
        """페이지를 열고 스크롤하며, 웹앱이 보낸 피드 쿼리의 응답에서 스레드를 모은다.

        요청을 직접 만들지 않는다. 브라우저가 방금 받은 요청을 payload와 헤더까지
        그대로 재전송해도 edges가 0으로 오고 오류도 없었다(2026-09-22 실측). 클라이언트
        지문 단계에서 걸러지는 것이라 요청을 흉내내는 방향으로는 못 고친다.
        """
        collected: List[Dict[str, Any]] = []
        responses = 0

        async def on_response(response: Any) -> None:
            nonlocal responses
            # GraphQL이 아닌 요청은 본문을 열지 않는다. post_data는 본문을 UTF-8로
            # 디코드하므로 로깅 비콘 같은 바이너리 본문에서 예외가 난다.
            # 경로의 세부 형태(/graphql/query, /api/graphql)에는 기대지 않는다.
            if "graphql" not in response.url:
                return
            request = response.request
            try:
                post_data = request.post_data
            except UnicodeDecodeError:
                return
            if request_query_name(post_data, request.headers) != query_name:
                return
            responses += 1
            try:
                body = parse_meta_response(await response.text())
            except Exception:  # noqa: BLE001 - 응답 본문을 못 읽어도 수집은 이어간다
                return
            if body:
                collected.extend(extract(body))

        page = await self._context.new_page()
        try:
            page.on("response", lambda r: asyncio.create_task(on_response(r)))
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)
            # 첫 화면은 서버 렌더링이라 GraphQL을 타지 않는다. 스크롤해야 피드가 온다.
            for _ in range(MAX_TIMELINE_SCROLLS):
                if len(collected) >= count:
                    break
                await page.mouse.wheel(0, 6000)
                await page.wait_for_timeout(2500)
        finally:
            await page.close()

        # 두 경우를 나눠 남긴다. 쿼리 응답이 아예 없으면 웹앱이 쿼리 이름을 바꿨거나
        # 로그인이 풀린 것이고, 응답은 왔는데 비었으면 응답 구조가 바뀐 것이다.
        # 어느 쪽인지는 크롤이 끝난 뒤 계정 확인(#103)이 가린다.
        if not collected and not responses:
            typer.echo(
                f"  [!] 피드 쿼리({query_name}) 응답이 없었습니다. "
                "웹앱의 쿼리 이름이 바뀌었거나 로그인이 풀렸을 수 있습니다."
            )
        elif not collected:
            typer.echo(
                f"  [!] 피드 쿼리 응답 {responses}건에 스레드가 없습니다. "
                "응답 구조가 바뀌었을 수 있습니다."
            )
        return collected

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
