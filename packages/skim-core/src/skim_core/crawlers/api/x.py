"""
@file x_api.py
@description X (Twitter) 크롤러

`skim login`이 로그인해 둔 전용 Chrome 프로필을 열어 웹앱이 스스로 보내는 GraphQL 응답을
읽습니다(#105). 쿠키를 꺼내 다른 환경에서 쓰지 않습니다. twitter-api-client로 세션 파일의
쿠키를 실어 요청을 직접 만들던 경로는 지웠습니다.

주요 기능:
1. For You 타임라인: 홈을 열고 스크롤할 때 오는 HomeTimeline 응답
2. 사용자 트윗(--user-id): 프로필 페이지를 스크롤할 때 오는 UserTweets 응답
3. 스레드 병합과 답글: 같은 탭에서 웹앱 안 이동으로 트윗을 열 때 오는 TweetDetail 응답

@dependencies
- playwright: 전용 프로필 Chrome에 CDP로 붙는다
- typer: CLI 출력
"""

import asyncio
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import typer
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ...comments import Comment, append_comment_section, render_comment_section
from ...models import Post
from ..browser_profile import open_profile

X_BASE = "https://x.com"
X_HOME_URL = f"{X_BASE}/home"
TIMELINE_QUERY = "HomeTimeline"
USER_TWEETS_QUERY = "UserTweets"
DETAIL_QUERY = "TweetDetail"
# 홈 왼쪽 메뉴의 계정 버튼. 표시 이름과 @핸들이 들어 있다.
ACCOUNT_SWITCHER = "[data-testid='SideNav_AccountSwitcher_Button']"
MAX_REPLIES = 15
# 스레드는 TweetDetail을 이미 부르므로 답글이 공짜다. 단독 트윗은 요청이 추가로 드는데
# 실측 3~9초/건이라 무제한으로 두면 크롤이 몇 배 길어진다. 반응이 있는 것만, 회차 상한 안에서.
MIN_REPLIES_FOR_FETCH = 3
MAX_REPLY_FETCHES_PER_RUN = 20
# 피드 스크롤 상한. HomeTimeline 한 번에 트윗 20개 안팎이 온다.
MAX_SCROLLS = 15
# 트윗 페이지를 연달아 열 때 사이에 두는 간격(초).
DETAIL_INTERVAL_SECONDS = 1.0
DETAIL_TIMEOUT_MS = 30000
# 웹앱 안에서 주소만 바꿔 트윗을 연다. 그러면 TweetDetail 1건만 나간다. 문서를 새로 받으면
# 사이드바와 계정 쿼리가 트윗마다 8건쯤 함께 나간다(2026-10-20 실측, 20건 크롤에 약 210건).
OPEN_STATUS_SCRIPT = """id => {
    history.pushState({}, '', '/i/status/' + id);
    dispatchEvent(new PopStateEvent('popstate', {state: {}}));
}"""
# 로그인이 풀렸거나 계정 확인이 걸리면 웹앱이 이 경로로 보낸다.
LOGIN_WALL_PATHS = ("/i/flow/login", "/login", "/logout", "/account/access")
_SCREEN_NAME = re.compile(r"@(\w{1,15})")


def graphql_query_name(url: str) -> str:
    """GraphQL 주소의 마지막 경로가 쿼리 이름이다 (/i/api/graphql/<id>/HomeTimeline)."""
    path = urlparse(url).path
    return path.rsplit("/", 1)[-1] if "/graphql/" in path else ""


def login_wall(page_url: str) -> Optional[str]:
    """페이지가 로그인이나 계정 확인 화면으로 넘어갔으면 그 경로, 아니면 None."""
    path = urlparse(page_url).path
    return next((wall for wall in LOGIN_WALL_PATHS if path.startswith(wall)), None)


def screen_name_from(text: str) -> str:
    """계정 메뉴 글자에서 @핸들을 찾는다. 없으면 빈 문자열."""
    match = _SCREEN_NAME.search(text or "")
    return match.group(1) if match else ""


class XAPICrawler:
    """X 크롤러. 전용 프로필 브라우저에서 웹앱의 GraphQL 응답을 읽는다."""

    platform = "x"
    # 단독 트윗 답글은 요청이 추가로 드는 유일한 경로라 --no-content로 끌 수 있다.
    # 클래스 속성으로 둬야 생성자를 우회해 만든 인스턴스(테스트)에서도 정의돼 있다.
    _skip_extra_reply_fetch = False
    # crawl()이 연 전용 프로필 브라우저의 컨텍스트. 크롤 한 번 동안만 산다.
    _context: Any = None
    # 미리 받아 둔 TweetDetail 트윗 목록(대화 id별). _parse_tweets가 여기서 읽는다.
    _details: Optional[Dict[str, list[dict]]] = None
    # 계획 단계에서 TweetDetail이 필요한 대화를 적는 목록. 계획 단계가 아니면 None이다.
    _wanted: Optional[List[str]] = None

    def __init__(self, debug_mode: bool = False):
        self.platform_name = "X"
        self.debug_mode = debug_mode

    async def crawl(self, **options) -> List[Post]:
        count = options.get("count", 10)
        user_id = options.get("user_id")
        self._skip_extra_reply_fetch = options.get("no_content", False)
        handle = str(user_id).lstrip("@") if user_id else None
        async with open_profile() as context:
            self._context = context
            try:
                return await self._crawl_impl(count, handle)
            finally:
                self._context = None
                self._details = None

    async def _crawl_impl(
        self, count: int = 10, handle: Optional[str] = None
    ) -> List[Post]:
        """타임라인이나 사용자 트윗을 받고, 필요한 대화만 TweetDetail로 연 뒤 파싱한다."""
        mode = f"사용자 @{handle}" if handle else "For You 타임라인"
        typer.echo(
            f"[브라우저] {self.platform_name} 크롤링 시작 - {mode} (게시글 {count}개)"
        )
        # 타임라인과 트윗 상세를 한 탭에서 받는다. 트윗은 웹앱 안에서 주소만 바꿔 연다.
        page = await self._context.new_page()
        try:
            if handle:
                raw_data = await self._capture(
                    page, f"{X_BASE}/{handle}", USER_TWEETS_QUERY, count
                )
            else:
                raw_data = await self._capture(page, X_HOME_URL, TIMELINE_QUERY, count)
            if not raw_data:
                return []
            self._details = await self._fetch_details(
                page, self._detail_targets(raw_data, count)
            )
        finally:
            await page.close()

        posts = self._parse_tweets(raw_data, count)
        typer.echo(f"총 {len(posts)}개의 게시글을 추출했습니다.")
        return posts

    async def _capture(self, page: Any, url: str, query: str, count: int) -> List[dict]:
        """페이지를 열고 스크롤하며 웹앱이 받은 쿼리 응답을 모은다.

        요청을 직접 만들지 않는다. 로그인 화면으로 넘어가면 빈 목록이다.
        리트윗과 같은 대화의 조각이 Post 하나로 줄어드므로 트윗은 넉넉히 모은다.
        """
        bodies: List[dict] = []

        async def on_response(response: Any) -> None:
            if graphql_query_name(response.url) != query:
                return
            try:
                body = await response.json()
            except Exception:  # noqa: BLE001 - 응답 본문을 못 읽어도 수집은 이어간다
                return
            if isinstance(body, dict):
                bodies.append(body)

        def handler(response: Any) -> None:
            asyncio.create_task(on_response(response))

        page.on("response", handler)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)
            wall = login_wall(page.url)
            if wall:
                typer.echo(f"   ❌ X가 로그인 화면으로 넘어갔습니다 ({wall})")
                typer.echo(
                    "   세션이 만료되었습니다. 다시 로그인하세요: uv run skim login x"
                )
                return []
            for _ in range(MAX_SCROLLS):
                if self._tweet_count(bodies) >= count * 2:
                    break
                await page.mouse.wheel(0, 6000)
                await page.wait_for_timeout(2500)
        finally:
            # 같은 탭에서 트윗을 열 때 늦게 온 응답이 목록을 바꾸지 않게 뗀다.
            page.remove_listener("response", handler)

        if not bodies:
            typer.echo(
                f"  [!] {query} 응답이 없었습니다. "
                "웹앱의 쿼리 이름이 바뀌었거나 로그인이 풀렸을 수 있습니다."
            )
        return list(bodies)

    def _tweet_count(self, bodies: List[dict]) -> int:
        return sum(len(self._extract_tweet_results(body)) for body in bodies)

    def _detail_targets(self, raw_data: list[dict], count: int) -> List[str]:
        """파싱을 요청 없이 한 번 돌려 TweetDetail이 필요한 대화를 모은다.

        스레드인지, 답글을 채울 단독 트윗인지 고르는 기준이 _parse_tweets 안에 있다.
        기준을 두 곳에 두지 않으려고 같은 함수를 계획 단계로 돌린다.
        """
        self._details = None
        self._wanted = []
        try:
            self._parse_tweets(raw_data, count)
            return list(dict.fromkeys(self._wanted))
        finally:
            self._wanted = None

    async def _fetch_details(
        self, page: Any, conv_ids: List[str]
    ) -> Dict[str, list[dict]]:
        """대화마다 트윗을 열어 웹앱이 받은 TweetDetail의 트윗 목록을 모은다.

        웹앱 안에서 주소만 바꿔 연다. 웹앱이 그 이동에 반응하지 않아 응답이 안 오면
        남은 트윗은 페이지를 새로 받아 연다. 요청은 늘지만 답글은 계속 받는다.
        """
        details: Dict[str, list[dict]] = {}
        in_app = True
        for index, conv_id in enumerate(conv_ids):
            if index:
                await asyncio.sleep(DETAIL_INTERVAL_SECONDS)
            try:
                body = await self._open_detail(page, conv_id, in_app)
            except PlaywrightTimeout:
                if not in_app:
                    continue
                in_app = False
                typer.echo(
                    "   [!] X 웹앱 안 이동에 TweetDetail 응답이 없어 "
                    "남은 트윗은 페이지를 새로 받아 엽니다"
                )
                body = await self._open_detail_safely(page, conv_id, in_app)
            except Exception as exc:  # noqa: BLE001 - 네트워크, JSON 아님
                self._debug_detail_failure(exc)
                continue
            tweets = self._extract_tweet_results(body) if body else []
            if tweets:
                details[conv_id] = tweets
        missing = len(conv_ids) - len(details)
        if missing:
            typer.echo(
                f"   [!] X 상세 조회 실패 {missing}건 (응답 내 조각으로 대체, 답글 없음)"
            )
        return details

    async def _open_detail(self, page: Any, conv_id: str, in_app: bool) -> Any:
        """트윗을 열고 웹앱이 받은 TweetDetail 본문을 돌려준다. 응답이 없으면 시간 초과."""
        async with page.expect_response(
            lambda r: graphql_query_name(r.url) == DETAIL_QUERY,
            timeout=DETAIL_TIMEOUT_MS,
        ) as info:
            if in_app:
                await page.evaluate(OPEN_STATUS_SCRIPT, conv_id)
            else:
                await page.goto(
                    f"{X_BASE}/i/status/{conv_id}",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
        return await (await info.value).json()

    async def _open_detail_safely(self, page: Any, conv_id: str, in_app: bool) -> Any:
        try:
            return await self._open_detail(page, conv_id, in_app)
        except Exception as exc:  # noqa: BLE001 - 시간 초과, 네트워크, JSON 아님
            self._debug_detail_failure(exc)
            return None

    def _debug_detail_failure(self, exc: Exception) -> None:
        if self.debug_mode:
            typer.echo(f"  스레드 상세 조회 실패(응답 내 조각으로 폴백): {exc}")

    def _parse_tweets(self, raw_data: list[dict], count: int) -> List[Post]:
        """API 응답을 Post 모델로 변환

        같은 작성자가 self-reply로 이어단 스레드(줄줄이 글)는 하나의 Post로 병합한다.
        스레드로 판명되면 conversation 전체를 TweetDetail로 재조회해 응답에 잘려 온
        뒷부분까지 완전 복원하고, 재조회 실패 시 응답 내 조각으로 폴백한다.
        """
        tweets: list[dict] = []
        for entry in raw_data:
            tweets.extend(self._extract_tweet_results(entry))

        # 트윗별 메타(작성자/대화 id/self-reply 여부) 부착, RT·파싱불가 제외.
        # 스크롤로 받은 뒤쪽 응답에 앞에서 본 트윗이 다시 실리기도 해서 id로 한 번만 남긴다.
        parsed: list[tuple[dict, dict]] = []
        seen_ids: set[str] = set()
        for tweet in tweets:
            meta = self._tweet_meta(tweet)
            if meta and not meta["is_rt"] and meta["id"] not in seen_ids:
                seen_ids.add(meta["id"])
                parsed.append((tweet, meta))

        # conversation(스레드) 단위로 응답 내 조각을 모은다
        conv_groups: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
        for tweet, meta in parsed:
            conv_groups[meta["conv_id"]].append((tweet, meta))

        posts: List[Post] = []
        done_conv: set[str] = set()
        reply_fetches = 0
        for tweet, meta in parsed:
            if len(posts) >= count:
                break
            conv = meta["conv_id"]
            if conv in done_conv:
                continue
            group = conv_groups[conv]
            # 그룹 안에 작성자 자신에게 단 답글이 있으면 self-reply 스레드다
            is_thread = any(
                gm["reply_to_user"] and gm["reply_to_user"] == gm["author_id"]
                for _, gm in group
            )
            if is_thread:
                done_conv.add(conv)
                post = self._build_thread_post(conv, group)
            else:
                post = self._parse_single_tweet(tweet)
                # 단독 트윗은 스레드와 달리 TweetDetail을 부르지 않았다. 반응이 있는 것만
                # 회차 상한 안에서 재조회해 답글을 채운다.
                if (
                    post
                    and not self._skip_extra_reply_fetch
                    and reply_fetches < MAX_REPLY_FETCHES_PER_RUN
                    and (post.comments or 0) >= MIN_REPLIES_FOR_FETCH
                ):
                    reply_fetches += 1
                    post.content_markdown = append_comment_section(
                        post.content_markdown or post.content,
                        self._reply_section(conv, meta["author_id"]),
                    )
            if post:
                posts.append(post)
                if self.debug_mode:
                    typer.echo(f"  @{post.author}: {post.content[:60]}...")

        return posts[:count]

    def _tweet_meta(self, tweet: dict) -> Optional[dict]:
        """스레드 그룹핑에 필요한 최소 메타 추출"""
        legacy = tweet.get("legacy", {})
        tweet_id = legacy.get("id_str")
        if not legacy or not tweet_id:
            return None
        core = tweet.get("core", {}).get("user_results", {}).get("result", {})
        return {
            "id": tweet_id,
            "conv_id": legacy.get("conversation_id_str") or tweet_id,
            "reply_to_status": legacy.get("in_reply_to_status_id_str"),
            "reply_to_user": legacy.get("in_reply_to_user_id_str"),
            "author_id": core.get("rest_id"),
            "author_screen": core.get("legacy", {}).get("screen_name", "Unknown"),
            "is_rt": legacy.get("full_text", "").startswith("RT @"),
            "created_at": legacy.get("created_at", ""),
        }

    def _reply_section(
        self,
        conv_id: str,
        root_author_id: Optional[str],
        source_tweets: Optional[list[dict]] = None,
    ) -> Optional[str]:
        """답글 섹션을 만들되 절대 예외를 위로 올리지 않는다.

        이 함수는 `_parse_tweets` 루프 안에서 불린다. 상류 응답 구조가 바뀌어 파싱이
        터지면 그 회차의 트윗이 통째로 유실되므로, 답글만 포기하고 본문은 살린다.
        """
        try:
            return self._build_reply_section(conv_id, root_author_id, source_tweets)
        except Exception as exc:  # noqa: BLE001 - 답글 실패가 게시물 저장을 막지 않는다
            typer.echo(f"   [!] X 답글 파싱 실패: {exc}")
            return None

    def _build_reply_section(
        self,
        conv_id: str,
        root_author_id: Optional[str],
        source_tweets: Optional[list[dict]] = None,
    ) -> Optional[str]:
        """conversation에 달린 타인 답글을 본문용 섹션으로 만든다.

        `source_tweets`가 오면 이미 받은 TweetDetail 응답을 재사용해 요청이 늘지 않는다.
        스레드 병합이 작성자 본인 트윗만 쓰고 나머지를 버리던 자리를 그대로 활용한다.
        """
        tweets = (
            source_tweets
            if source_tweets is not None
            else self._fetch_thread_detail(conv_id)
        )
        if not tweets:
            return None

        collected: list[Comment] = []
        for tweet in tweets:
            meta = self._tweet_meta(tweet)
            if not meta or meta["is_rt"]:
                continue
            # TweetDetail 응답에는 이 대화와 무관한 추천/광고 트윗도 섞여 온다.
            # conversation_id가 다르면 답글이 아니다.
            if meta["conv_id"] != conv_id:
                continue
            if meta["id"] == conv_id or meta["author_id"] == root_author_id:
                continue
            content, _, _ = self._clean_content_and_media(tweet)
            if not content:
                continue
            legacy = tweet.get("legacy", {})
            collected.append(
                Comment(
                    author=f"@{meta['author_screen']}",
                    text=content,
                    score=legacy.get("favorite_count"),
                    created=(self._parse_timestamp(meta["created_at"]) or "")[
                        :16
                    ].replace("T", " ")
                    or None,
                )
            )

        # API는 시간순으로 주지만 읽는 쪽에는 반응이 큰 답글이 먼저인 편이 낫다.
        collected.sort(key=lambda comment: comment.score or 0, reverse=True)
        return render_comment_section(
            "X Replies", collected, max_comments=MAX_REPLIES, score_unit="like"
        )

    def _build_thread_post(
        self, conv_id: str, fallback_group: list[tuple[dict, dict]]
    ) -> Optional[Post]:
        """self-reply 스레드를 하나의 Post로 병합한다.

        conversation 루트(id == conv_id)로 TweetDetail을 재조회해 작성자 본인의
        모든 self-reply를 시간순(트윗 id 오름차순 = snowflake 시간순)으로 이어붙인다.
        재조회가 실패하거나 비면 응답에 이미 담긴 조각으로 병합한다.
        """
        detail = self._fetch_thread_detail(conv_id)
        source = detail if detail else [tweet for tweet, _ in fallback_group]

        indexed: dict[str, tuple[dict, dict]] = {}
        for tweet in source:
            meta = self._tweet_meta(tweet)
            if meta and not meta["is_rt"]:
                indexed[meta["id"]] = (tweet, meta)
        if not indexed:
            return None

        # 스레드 주인 = 루트 트윗 작성자(없으면 가장 이른 트윗 작성자)
        root = indexed.get(conv_id) or min(
            indexed.values(), key=lambda x: int(x[1]["id"])
        )
        root_author = root[1]["author_id"]

        own = [pair for pair in indexed.values() if pair[1]["author_id"] == root_author]
        own.sort(key=lambda pair: int(pair[1]["id"]))
        if len(own) <= 1:
            # 이어지는 self-reply가 실제로 없으면 단독 트윗으로 처리.
            # 재조회는 이미 끝났으니 답글은 추가 요청 없이 붙인다.
            post = self._parse_single_tweet(root[0])
            if post:
                post.content_markdown = append_comment_section(
                    post.content_markdown or post.content,
                    self._reply_section(conv_id, root_author, source),
                )
            return post

        texts: list[str] = []
        image_urls: list[str] = []
        statuses: list[str] = []
        for tweet, _ in own:
            content, images, status = self._clean_content_and_media(tweet)
            if content:
                texts.append(content)
                if status:
                    statuses.append(status)
            image_urls.extend(images)
        if not texts:
            return None
        # 스레드 전체가 폴백 본문일 때만 표시한다. 한 조각만 미디어면 나머지
        # 텍스트가 본문을 이루므로 폴백이라고 부를 수 없다.
        thread_status = statuses[0] if len(statuses) == len(texts) else None
        content = "\n\n---\n\n".join(texts) if len(texts) > 1 else texts[0]

        root_tweet, root_meta = root
        author = root_meta["author_screen"]
        tweet_id = root_meta["id"]
        root_legacy = root_tweet.get("legacy", {})
        views = root_tweet.get("views", {}).get("count")
        return Post(
            platform="x",
            author=author,
            content=content,
            content_markdown=append_comment_section(
                content, self._reply_section(conv_id, root_author, source)
            ),
            timestamp=self._parse_timestamp(root_meta["created_at"]),
            url=f"https://x.com/{author}/status/{tweet_id}" if tweet_id else None,
            likes=root_legacy.get("favorite_count", 0),
            comments=root_legacy.get("reply_count", 0),
            reposts=root_legacy.get("retweet_count", 0),
            views=int(views) if views else None,
            external_id=tweet_id or None,
            **({"images": list(dict.fromkeys(image_urls))} if image_urls else {}),
            **({"content_status": thread_status} if thread_status else {}),
        )

    def _fetch_thread_detail(self, conv_id: str) -> Optional[list[dict]]:
        """미리 받아 둔 TweetDetail의 트윗 목록. 계획 단계에서는 필요한 대화만 적는다."""
        if self._wanted is not None:
            self._wanted.append(conv_id)
            return None
        return (self._details or {}).get(conv_id)

    def _extract_tweet_results(self, entry: dict) -> list[dict]:
        """중첩된 응답에서 tweet result 객체 추출"""
        results = []

        def _find_tweets(obj):
            if isinstance(obj, dict):
                # tweet result 패턴: legacy + core 포함
                if "legacy" in obj and "core" in obj:
                    results.append(obj)
                    return
                # tweet_results 키 확인
                if "tweet_results" in obj:
                    result = obj["tweet_results"].get("result", {})
                    if result:
                        # quoted tweet이 아닌 원본만
                        if "legacy" in result:
                            results.append(result)
                        elif "tweet" in result and "legacy" in result["tweet"]:
                            results.append(result["tweet"])
                    return
                for v in obj.values():
                    _find_tweets(v)
            elif isinstance(obj, list):
                for item in obj:
                    _find_tweets(item)

        _find_tweets(entry)
        return results

    def _parse_single_tweet(self, tweet: dict) -> Optional[Post]:
        """단일 트윗을 Post 모델로 변환"""
        try:
            legacy = tweet.get("legacy", {})
            if not legacy:
                return None

            # 리트윗 제외
            if legacy.get("full_text", "").startswith("RT @"):
                return None

            # 작성자
            core = tweet.get("core", {})
            user_results = core.get("user_results", {}).get("result", {})
            user_legacy = user_results.get("legacy", {})
            author = user_legacy.get("screen_name", "Unknown")

            # 타임스탬프
            created_at = legacy.get("created_at", "")
            timestamp = self._parse_timestamp(created_at)

            # URL
            tweet_id = legacy.get("id_str") or ""
            url = f"https://x.com/{author}/status/{tweet_id}" if tweet_id else None

            # 상호작용
            likes = legacy.get("favorite_count", 0)
            comments = legacy.get("reply_count", 0)
            reposts = legacy.get("retweet_count", 0)
            views = tweet.get("views", {}).get("count")
            if views:
                views = int(views)

            content, image_urls, content_status = self._clean_content_and_media(tweet)
            if content is None:
                return None

            return Post(
                platform="x",
                author=author,
                content=content,
                timestamp=timestamp,
                url=url,
                likes=likes,
                comments=comments,
                reposts=reposts,
                views=views,
                external_id=tweet_id or None,
                content_status=content_status,
                **({"images": list(dict.fromkeys(image_urls))} if image_urls else {}),
            )

        except Exception as e:
            # 스키마 변경으로 파싱이 깨져도 조용히 누락되지 않게 항상 알린다.
            typer.echo(f"  [!] 트윗 파싱 오류(건너뜀): {e}")
            return None

    def _clean_content_and_media(
        self, tweet: dict
    ) -> tuple[Optional[str], list[str], Optional[str]]:
        """트윗 본문에서 t.co 링크를 실제 URL로 펴고 미디어 URL을 정리한다.

        Returns:
            (content, image_urls, content_status).
            본문이 비고 대체할 미디어도 없으면 content는 None.
        """
        legacy = tweet.get("legacy", {})
        content = legacy.get("full_text", "")

        # t.co URL을 실제 URL로 교체
        for u in legacy.get("entities", {}).get("urls", []):
            short = u.get("url", "")
            expanded = u.get("expanded_url", "")
            if short and expanded:
                content = content.replace(short, expanded)

        # 미디어 URL 제거
        media = []
        for media_container in (
            legacy.get("entities", {}),
            legacy.get("extended_entities", {}),
        ):
            media.extend(media_container.get("media") or [])
        media_fallbacks = []
        media_alt_texts = []
        image_urls = []
        for m in media:
            media_url = m.get("url", "")
            alt_text = (m.get("ext_alt_text") or "").strip()
            if alt_text:
                media_alt_texts.append(alt_text)
            # 사진 첨부의 CDN 원본 URL(pbs.twimg.com)을 보존한다
            if m.get("type") == "photo" and m.get("media_url_https"):
                image_urls.append(m["media_url_https"])
            media_link = (
                m.get("expanded_url")
                or m.get("display_url")
                or m.get("media_url_https")
            )
            if media_link:
                media_fallbacks.append(media_link)
            if media_url:
                content = content.replace(media_url, "").strip()

        content = content.strip()
        content_status = None
        if not content:
            if media_alt_texts:
                content = "\n".join(media_alt_texts)
                content_status = "media_alt_text"
            elif media_fallbacks:
                content = "\n".join(dict.fromkeys(media_fallbacks))
                content_status = "media_link"
            else:
                return None, [], None
        return content, image_urls, content_status

    def _parse_timestamp(self, created_at: str) -> str:
        """X 타임스탬프 파싱 (예: 'Wed Oct 10 20:19:24 +0000 2018')"""
        if not created_at:
            return ""
        try:
            dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
            return dt.isoformat(timespec="seconds")
        except ValueError:
            return created_at
