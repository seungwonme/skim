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

import json
from datetime import datetime
from typing import Dict, List, Optional

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
        users = scraper.users([screen_name])
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

        return scraper.tweets([uid], limit=count)

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
        """API 응답을 Post 모델로 변환"""
        posts: List[Post] = []

        for entry in raw_data:
            tweet_results = self._extract_tweet_results(entry)
            for tweet in tweet_results:
                post = self._parse_single_tweet(tweet)
                if post:
                    posts.append(post)
                    if self.debug_mode:
                        typer.echo(f"  @{post.author}: {post.content[:60]}...")
                    if len(posts) >= count:
                        return posts

        return posts

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

            # 콘텐츠
            content = legacy.get("full_text", "")
            if not content:
                return None

            # 리트윗 제외
            if content.startswith("RT @"):
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

            # t.co URL을 실제 URL로 교체
            urls = legacy.get("entities", {}).get("urls", [])
            for u in urls:
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
            for m in media:
                media_url = m.get("url", "")
                alt_text = (m.get("ext_alt_text") or "").strip()
                if alt_text:
                    media_alt_texts.append(alt_text)
                media_link = (
                    m.get("expanded_url") or m.get("display_url") or m.get("media_url_https")
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
            )

        except Exception as e:
            # 스키마 변경으로 파싱이 깨져도 조용히 누락되지 않게 항상 알린다.
            typer.echo(f"  [!] 트윗 파싱 오류(건너뜀): {e}")
            return None

    def _parse_timestamp(self, created_at: str) -> str:
        """X 타임스탬프 파싱 (예: 'Wed Oct 10 20:19:24 +0000 2018')"""
        if not created_at:
            return ""
        try:
            dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
            return dt.isoformat(timespec="seconds")
        except ValueError:
            return created_at
