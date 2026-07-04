"""
@file x_api.py
@description X (Twitter) API 기반 크롤러 (브라우저 없이 동작)

twitter-api-client 라이브러리를 사용하여 X 게시글을 수집합니다.
CDP로 추출한 세션 쿠키를 재사용하여 인증합니다.

주요 기능:
1. 브라우저 없이 For You 타임라인 피드 수집
2. 특정 사용자 트윗 수집
3. 페이지네이션 지원

@dependencies
- twitter-api-client: X GraphQL API 클라이언트
- typer: CLI 출력
"""

import asyncio
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, List, Optional

import typer

from ...models import Post
from ...paths import SESSIONS_DIR

SESSION_PATH = SESSIONS_DIR / "x_session.json"


class XAPICrawler:
    """
    X (Twitter) API 기반 크롤러

    twitter-api-client를 사용하여 브라우저 없이 X 게시글을 수집합니다.
    CDP로 추출한 세션 쿠키를 재사용합니다.
    """

    platform = "x"

    def __init__(self, debug_mode: bool = False):
        self.platform_name = "X"
        self.debug_mode = debug_mode
        self.session_path = SESSION_PATH
        self._scraper = None
        self._setup_client()

    def _setup_client(self) -> None:
        """세션 쿠키 로드 및 API 클라이언트 설정"""
        cookies = self._load_cookies()
        if not cookies:
            typer.echo("세션 쿠키가 없습니다. 먼저 로그인하세요:")
            typer.echo("  uv run skim login x")
            raise typer.Exit(1)

        ct0 = cookies.get("ct0")
        auth_token = cookies.get("auth_token")

        if not ct0 or not auth_token:
            typer.echo("필수 쿠키(ct0, auth_token)가 없습니다. 재로그인하세요:")
            typer.echo("  uv run skim login x")
            raise typer.Exit(1)

        try:
            from twitter.account import Account  # pylint: disable=import-outside-toplevel

            self.account = Account(
                cookies={"ct0": ct0, "auth_token": auth_token},
                debug=1 if self.debug_mode else 0,
                save=False,
            )
        except Exception as e:
            typer.echo(f"X API 클라이언트 초기화 실패: {e}")
            typer.echo("세션이 만료되었을 수 있습니다. 재로그인하세요:")
            typer.echo("  uv run skim login x")
            raise typer.Exit(1)

        if self.debug_mode:
            # 시크릿 값은 일부라도 로그에 남기지 않는다.
            typer.echo(f"ct0: {len(ct0)}자, auth_token: {len(auth_token)}자 로드됨")

    def _load_cookies(self) -> Dict[str, str]:
        """세션 파일에서 쿠키 로드"""
        if not self.session_path.exists():
            return {}

        with open(self.session_path, "r", encoding="utf-8") as f:
            storage_state = json.load(f)

        cookies = {}
        for cookie in storage_state.get("cookies", []):
            domain = cookie.get("domain", "")
            if "x.com" in domain or "twitter.com" in domain:
                cookies[cookie["name"]] = cookie["value"]

        return cookies

    async def crawl(self, **options) -> List[Post]:
        count = options.get("count", 10)
        user_id = options.get("user_id")
        return await self._crawl_impl(count, user_id)

    async def _crawl_impl(self, count: int = 10, user_id: Optional[str] = None) -> List[Post]:
        """
        X 게시글 크롤링

        Args:
            count: 수집할 게시글 수
            user_id: 특정 사용자 screen name (없으면 For You 타임라인)

        Returns:
            크롤링된 게시글 목록
        """
        mode = f"사용자 @{user_id}" if user_id else "For You 타임라인"
        typer.echo(f"[API 모드] {self.platform_name} 크롤링 시작 - {mode} (게시글 {count}개)")

        try:
            if user_id:
                raw_data = self._fetch_user_tweets(user_id, count)
            else:
                raw_data = self._fetch_timeline(count)
        except Exception as e:
            typer.echo(f"API 요청 실패: {e}")
            if "401" in str(e) or "unauthorized" in str(e).lower():
                typer.echo("세션이 만료되었습니다. 재로그인하세요:")
                typer.echo("  uv run skim login x")
            return []

        posts = self._parse_tweets(raw_data, count)
        typer.echo(f"총 {len(posts)}개의 게시글을 추출했습니다.")
        return posts

    def _fetch_timeline(self, count: int) -> list[dict]:
        """For You 타임라인 가져오기"""
        if self.debug_mode:
            typer.echo("  타임라인 요청 중...")
        return self.account.home_timeline(limit=count)

    def _fetch_user_tweets(self, screen_name: str, count: int) -> list[dict]:
        """특정 사용자의 트윗 가져오기"""
        from twitter.scraper import Scraper  # pylint: disable=import-outside-toplevel

        if self.debug_mode:
            typer.echo(f"  @{screen_name} 트윗 요청 중...")

        cookies = self._load_cookies()
        scraper = Scraper(
            cookies={"ct0": cookies["ct0"], "auth_token": cookies["auth_token"]},
            debug=1 if self.debug_mode else 0,
            save=False,
        )

        # screen_name → user_id 변환
        users = self._run_off_loop(lambda: scraper.users([screen_name]))
        if not users:
            typer.echo(f"  사용자 @{screen_name}을 찾을 수 없습니다")
            return []

        user_data = users[0]
        uid = self._extract_user_id(user_data)
        if not uid:
            typer.echo("  사용자 ID를 추출할 수 없습니다")
            return []

        if self.debug_mode:
            typer.echo(f"  user_id: {uid}")

        return self._run_off_loop(lambda: scraper.tweets([uid], limit=count))

    def _run_off_loop(self, fn: Callable):
        """Scraper._run은 asyncio.run()을 쓰므로 실행 중인 이벤트 루프 안(예: CLI의
        asyncio.run → crawl)에서 직접 호출하면 RuntimeError가 난다. 루프가 돌고
        있으면 별도 스레드에서 실행해 격리한다."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return fn()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(fn).result()

    def _extract_user_id(self, user_data: dict) -> Optional[int]:
        """중첩된 응답에서 user_id 추출"""
        try:
            # 재귀적으로 rest_id 찾기
            return int(self._find_key(user_data, "rest_id"))
        except (ValueError, TypeError):
            return None

    def _find_key(self, obj, key: str):
        """딕셔너리에서 재귀적으로 키 찾기"""
        if isinstance(obj, dict):
            if key in obj:
                val = obj[key]
                # rest_id는 숫자 문자열이어야 함
                if key == "rest_id" and isinstance(val, str) and val.isdigit():
                    return val
            for v in obj.values():
                result = self._find_key(v, key)
                if result is not None:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_key(item, key)
                if result is not None:
                    return result
        return None

    def _parse_tweets(self, raw_data: list[dict], count: int) -> List[Post]:
        """API 응답을 Post 모델로 변환

        같은 작성자가 self-reply로 이어단 스레드(줄줄이 글)는 하나의 Post로 병합한다.
        스레드로 판명되면 conversation 전체를 TweetDetail로 재조회해 응답에 잘려 온
        뒷부분까지 완전 복원하고, 재조회 실패 시 응답 내 조각으로 폴백한다.
        """
        tweets: list[dict] = []
        for entry in raw_data:
            tweets.extend(self._extract_tweet_results(entry))

        # 트윗별 메타(작성자/대화 id/self-reply 여부) 부착, RT·파싱불가 제외
        parsed: list[tuple[dict, dict]] = []
        for tweet in tweets:
            meta = self._tweet_meta(tweet)
            if meta and not meta["is_rt"]:
                parsed.append((tweet, meta))

        # conversation(스레드) 단위로 응답 내 조각을 모은다
        conv_groups: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
        for tweet, meta in parsed:
            conv_groups[meta["conv_id"]].append((tweet, meta))

        posts: List[Post] = []
        done_conv: set[str] = set()
        for tweet, meta in parsed:
            if len(posts) >= count:
                break
            conv = meta["conv_id"]
            if conv in done_conv:
                continue
            group = conv_groups[conv]
            # 그룹 안에 작성자 자신에게 단 답글이 있으면 self-reply 스레드다
            is_thread = any(
                gm["reply_to_user"] and gm["reply_to_user"] == gm["author_id"] for _, gm in group
            )
            if is_thread:
                done_conv.add(conv)
                post = self._build_thread_post(conv, group)
            else:
                post = self._parse_single_tweet(tweet)
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
        root = indexed.get(conv_id) or min(indexed.values(), key=lambda x: int(x[1]["id"]))
        root_author = root[1]["author_id"]

        own = [pair for pair in indexed.values() if pair[1]["author_id"] == root_author]
        own.sort(key=lambda pair: int(pair[1]["id"]))
        if len(own) <= 1:
            # 이어지는 self-reply가 실제로 없으면 단독 트윗으로 처리
            return self._parse_single_tweet(root[0])

        texts: list[str] = []
        image_urls: list[str] = []
        for tweet, _ in own:
            content, images, _ = self._clean_content_and_media(tweet)
            if content:
                texts.append(content)
            image_urls.extend(images)
        if not texts:
            return None
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
            timestamp=self._parse_timestamp(root_meta["created_at"]),
            url=f"https://x.com/{author}/status/{tweet_id}" if tweet_id else None,
            likes=root_legacy.get("favorite_count", 0),
            comments=root_legacy.get("reply_count", 0),
            reposts=root_legacy.get("retweet_count", 0),
            views=int(views) if views else None,
            external_id=tweet_id or None,
            **({"images": list(dict.fromkeys(image_urls))} if image_urls else {}),
        )

    def _fetch_thread_detail(self, conv_id: str) -> Optional[list[dict]]:
        """TweetDetail로 conversation 전체를 재조회해 트윗 객체 목록을 반환한다."""
        try:
            scraper = self._get_scraper()
            if scraper is None:
                return None
            detail = self._run_off_loop(lambda: scraper.tweets_details([int(conv_id)]))
        except Exception as e:  # noqa: BLE001 - 재조회 실패는 폴백으로 흡수
            if self.debug_mode:
                typer.echo(f"  스레드 상세 조회 실패(응답 내 조각으로 폴백): {e}")
            return None
        tweets: list[dict] = []
        for entry in detail or []:
            tweets.extend(self._extract_tweet_results(entry))
        return tweets or None

    def _get_scraper(self):
        """TweetDetail 재조회용 Scraper (세션 쿠키 재사용, lazy 생성)"""
        if getattr(self, "_scraper", None) is not None:
            return self._scraper
        try:
            from twitter.scraper import Scraper  # pylint: disable=import-outside-toplevel

            cookies = self._load_cookies()
            self._scraper = Scraper(
                cookies={"ct0": cookies["ct0"], "auth_token": cookies["auth_token"]},
                debug=1 if self.debug_mode else 0,
                save=False,
            )
        except Exception:  # noqa: BLE001 - Scraper 불가 시 재조회 스킵
            self._scraper = None
        return self._scraper

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
            media_link = m.get("expanded_url") or m.get("display_url") or m.get("media_url_https")
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
