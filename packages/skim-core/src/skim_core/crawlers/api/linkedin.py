"""
@file linkedin_api.py
@description LinkedIn GraphQL API 기반 크롤러 (headless 모드)

Playwright 세션 쿠키를 사용하여 LinkedIn Voyager GraphQL API를
page.evaluate(fetch())로 호출합니다. DOM 파싱 없이 피드 데이터를 수집합니다.

주요 기능:
1. headless 브라우저 + 세션 쿠키로 인증
2. GraphQL API (voyagerFeedDashMainFeed)로 피드 수집
3. URN 참조 resolve하여 engagement 데이터 추출
4. 페이지네이션 지원

@dependencies
- playwright.async_api: 브라우저 자동화 (세션 + fetch)
- typer: CLI 출력
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import typer
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from ...models import Post
from ...paths import DATA_DIR, SESSIONS_DIR

GRAPHQL_QUERY_ID = "voyagerFeedDashMainFeed.923020905727c01516495a0ac90bb475"
GRAPHQL_BASE = "/voyager/api/graphql"


class LinkedInAPICrawler:
    """
    LinkedIn GraphQL API 기반 크롤러

    Playwright의 page.evaluate(fetch())를 사용하여
    LinkedIn Voyager GraphQL API를 직접 호출합니다.
    """

    platform = "linkedin"

    def __init__(self, debug_mode: bool = False):
        self.platform_name = "LinkedIn"
        self.debug_mode = debug_mode
        self.session_path = SESSIONS_DIR / "linkedin_session.json"

    async def crawl(self, **options) -> List[Post]:
        count = options.get("count", 5)
        return await self._crawl_impl(count)

    async def _crawl_impl(self, count: int) -> List[Post]:
        """LinkedIn 피드를 GraphQL API로 수집"""
        typer.echo(f"[API 모드] LinkedIn 크롤링 시작... (게시글 {count}개)")

        if not self.session_path.exists():
            typer.echo("❌ 세션 파일이 없습니다. 먼저 로그인하세요:")
            typer.echo("  uv run skim login linkedin")
            return []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=not self.debug_mode,
            )
            context = await browser.new_context(
                storage_state=str(self.session_path),
            )
            page = await context.new_page()

            try:
                # 피드 페이지 로드 (세션 활성화)
                if not await self._navigate_to_feed(page):
                    await browser.close()
                    return []

                # GraphQL API로 피드 수집
                posts = await self._collect_feed(page, count)

                # 세션 갱신 저장
                await self._save_session(context)

                return posts

            except Exception as e:
                typer.echo(f"❌ 크롤링 중 오류: {e}")
                if self.debug_mode:
                    import traceback  # pylint: disable=import-outside-toplevel

                    traceback.print_exc()
                return []
            finally:
                await browser.close()

    async def _navigate_to_feed(self, page) -> bool:
        """피드 페이지로 이동 (세션 검증 + Remember Me 처리)"""
        try:
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
        except PlaywrightTimeoutError:
            pass

        await page.wait_for_timeout(3000)

        # Remember Me 페이지 처리
        current_url = page.url
        if "/uas/login" in current_url or "/checkpoint/rm" in current_url:
            typer.echo("🔄 Remember Me 페이지 감지...")
            profile_btn = page.locator("button.member-profile__details").first
            try:
                await profile_btn.wait_for(state="visible", timeout=5000)
                await profile_btn.click()
                typer.echo("   ✅ 프로필 클릭")
                try:
                    await page.wait_for_url("**/feed/**", timeout=15000)
                except PlaywrightTimeoutError:
                    pass
                await page.wait_for_timeout(3000)
            except PlaywrightTimeoutError:
                typer.echo("❌ Remember Me 프로필을 찾을 수 없습니다.")
                typer.echo("   먼저 로그인하세요: uv run skim login linkedin")
                return False

        # 로그인 확인
        if "/feed" not in page.url or "/login" in page.url:
            typer.echo("❌ 로그인 실패. 먼저 로그인하세요:")
            typer.echo("  uv run skim login linkedin")
            return False

        typer.echo("✅ LinkedIn 피드 접근 성공")
        return True

    async def _collect_feed(self, page, target_count: int) -> List[Post]:
        """GraphQL API로 피드 게시글 수집 (페이지네이션)"""
        all_posts: List[Post] = []
        seen: set[str] = set()  # 중복 제거용 (author+content 해시)
        start = 0
        batch_size = min(target_count, 20)
        max_iterations = 5

        for _ in range(max_iterations):
            if len(all_posts) >= target_count:
                break

            remaining = target_count - len(all_posts)
            count = min(batch_size, remaining + 5)  # 중복 대비 여유분

            if self.debug_mode:
                typer.echo(f"   📡 API 호출: start={start}, count={count}")

            raw = await self._fetch_graphql(page, start, count)
            if not raw:
                break

            posts = self._parse_response(raw)
            if not posts:
                if self.debug_mode:
                    typer.echo("   ⚠️ 게시글 없음 - 종료")
                break

            new_count = 0
            for post in posts:
                key = f"{post.author}:{post.content[:100]}"
                if key not in seen:
                    seen.add(key)
                    all_posts.append(post)
                    new_count += 1

            start += count

            if self.debug_mode:
                typer.echo(f"   ✅ {new_count}개 추출 (총 {len(all_posts)}개)")

        typer.echo(f"📊 총 {len(all_posts)}개의 게시글을 추출했습니다.")
        return all_posts[:target_count]

    async def _fetch_graphql(self, page, start: int, count: int) -> Optional[dict]:
        """page.evaluate(fetch())로 GraphQL API 호출"""
        url = (
            f"{GRAPHQL_BASE}?includeWebMetadata=true"
            f"&variables=(start:{start},count:{count},sortOrder:RELEVANCE)"
            f"&queryId={GRAPHQL_QUERY_ID}"
        )

        try:
            result = await page.evaluate(
                """async (url) => {
                // CSRF 토큰을 쿠키에서 추출
                const csrfMatch = document.cookie.match(/JSESSIONID="?([^;"]+)/);
                const csrf = csrfMatch ? csrfMatch[1] : '';

                const resp = await fetch(url, {
                    credentials: 'include',
                    headers: {
                        'Accept': 'application/vnd.linkedin.normalized+json+2.1',
                        'csrf-token': csrf,
                        'x-restli-protocol-version': '2.0.0',
                    }
                });
                if (!resp.ok) return { error: resp.status };
                return await resp.json();
            }""",
                url,
            )

            if "error" in result:
                typer.echo(f"   ❌ API 오류: {result['error']}")
                return None

            if self.debug_mode:
                debug_dir = DATA_DIR / "debug" / "linkedin"
                debug_dir.mkdir(parents=True, exist_ok=True)
                with open(debug_dir / f"api_response_{start}.json", "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)

            return result

        except Exception as e:
            typer.echo(f"   ❌ fetch 오류: {e}")
            return None

    def _parse_response(self, data: dict) -> List[Post]:
        """GraphQL 응답에서 게시글 추출 (URN 참조 resolve)"""
        included = data.get("included", [])
        if not included:
            return []

        # URN 인덱스 구축
        urn_index = {}
        for item in included:
            urn = item.get("entityUrn")
            if urn:
                urn_index[urn] = item

        posts = []
        for item in included:
            post = self._extract_post(item, urn_index)
            if post:
                posts.append(post)

        return posts

    def _extract_post(self, item: dict, urn_index: dict) -> Optional[Post]:
        """단일 아이템에서 Post 객체 생성"""
        # commentary에서 텍스트 추출
        commentary = item.get("commentary")
        if not isinstance(commentary, dict):
            return None

        text_obj = commentary.get("text")
        if not isinstance(text_obj, dict):
            return None

        content = text_obj.get("text", "")
        if len(content) < 10:
            return None

        # 작성자
        actor = item.get("actor", {})
        author = "Unknown"
        if isinstance(actor, dict):
            name_obj = actor.get("name", {})
            if isinstance(name_obj, dict):
                author = name_obj.get("text", "Unknown")

        entity_urn = item.get("entityUrn", "")
        activity_id = self._extract_activity_id(entity_urn)

        # 타임스탬프
        timestamp = self._coerce_linkedin_timestamp(item.get("createdAt"))
        if not timestamp and isinstance(actor, dict):
            sub_desc = actor.get("subDescription", {})
            if isinstance(sub_desc, dict):
                timestamp = self._parse_relative_timestamp(
                    sub_desc.get("accessibilityText", "")
                ) or self._parse_relative_timestamp(sub_desc.get("text", ""))

        # URL (entityUrn에서 activity ID 추출)
        url = None
        if activity_id:
            url = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"

        # Engagement (URN 참조 resolve)
        likes, comments, shares = self._resolve_engagement(item, urn_index)

        return Post(
            platform="linkedin",
            author=author,
            content=content,
            timestamp=timestamp or "",
            url=url,
            likes=likes,
            comments=comments,
            reposts=shares,
            external_id=activity_id,
        )

    @staticmethod
    def _extract_activity_id(entity_urn: str) -> Optional[str]:
        """entityUrn에서 activity id를 추출합니다."""
        match = re.search(r"urn:li:activity:(\d+)", entity_urn or "")
        if match:
            return match.group(1)
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
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds")

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

        base_time = reference_time or datetime.now().astimezone()
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)

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

    def _resolve_engagement(self, item: dict, urn_index: dict) -> tuple[int, int, int]:
        """URN 참조를 따라가서 engagement 데이터 추출"""
        # *socialDetail → *totalSocialActivityCounts
        social_urn = item.get("*socialDetail", "")
        social_obj = urn_index.get(social_urn, {})

        counts_urn = social_obj.get("*totalSocialActivityCounts", "")
        counts_obj = urn_index.get(counts_urn, {})

        likes = counts_obj.get("numLikes", 0)
        comments = counts_obj.get("numComments", 0)
        shares = counts_obj.get("numShares", 0)

        return likes, comments, shares

    async def _save_session(self, context) -> None:
        """세션 상태 저장"""
        try:
            state = await context.storage_state()
            with open(self.session_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass
