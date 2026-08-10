"""
@file linkedin_api.py
@description LinkedIn Voyager API 기반 크롤러

저장된 Playwright 세션 쿠키를 requests 세션으로 옮겨 LinkedIn Voyager feed
endpoint를 호출합니다. DOM 파싱 없이 normalized payload에서 게시글을 추출합니다.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urljoin, urlparse

import requests
import typer

from ...comments import Comment, append_comment_section, render_comment_section
from ...models import Post
from ...paths import DATA_DIR, SESSIONS_DIR

LINKEDIN_BASE_URL = "https://www.linkedin.com"
VOYAGER_FEED_URL = f"{LINKEDIN_BASE_URL}/voyager/api/feed/updatesV2"
VOYAGER_COMMENTS_URL = f"{LINKEDIN_BASE_URL}/voyager/api/feed/comments"
MAX_COMMENTS = 15
# 댓글은 게시글당 요청 1건이다. 호스트별 간격을 둬서 연속 호출로 눈에 띄지 않게 한다.
COMMENT_REQUEST_INTERVAL_SECONDS = 1.0
LINKEDIN_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
REQUIRED_COOKIE_NAMES = {"li_at", "JSESSIONID"}
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
SESSION_REJECTED_REASONS = {
    "login",
    "authwall",
    "checkpoint",
    "challenge",
    "self-redirect-loop",
}


def _classify_redirect(response: requests.Response) -> str:
    """redirect Location 경로로 세션 거부 원인을 분류합니다."""
    location = response.headers.get("location") or ""
    if not location:
        return "empty-redirect"
    absolute = urljoin(str(response.url), location)
    if absolute == str(response.url):
        return "self-redirect-loop"
    path = urlparse(absolute).path.lower()
    if "checkpoint" in path:
        return "checkpoint"
    if "login" in path:  # /uas/login 포함
        return "login"
    if "authwall" in path:
        return "authwall"
    if "challenge" in path or "/security" in path:
        return "challenge"
    return "redirect"


class LinkedInAPICrawler:
    """LinkedIn Voyager API 기반 크롤러."""

    platform = "linkedin"

    def __init__(
        self,
        debug_mode: bool = False,
        session_path: Path = SESSIONS_DIR / "linkedin_session.json",
        session: Optional[requests.Session] = None,
    ):
        self.platform_name = "LinkedIn"
        self.debug_mode = debug_mode
        self.session_path = session_path
        self.session = session or requests.Session()
        self._owns_session = session is None
        self.session.headers.update(
            {
                "Accept": "application/vnd.linkedin.normalized+json+2.1",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": f"{LINKEDIN_BASE_URL}/feed/",
                "User-Agent": LINKEDIN_USER_AGENT,
                "X-Li-Lang": "en_US",
                "X-Restli-Protocol-Version": "2.0.0",
            }
        )
        self._has_login_session = False
        self._load_session_cookies()

    async def crawl(self, **options) -> List[Post]:
        count = options.get("count", 5)
        no_content = options.get("no_content", False)
        try:
            posts = self.fetch_feed(count=count)
            if not no_content:
                self.attach_comments(posts)
            return posts
        finally:
            # 크롤러 인스턴스는 1회성이다. 직접 만든 세션은 커넥션 풀을 정리한다.
            if self._owns_session:
                self.session.close()

    def attach_comments(self, posts: List[Post]) -> None:
        """게시글별 댓글을 정본 본문 뒤에 잇는다. 게시글당 요청 1건이 늘어난다."""
        failures = 0
        for index, post in enumerate(posts):
            if (post.comments or 0) < 1:
                continue
            # 게시글당 요청 1건이라 호스트별 간격을 둔다 (reddit과 같은 패턴).
            if index:
                time.sleep(COMMENT_REQUEST_INTERVAL_SECONDS)
            # HTTP 실패는 fetch_comment_section 안에서 None이 된다. 여기서 잡는 건
            # Voyager 응답 구조가 바뀌었을 때의 파싱 실패다.
            try:
                section = self.fetch_comment_section(post.external_id)
            except Exception as exc:  # noqa: BLE001 - 댓글 실패가 게시글 저장을 막지 않는다
                failures += 1
                typer.echo(f"   [!] LinkedIn 댓글 파싱 실패: {exc}")
                continue
            if section is None and post.external_id:
                failures += 1
                continue
            body = post.content_markdown or post.content
            post.content_markdown = append_comment_section(body, section)

        if failures:
            typer.echo(f"   [!] LinkedIn 댓글 수집 실패 {failures}건 (본문만 저장)")

    def fetch_comment_section(self, external_id: Optional[str]) -> Optional[str]:
        """activity id의 댓글을 본문용 마크다운 섹션으로 만든다."""
        if not external_id:
            return None
        activity_id = self._extract_activity_id(str(external_id)) or str(external_id)
        if not activity_id.isdigit():
            return None

        try:
            response = self.session.get(
                VOYAGER_COMMENTS_URL,
                params={
                    "count": str(MAX_COMMENTS),
                    "q": "comments",
                    "sortOrder": "RELEVANCE",
                    "updateId": f"activity:{activity_id}",
                },
                allow_redirects=False,
                timeout=20,
            )
        except Exception:  # noqa: BLE001 - 댓글 실패가 게시글 저장을 막지 않는다
            return None

        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None

        # Voyager는 정규화 응답이라 실데이터가 elements가 아닌 included에 온다.
        collected: List[Comment] = []
        for entry in payload.get("included") or []:
            if not str(entry.get("$type", "")).endswith("feed.Comment"):
                continue
            # RELEVANCE 정렬은 답글을 부모보다 먼저 주기도 해서 그대로 담으면 순서가
            # 뒤엉킨다. 트리를 재구성할 만한 값이 아니라 최상위 댓글만 담는다.
            # spartan: 답글까지 필요해지면 parentCommentUrn으로 부모 뒤에 끼워 넣는다.
            if entry.get("parentCommentUrn"):
                continue
            text = self._extract_path(entry, "commentV2.text") or ""
            if not text:
                continue
            author = self._extract_path(entry, "commenterForDashConversion.title.text")
            created_ms = entry.get("createdTime")
            created = None
            if isinstance(created_ms, (int, float)):
                created = datetime.fromtimestamp(
                    created_ms / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")
            collected.append(
                Comment(author=author or "unknown", text=text, created=created)
            )

        return render_comment_section(
            "LinkedIn Comments", collected, max_comments=MAX_COMMENTS
        )

    def fetch_feed(self, *, count: int) -> List[Post]:
        """LinkedIn 홈 피드 게시글을 수집합니다."""
        typer.echo(f"[API 모드] LinkedIn 크롤링 시작... (게시글 {count}개)")

        if not self._has_login_session:
            typer.echo(
                "❌ 세션 파일이 없거나 LinkedIn 쿠키가 부족합니다. 먼저 로그인하세요:"
            )
            typer.echo("  uv run skim login linkedin")
            return []

        posts: List[Post] = []
        seen: set[str] = set()
        start = 0
        max_iterations = 5

        for _ in range(max_iterations):
            if len(posts) >= count:
                break

            request_count = min(max(count - len(posts) + 5, 1), 100)
            raw = self._fetch_feed_page(start=start, count=request_count)
            if not raw:
                break

            page_posts = self._parse_response(raw)
            if not page_posts:
                break

            for post in page_posts:
                key = post.external_id or f"{post.author}:{post.content[:100]}"
                if key in seen:
                    continue
                seen.add(key)
                posts.append(post)
                if len(posts) >= count:
                    break

            # ponytail: offset paging is enough for small CLI crawls; add cursor paging if needed.
            start += request_count

        typer.echo(f"📊 총 {len(posts)}개의 게시글을 추출했습니다.")
        return posts[:count]

    def _fetch_feed_page(self, *, start: int, count: int) -> Optional[dict]:
        """Voyager feed endpoint를 호출합니다."""
        response = self.session.get(
            VOYAGER_FEED_URL,
            params={"count": str(count), "q": "chronFeed", "start": str(start)},
            allow_redirects=False,
            timeout=20,
        )

        if response.status_code in REDIRECT_STATUS_CODES:
            reason = _classify_redirect(response)
            typer.echo(
                f"   ❌ LinkedIn 세션이 redirect 되었습니다: {response.status_code} ({reason})"
            )
            if reason in SESSION_REJECTED_REASONS:
                typer.echo(
                    "   세션이 만료되었습니다. 다시 로그인하세요: uv run skim login linkedin"
                )
            return None
        if response.status_code in {401, 403}:
            typer.echo(f"   ❌ LinkedIn 인증이 거부되었습니다: {response.status_code}")
            return None
        if response.status_code >= 400:
            typer.echo(f"   ❌ LinkedIn API 오류: {response.status_code}")
            return None

        try:
            result = response.json()
        except ValueError:
            typer.echo("   ❌ LinkedIn API가 JSON이 아닌 응답을 반환했습니다.")
            return None

        if self.debug_mode:
            debug_dir = DATA_DIR / "debug" / "linkedin"
            debug_dir.mkdir(parents=True, exist_ok=True)
            with open(debug_dir / f"feed_{start}.json", "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

        return result

    def _parse_response(self, data: dict) -> List[Post]:
        """Voyager normalized 응답에서 게시글을 추출합니다."""
        included = data.get("included", [])
        if not isinstance(included, list) or not included:
            return []

        urn_index = {
            item.get("entityUrn"): item
            for item in included
            if isinstance(item, dict) and item.get("entityUrn")
        }

        ordered_items = self._ordered_update_items(data, urn_index)
        if not ordered_items:
            ordered_items = [item for item in included if self._is_update_item(item)]

        posts: List[Post] = []
        for item in ordered_items:
            post = self._extract_post(item, urn_index)
            if post:
                posts.append(post)
        return posts

    def _ordered_update_items(
        self, data: dict, urn_index: dict[str, dict]
    ) -> list[dict]:
        element_urns = self._find_first_elements(data)
        if not element_urns:
            return []

        ordered: list[dict] = []
        for raw_urn in element_urns:
            update_urn = self._coerce_update_urn(raw_urn)
            item = urn_index.get(update_urn)
            if item and self._is_update_item(item):
                ordered.append(item)
        return ordered

    def _find_first_elements(self, node: Any) -> list[Any]:
        if isinstance(node, dict):
            elements = node.get("*elements")
            if isinstance(elements, list) and elements:
                return elements
            for value in node.values():
                found = self._find_first_elements(value)
                if found:
                    return found
        if isinstance(node, list):
            for item in node:
                found = self._find_first_elements(item)
                if found:
                    return found
        return []

    @staticmethod
    def _coerce_update_urn(raw_value: Any) -> str:
        if isinstance(raw_value, str):
            return raw_value
        if isinstance(raw_value, dict):
            for key in ("*update", "entityUrn", "urn"):
                value = raw_value.get(key)
                if isinstance(value, str):
                    return value
        return ""

    @staticmethod
    def _is_update_item(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        item_type = item.get("$type", "")
        entity_urn = item.get("entityUrn", "")
        return (
            "feed.Update" in item_type
            or "fsd_update" in entity_urn
            or (
                isinstance(item.get("commentary"), dict)
                and isinstance(item.get("actor"), dict)
            )
        )

    def _extract_post(self, item: dict, urn_index: dict) -> Optional[Post]:
        """단일 아이템에서 Post 객체 생성."""
        content = self._extract_text(item.get("commentary")).strip()
        # actor(프로필 사진)를 피하기 위해 게시글 content 서브트리에서만 이미지를 찾는다
        image_urls = self._extract_image_urls(item.get("content"))

        # 이미지만 올린 게시물은 commentary가 비지만, 버리면 행 자체가 안 생겨
        # 그날 그 계정이 무엇을 올렸는지가 통째로 사라진다. x가 이미 쓰는
        # 사다리(본문 -> 미디어 링크 -> 버림)를 따른다.
        # 임계를 10자로 두면 "명문이 아닐 수 없다." 같은 짧고 유효한 글도 잘린다.
        content_status = None
        if not content:
            if not image_urls:
                return None
            content = "\n".join(image_urls)
            content_status = "media_link"

        actor = item.get("actor", {})
        if not isinstance(actor, dict):
            actor = {}
        if self._is_promoted(actor):
            return None
        author = self._extract_text(actor.get("name")) or "Unknown"

        activity_urn = self._extract_activity_urn(item)
        activity_id = self._extract_activity_id(activity_urn)

        timestamp = self._coerce_linkedin_timestamp(item.get("createdAt"))
        if not timestamp and isinstance(actor, dict):
            sub_desc = actor.get("subDescription", {})
            if isinstance(sub_desc, dict):
                timestamp = self._parse_relative_timestamp(
                    sub_desc.get("accessibilityText", "")
                ) or self._parse_relative_timestamp(sub_desc.get("text", ""))

        url = None
        if activity_urn:
            url = f"{LINKEDIN_BASE_URL}/feed/update/{activity_urn}/"

        likes, comments, shares, views = self._resolve_engagement(item, urn_index)

        return Post(
            platform="linkedin",
            author=author,
            content=content,
            timestamp=timestamp or "",
            url=url,
            likes=likes,
            comments=comments,
            reposts=shares,
            views=views,
            source="unofficial",
            external_id=activity_id,
            **({"images": image_urls} if image_urls else {}),
            **({"content_status": content_status} if content_status else {}),
        )

    @staticmethod
    def _is_promoted(actor: dict) -> bool:
        """Promoted(광고) 게시글 여부 — 상대시간 자리(subDescription)에 광고 라벨이 온다."""
        sub_desc = actor.get("subDescription")
        if not isinstance(sub_desc, dict):
            return False
        for key in ("text", "accessibilityText"):
            value = sub_desc.get(key)
            if isinstance(value, str) and (
                "promoted" in value.lower() or "광고" in value
            ):
                return True
        return False

    def _extract_image_urls(self, node: Any) -> list[str]:
        """Voyager vectorImage(rootUrl + artifacts)에서 최대 해상도 이미지 URL을 수집합니다."""
        urls: list[str] = []
        if isinstance(node, dict):
            root = node.get("rootUrl")
            artifacts = node.get("artifacts")
            if isinstance(root, str) and isinstance(artifacts, list) and artifacts:
                largest = max(
                    (a for a in artifacts if isinstance(a, dict)),
                    key=lambda a: a.get("width", 0),
                    default=None,
                )
                segment = (largest or {}).get("fileIdentifyingUrlPathSegment")
                if segment:
                    urls.append(root + segment)
            else:
                for value in node.values():
                    urls.extend(self._extract_image_urls(value))
        elif isinstance(node, list):
            for value in node:
                urls.extend(self._extract_image_urls(value))
        return list(dict.fromkeys(urls))

    def _extract_activity_urn(self, item: dict) -> str:
        candidates = [
            self._extract_path(item, "socialContent.shareUrl"),
            self._extract_path(item, "metadata.backendUrn"),
            self._extract_path(item, "updateMetadata.urn"),
            item.get("entityUrn"),
            item.get("preDashEntityUrn"),
        ]
        for candidate in candidates:
            activity_id = self._extract_activity_id(str(candidate or ""))
            if activity_id:
                return f"urn:li:activity:{activity_id}"
        return ""

    @staticmethod
    def _extract_activity_id(raw_value: str) -> Optional[str]:
        """문자열에서 LinkedIn activity id를 추출합니다."""
        match = re.search(r"urn:li:activity:(\d+)|activity-(\d+)", raw_value or "")
        if match:
            return match.group(1) or match.group(2)
        return None

    def _coerce_linkedin_timestamp(self, raw_value) -> Optional[str]:
        """LinkedIn createdAt 류 값을 ISO 8601 문자열로 변환합니다."""
        if raw_value is None:
            return None

        if isinstance(raw_value, dict):
            for key in ("time", "timestamp", "value", "epochMillis", "epoch"):
                coerced = self._coerce_linkedin_timestamp(raw_value.get(key))
                if coerced:
                    return coerced
            return None

        if isinstance(raw_value, str):
            stripped = raw_value.strip()
            if stripped.isdigit():
                raw_value = int(stripped)
            else:
                return None

        if isinstance(raw_value, (int, float)):
            seconds = raw_value / 1000 if raw_value > 10_000_000_000 else raw_value
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(
                timespec="seconds"
            )

        return None

    def _parse_relative_timestamp(
        self,
        raw_text: str,
        *,
        reference_time: Optional[datetime] = None,
    ) -> Optional[str]:
        """LinkedIn 상대시간 문자열을 ISO 8601 문자열로 변환합니다."""
        if not raw_text:
            return None

        text = raw_text.split("•", 1)[0].strip().lower()
        if not text or text in {"알 수 없음", "unknown"}:
            return None

        base_time = reference_time or datetime.now(timezone.utc)
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)
        else:
            base_time = base_time.astimezone(timezone.utc)

        if text in {"현재 시간", "just now", "now"}:
            return base_time.isoformat(timespec="seconds")

        normalized = text.replace("ago", "").strip()
        patterns = (
            (r"(\d+)\s*(s|sec|secs|second|seconds|초)$", "seconds"),
            (r"(\d+)\s*(m|min|mins|minute|minutes|분)$", "minutes"),
            (r"(\d+)\s*(h|hr|hrs|hour|hours|시간)$", "hours"),
            (r"(\d+)\s*(d|day|days|일)$", "days"),
            (r"(\d+)\s*(w|week|weeks|주)$", "weeks"),
            (r"(\d+)\s*(mo|month|months|개월)$", "days"),
            (r"(\d+)\s*(y|year|years|년)$", "days"),
        )

        for pattern, unit in patterns:
            match = re.match(pattern, normalized)
            if not match:
                continue

            amount = int(match.group(1))
            if unit == "days" and match.group(2) in {"mo", "month", "months", "개월"}:
                amount *= 30
            elif unit == "days" and match.group(2) in {"y", "year", "years", "년"}:
                amount *= 365

            delta = timedelta(**{unit: amount})
            return (base_time - delta).isoformat(timespec="seconds")

        return None

    def _resolve_engagement(
        self, item: dict, urn_index: dict
    ) -> tuple[int, int, int, Optional[int]]:
        """URN 참조를 따라가서 engagement 데이터 추출."""
        social_urn = item.get("*socialDetail", "")
        social_obj = urn_index.get(social_urn, {})

        counts_urn = social_obj.get("*totalSocialActivityCounts", "")
        counts_obj = urn_index.get(counts_urn, {})

        likes = self._coerce_count(counts_obj.get("numLikes"))
        comments = self._coerce_count(counts_obj.get("numComments"))
        shares = self._coerce_count(counts_obj.get("numShares"))
        views = counts_obj.get("numImpressions")

        return likes, comments, shares, self._coerce_optional_count(views)

    @staticmethod
    def _coerce_count(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _coerce_optional_count(cls, value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        return cls._coerce_count(value)

    def _load_session_cookies(self) -> None:
        """Playwright storage_state의 LinkedIn 쿠키를 requests 세션으로 옮깁니다."""
        if not self.session_path.exists():
            return

        storage_state = json.loads(self.session_path.read_text(encoding="utf-8"))
        cookie_names = set()
        csrf_token = ""
        for cookie in storage_state.get("cookies", []):
            domain = cookie.get("domain", "")
            if "linkedin.com" not in domain:
                continue

            name = cookie.get("name", "")
            value = cookie.get("value", "")
            if name == "JSESSIONID" and value:
                csrf_token = value.strip('"')
            self.session.cookies.set(
                name,
                value,
                domain=domain,
                path=cookie.get("path", "/"),
            )
            if name:
                cookie_names.add(name)

        self._has_login_session = REQUIRED_COOKIE_NAMES <= cookie_names
        if csrf_token:
            self.session.headers["Csrf-Token"] = csrf_token

    def _extract_text(self, raw_value: Any) -> str:
        if raw_value is None:
            return ""
        if isinstance(raw_value, str):
            return raw_value.strip()
        if isinstance(raw_value, dict):
            for key in ("text", "accessibilityText", "title", "value", "string"):
                text = self._extract_text(raw_value.get(key))
                if text:
                    return text
            for value in raw_value.values():
                text = self._extract_text(value)
                if text:
                    return text
        if isinstance(raw_value, list):
            return " ".join(
                part
                for part in (self._extract_text(item) for item in raw_value)
                if part
            ).strip()
        # dict/list에서 텍스트를 못 찾았으면 없는 것이다. str()로 떨어뜨리면
        # `{'text': ''}` 같은 repr이 본문이 된다. 문자 길이 임계(<10)가 이걸
        # 우연히 막고 있었어서, 임계를 낮추는 순간 표면화된다.
        if isinstance(raw_value, (dict, list)):
            return ""
        return str(raw_value).strip()

    @staticmethod
    def _extract_path(raw_value: Any, path: str) -> Any:
        current = raw_value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current
