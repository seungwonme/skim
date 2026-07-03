"""
@file reddit.py
@description Reddit listing API 기반 크롤러

브라우저 DOM 파싱 없이 Reddit의 JSON listing 엔드포인트를 사용해 게시글을 수집합니다.
서브레딧 수집은 verification challenge를 풀어 비로그인 상태에서도 동작하고,
홈 피드 수집은 저장된 로그인 세션 쿠키를 재사용합니다.
"""

import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests
import typer

from ...models import Post
from ...paths import SESSIONS_DIR

SESSION_PATH = SESSIONS_DIR / "reddit_session.json"
REDDIT_BASE_URL = "https://www.reddit.com"
REDDIT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
HOME_FEED_COOKIE_NAMES = {"reddit_session", "token_v2", "session_tracker", "loid", "csrf_token"}
VERIFICATION_TITLE = "please wait for verification"


class RedditAPICrawler:
    """Reddit listing API 기반 크롤러."""

    platform = "reddit"

    def __init__(
        self,
        debug_mode: bool = False,
        session_path: Path = SESSION_PATH,
        session: Optional[requests.Session] = None,
    ):
        self.platform_name = "Reddit"
        self.debug_mode = debug_mode
        self.session_path = session_path
        self.session = session or requests.Session()
        self._owns_session = session is None
        self.session.headers.update(
            {
                "User-Agent": REDDIT_USER_AGENT,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            }
        )
        self._has_login_session = False
        self._load_session_cookies()

    async def crawl(self, **options) -> List[Post]:
        count = options.get("count", 10)
        subreddit = options.get("subreddit")
        sort = options.get("sort", "hot")
        try:
            return self.fetch_listing(count=count, subreddit=subreddit, sort=sort)
        finally:
            # 크롤러 인스턴스는 1회성이다. 직접 만든 세션은 커넥션 풀을 정리한다.
            if self._owns_session:
                self.session.close()

    def fetch_listing(
        self,
        *,
        count: int,
        subreddit: Optional[str],
        sort: str,
    ) -> List[Post]:
        """서브레딧 또는 홈 피드 listing을 수집합니다."""
        normalized_subreddit = self.normalize_subreddit(subreddit)
        normalized_sort = self.normalize_sort(sort)

        if not normalized_subreddit and not self._has_login_session:
            typer.echo("레딧 홈 피드는 로그인 세션이 필요합니다. 먼저 로그인하세요:")
            typer.echo("  uv run skim login reddit")
            raise typer.Exit(1)

        posts: List[Post] = []
        after: Optional[str] = None

        # 전량 필터된 페이지가 이어져도 무한 호출하지 않게 페이지 상한을 둔다.
        max_pages = 10
        for _ in range(max_pages):
            if len(posts) >= count:
                break
            remaining = count - len(posts)
            url = self.build_listing_url(
                subreddit=normalized_subreddit,
                sort=normalized_sort,
                limit=min(remaining, 100),
                after=after,
            )
            try:
                listing = self.fetch_listing_page(url)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if normalized_subreddit and status_code == 403:
                    return self.fetch_subreddit_rss(
                        count=count,
                        subreddit=normalized_subreddit,
                        sort=normalized_sort,
                    )
                raise
            page_posts, after = self.parse_listing(listing)
            posts.extend(page_posts)

            # 페이지가 전량 필터돼도 after cursor가 남아 있으면 다음 페이지를 이어서 본다.
            if not after:
                break

        return posts[:count]

    def build_listing_url(
        self,
        *,
        subreddit: Optional[str],
        sort: str,
        limit: int,
        after: Optional[str] = None,
    ) -> str:
        """listing 요청 URL을 생성합니다."""
        if subreddit:
            path = f"/r/{subreddit}/{sort}.json"
        else:
            path = "/best.json"

        query_items = [("limit", str(limit)), ("raw_json", "1")]
        if after:
            query_items.append(("after", after))

        return f"{REDDIT_BASE_URL}{path}?{urlencode(query_items)}"

    def build_subreddit_rss_url(self, *, subreddit: str, sort: str, limit: int) -> str:
        """Reddit JSON listing이 막힐 때 사용할 subreddit Atom feed URL."""
        sort_path = "" if sort == "hot" else f"/{sort}"
        path = f"/r/{subreddit}{sort_path}/.rss"
        return f"{REDDIT_BASE_URL}{path}?{urlencode([('limit', str(limit))])}"

    def fetch_listing_page(self, url: str) -> dict:
        """verification challenge 처리까지 포함해 listing JSON을 가져옵니다."""
        response = self.session.get(url, timeout=15)
        response.raise_for_status()

        if self.is_verification_page(response.text):
            self.solve_verification_challenge(url, response.text)
            response = self.session.get(url, timeout=15)
            response.raise_for_status()

        if not self.is_json_response(response):
            raise ValueError(f"Reddit listing JSON 응답을 받지 못했습니다: {response.url}")

        return response.json()

    def fetch_subreddit_rss(self, *, count: int, subreddit: str, sort: str) -> List[Post]:
        """비로그인 subreddit JSON endpoint가 403일 때 Atom feed로 fallback."""
        url = self.build_subreddit_rss_url(subreddit=subreddit, sort=sort, limit=count)
        response = self.session.get(url, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        posts: List[Post] = []
        for entry in feed.entries[:count]:
            post = self.parse_rss_entry(entry, subreddit)
            if post:
                posts.append(post)
        return posts

    @staticmethod
    def is_verification_page(html: str) -> bool:
        """verification 페이지 여부를 판별합니다."""
        normalized = html.lower()
        return VERIFICATION_TITLE in normalized and 'name="token"' in normalized

    @staticmethod
    def is_json_response(response: requests.Response) -> bool:
        """JSON 응답 여부를 확인합니다."""
        content_type = response.headers.get("content-type", "").lower()
        return "application/json" in content_type or response.text.lstrip().startswith("{")

    def solve_verification_challenge(self, url: str, html: str) -> None:
        """Reddit verification challenge를 풀어 세션 쿠키를 확보합니다."""
        challenge_data = self.extract_challenge_data(html)
        challenge_url = self.append_query_params(
            url,
            {
                "solution": challenge_data["solution"],
                "js_challenge": "1",
                "token": challenge_data["token"],
            },
        )
        challenge_response = self.session.get(challenge_url, timeout=15)
        challenge_response.raise_for_status()

    @staticmethod
    def extract_challenge_data(html: str) -> Dict[str, str]:
        """verification 페이지에서 challenge seed와 token을 추출합니다."""
        seed_match = re.search(r'await\s*\(async e=>e\+e\)\("([^"]+)"\)', html)
        token_match = re.search(r'name="token"\s+value="([^"]+)"', html)

        if not seed_match or not token_match:
            raise ValueError("Reddit verification challenge를 해석하지 못했습니다.")

        seed = seed_match.group(1)
        return {
            "seed": seed,
            "solution": f"{seed}{seed}",
            "token": token_match.group(1),
        }

    @staticmethod
    def append_query_params(url: str, params: Dict[str, str]) -> str:
        """기존 URL query string 뒤에 challenge 파라미터를 덧붙입니다."""
        parsed = urlparse(url)
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        query_items.extend(params.items())
        return urlunparse(parsed._replace(query=urlencode(query_items)))

    def parse_listing(self, listing: dict) -> tuple[List[Post], Optional[str]]:
        """listing JSON을 Post 목록과 다음 페이지 cursor로 변환합니다."""
        data = listing.get("data", {})
        posts: List[Post] = []

        for child in data.get("children", []):
            post_data = child.get("data", {})
            post = self.parse_post(post_data)
            if post:
                posts.append(post)

        return posts, data.get("after")

    def parse_post(self, post_data: dict) -> Optional[Post]:
        """Reddit 단일 listing item을 Post로 변환합니다."""
        external_id = post_data.get("id")
        title = (post_data.get("title") or "").strip()
        content = (post_data.get("selftext") or "").strip() or title

        if not external_id or not content:
            return None

        permalink = post_data.get("permalink")
        url = self.build_post_url(permalink) if permalink else post_data.get("url")
        created_utc = post_data.get("created_utc")

        timestamp = ""
        if created_utc:
            timestamp = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()

        return Post(
            platform="reddit",
            author=post_data.get("author", "unknown"),
            title=title or None,
            content=content,
            timestamp=timestamp,
            url=url,
            likes=post_data.get("score"),
            comments=post_data.get("num_comments"),
            source=post_data.get("subreddit_name_prefixed"),
            external_id=external_id,
            subreddit=post_data.get("subreddit"),
            subreddit_name_prefixed=post_data.get("subreddit_name_prefixed"),
            upvote_ratio=post_data.get("upvote_ratio"),
            is_self=post_data.get("is_self"),
            over_18=post_data.get("over_18"),
        )

    def parse_rss_entry(self, entry: dict, subreddit: str) -> Optional[Post]:
        """Reddit Atom entry를 Post로 변환합니다."""
        raw_id = entry.get("id", "")
        external_id = raw_id.removeprefix("t3_") if raw_id else ""
        title = (entry.get("title") or "").strip()
        content_html = entry.get("summary") or entry.get("content", [{}])[0].get("value", "")
        content = self.strip_html(content_html) or title
        link = entry.get("link") or ""

        if not external_id or not content:
            return None

        timestamp = ""
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed:
            timestamp = datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()

        author = entry.get("author", "unknown").removeprefix("/u/")

        return Post(
            platform="reddit",
            author=author,
            title=title or None,
            content=content,
            timestamp=timestamp,
            url=link,
            source=f"r/{subreddit}",
            external_id=external_id,
            subreddit=subreddit,
            subreddit_name_prefixed=f"r/{subreddit}",
        )

    @staticmethod
    def strip_html(value: str) -> str:
        """HTML content를 검색 가능한 plain text로 축약합니다."""
        text = unescape(value or "")
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def build_post_url(permalink: str) -> str:
        """permalink를 절대 URL로 변환합니다."""
        if permalink.startswith("http"):
            return permalink
        return f"{REDDIT_BASE_URL}{permalink}"

    @staticmethod
    def normalize_subreddit(subreddit: Optional[str]) -> Optional[str]:
        """입력 subreddit slug를 정규화합니다."""
        if not subreddit:
            return None

        normalized = subreddit.strip()
        if normalized.startswith("r/"):
            normalized = normalized[2:]
        if normalized.startswith("/r/"):
            normalized = normalized[3:]
        return normalized.strip("/")

    @staticmethod
    def normalize_sort(sort: str) -> str:
        """지원하는 정렬값으로 정규화합니다."""
        normalized = (sort or "hot").lower()
        if normalized not in {"hot", "new"}:
            raise ValueError(f"지원하지 않는 reddit 정렬값입니다: {sort}")
        return normalized

    def _load_session_cookies(self) -> None:
        """Playwright storage_state의 reddit 쿠키를 requests 세션으로 옮깁니다."""
        if not self.session_path.exists():
            return

        storage_state = json.loads(self.session_path.read_text(encoding="utf-8"))
        cookie_names = set()
        for cookie in storage_state.get("cookies", []):
            domain = cookie.get("domain", "")
            if "reddit.com" not in domain:
                continue

            self.session.cookies.set(
                cookie.get("name", ""),
                cookie.get("value", ""),
                domain=domain,
                path=cookie.get("path", "/"),
            )
            if cookie.get("name"):
                cookie_names.add(cookie["name"])

        self._has_login_session = bool(cookie_names & HOME_FEED_COOKIE_NAMES)
