"""
Reddit 플랫폼 전용 크롤러

이 모듈은 Reddit 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. Reddit 피드에서 게시글 수집
2. Reddit 계정을 통한 로그인 지원
3. 작성자, 콘텐츠, 업보트/댓글 정보 추출
4. 세션 관리 (재로그인 방지)
5. 점진적 추출 시스템 (스크롤링, 서브레딧 탐색)
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import load_dotenv
from playwright.async_api import Page

from ...models import Post
from .base import BrowserCrawler

load_dotenv()


class RedditCrawler(BrowserCrawler):
    """Reddit 전용 크롤러"""

    platform = "reddit"

    # CSS 선택자
    POST_SELECTORS = [
        "shreddit-post",
        "article",
        'div[data-testid="post-container"]',
        'div[id^="t3_"]',
        'div[class*="Post"]',
        '[slot="post-container"]',
    ]

    LOGIN_BUTTON_SELECTORS = [
        'button:has-text("Log in")',
        'button:has-text("LOG IN")',
        'button:has-text("Sign in")',
        'button[type="submit"]',
        'fieldset button[class*="button"]',
        'fieldset button[class*="AnimatedForm"]',
        'button[class*="AnimatedForm__submitButton"]',
    ]

    LOGIN_INDICATORS = [
        'button[aria-label*="Expand user menu"]',
        'button[id*="USER_DROPDOWN"]',
        'div[class*="header-user-dropdown"]',
        'button[aria-label*="profile"]',
        'a[href="/submit"]',
        'button:has-text("Create Post")',
    ]

    ERROR_SELECTORS = [
        'div[class*="error"]',
        'span[class*="error"]',
        'div[class*="AnimatedForm__errorMessage"]',
        ".status-error",
        '[class*="status"][class*="error"]',
    ]

    # 타이밍 상수 (ms)
    PAGE_LOAD_WAIT_MS = 3000
    ELEMENT_WAIT_TIMEOUT_MS = 5000
    POLL_INTERVAL_MS = 500
    LOGIN_VERIFY_MAX_RETRIES = 30
    BUTTON_ENABLE_MAX_RETRIES = 10
    BUTTON_ENABLE_TAB_RETRY_AT = 4

    # 로그인 폼 선택자
    USERNAME_INPUT_SELECTOR = (
        'input#login-username, input[name="username"], input[id="loginUsername"]'
    )
    PASSWORD_INPUT_SELECTOR = (
        'input#login-password, input[name="password"], input[id="loginPassword"]'
    )

    def __init__(self, debug_mode: bool = False):
        super().__init__(
            platform_name="reddit",
            base_url="https://www.reddit.com",
            debug_mode=debug_mode,
        )
        self.username = os.getenv("REDDIT_USERNAME")
        self.password = os.getenv("REDDIT_PASSWORD")
        self.session_path = Path("data/sessions/reddit_session.json")
        self.max_scroll_attempts = 10

        if not self.username or not self.password:
            raise ValueError("REDDIT_USERNAME과 REDDIT_PASSWORD 환경 변수가 필요합니다")

    # ========== 크롤링 구현 ==========

    async def _crawl_implementation(self, page: Page, count: int) -> list[Post]:
        """Reddit 게시글 크롤링 구현"""
        try:
            if not await self._ensure_logged_in(page):
                typer.echo("❌ Reddit 로그인 실패로 크롤링을 중단합니다.")
                return []

            await self._navigate_to_feed(page)
            posts = await self._collect_posts_progressively(page, count)

            if self.debug_mode:
                await self._save_debug_html(page, "reddit_posts.html")

            return posts

        except Exception as e:
            typer.echo(f"❌ 크롤링 중 오류 발생: {e}")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_error.html")
            return []

    # ========== 로그인 관련 메서드 ==========

    async def _ensure_logged_in(self, page: Page) -> bool:
        """로그인 상태 확인 및 필요시 로그인"""
        if await self._try_load_session(page):
            return True

        if not await self._perform_login(page):
            return False

        await self._save_session(page)
        return True

    async def _perform_login(self, page: Page) -> bool:
        """Reddit 로그인 수행"""
        try:
            typer.echo("🔑 Reddit 로그인 중...")
            await page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded")
            await page.wait_for_timeout(self.PAGE_LOAD_WAIT_MS)

            # CAPTCHA 감지 시 수동 로그인 대기
            page_text = await page.title()
            if "humanity" in page_text.lower() or "captcha" in page_text.lower():
                typer.echo("🤖 CAPTCHA 감지! 브라우저에서 직접 로그인해주세요.")
                typer.echo("   로그인 완료 후 피드가 보이면 자동으로 진행됩니다. (최대 120초 대기)")
                for _ in range(240):
                    await page.wait_for_timeout(500)
                    current_url = page.url
                    if "reddit.com/login" not in current_url and "reddit.com" in current_url:
                        if await self._is_element_visible(page, self.LOGIN_INDICATORS):
                            typer.echo("✅ 수동 로그인 성공!")
                            return True
                typer.echo("❌ 수동 로그인 타임아웃 (120초)")
                return False

            if not await self._fill_login_credentials(page):
                return False

            if not await self._click_login_button(page):
                return False

            return await self._verify_login_success(page)

        except Exception as e:
            typer.echo(f"❌ 로그인 중 오류: {e}")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_login_exception.html")
            return False

    async def _fill_login_credentials(self, page: Page) -> bool:
        """로그인 자격증명 입력"""
        try:
            username_input = page.locator(self.USERNAME_INPUT_SELECTOR).first
            await username_input.wait_for(state="visible", timeout=self.ELEMENT_WAIT_TIMEOUT_MS)
            await username_input.fill(self.username)
            typer.echo(f"   ✅ 사용자명 입력 완료: {self.username}")

            password_input = page.locator(self.PASSWORD_INPUT_SELECTOR).first
            await password_input.wait_for(state="visible", timeout=self.ELEMENT_WAIT_TIMEOUT_MS)
            await password_input.fill(self.password)
            typer.echo("   ✅ 비밀번호 입력 완료")

            await username_input.press("Tab")
            await password_input.press("Tab")
            await page.wait_for_timeout(self.POLL_INTERVAL_MS)

            return True

        except Exception as e:
            typer.echo(f"   ❌ 로그인 폼 입력 실패: {e}")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_login_form_failed.html")
            return False

    async def _click_login_button(self, page: Page) -> bool:
        """로그인 버튼 찾기 및 클릭"""
        login_button = await self._find_element_by_selectors(
            page, self.LOGIN_BUTTON_SELECTORS, "로그인 버튼"
        )

        if not login_button:
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_no_login_button.html")
            return False

        return await self._wait_and_click_button(page, login_button)

    async def _wait_and_click_button(self, page: Page, button) -> bool:
        """버튼 활성화 대기 및 클릭"""
        for i in range(self.BUTTON_ENABLE_MAX_RETRIES):
            if await button.is_enabled():
                break
            await page.wait_for_timeout(self.POLL_INTERVAL_MS)
            if i == self.BUTTON_ENABLE_TAB_RETRY_AT:
                password_input = page.locator(self.PASSWORD_INPUT_SELECTOR).first
                await password_input.press("Tab")

        if not await button.is_enabled():
            typer.echo("   ❌ 로그인 버튼이 활성화되지 않음")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_login_button_disabled.html")
            return False

        await button.click()
        typer.echo("   🔄 로그인 버튼 클릭됨")
        return True

    async def _verify_login_success(self, page: Page) -> bool:
        """로그인 성공 여부 확인"""
        typer.echo("   - 로그인 성공 확인 중...")

        for _ in range(self.LOGIN_VERIFY_MAX_RETRIES):
            if page.url in ["https://www.reddit.com/", "https://www.reddit.com"]:
                typer.echo("✅ Reddit 로그인 성공!")
                return True

            if await self._is_element_visible(page, self.LOGIN_INDICATORS):
                typer.echo("✅ Reddit 로그인 성공! (사용자 메뉴 확인)")
                return True

            error_msg = await self._get_error_message(page)
            if error_msg:
                typer.echo(f"❌ Reddit 로그인 실패: {error_msg}")
                return False

            await page.wait_for_timeout(self.POLL_INTERVAL_MS)

        typer.echo("❌ Reddit 로그인 실패: 타임아웃")
        if self.debug_mode:
            await self._save_debug_html(page, "reddit_login_timeout.html")
        return False

    # ========== 게시글 수집 메서드 ==========

    async def _navigate_to_feed(self, page: Page):
        """메인 피드로 이동"""
        await page.goto("https://www.reddit.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(self.PAGE_LOAD_WAIT_MS)

    async def _collect_posts_progressively(self, page: Page, target_count: int) -> list[Post]:
        """점진적으로 게시글 수집"""
        posts: list[Post] = []
        seen_urls: set[str] = set()
        seen_content_authors: set[tuple[str, str]] = set()
        scroll_attempts = 0

        typer.echo(f"🔄 게시글 수집 시작 (목표: {target_count}개)")

        while len(posts) < target_count and scroll_attempts < self.max_scroll_attempts:
            new_posts = await self._extract_posts_from_page(page, seen_urls, seen_content_authors)
            posts.extend(new_posts)

            typer.echo(f"   📊 수집 현황: {len(posts)}/{target_count} (+{len(new_posts)}개 신규)")

            if len(posts) >= target_count:
                break

            if not new_posts:
                typer.echo("   ⚠️ 새로운 게시글이 없음")

            await self._scroll_page(page)
            scroll_attempts += 1

        typer.echo(f"✅ 총 {len(posts)}개 게시글 수집 완료")
        return posts[:target_count]

    async def _extract_posts_from_page(
        self,
        page: Page,
        seen_urls: set[str],
        seen_content_authors: set[tuple[str, str]],
    ) -> list[Post]:
        """현재 페이지에서 새로운 게시글 추출"""
        new_posts = []
        post_elements = await self._find_post_elements(page)

        for element in post_elements:
            try:
                post_data = await self._extract_post_data_smart(element)

                if not post_data:
                    continue

                if self._is_duplicate(post_data, seen_urls, seen_content_authors):
                    continue

                post = Post(
                    platform="reddit",
                    author=post_data.get("author", "Unknown"),
                    content=post_data.get("content", ""),
                    timestamp=post_data.get("timestamp", ""),
                    url=post_data.get("url"),
                    likes=post_data.get("likes", 0),
                    comments=post_data.get("comments", 0),
                )
                new_posts.append(post)

                # 중복 추적용 set 업데이트
                if post_data.get("url"):
                    seen_urls.add(post_data["url"])
                seen_content_authors.add(
                    (post_data.get("content", ""), post_data.get("author", ""))
                )

            except Exception as e:
                if self.debug_mode:
                    typer.echo(f"   ❌ 게시글 추출 실패: {e}")

        return new_posts

    async def _find_post_elements(self, page: Page):
        """게시글 요소 찾기"""
        for selector in self.POST_SELECTORS:
            try:
                elements = await page.locator(selector).all()
                if elements:
                    typer.echo(f"   🔎 {len(elements)}개 게시글 발견 (선택자: {selector})")
                    return elements
            except Exception:
                continue

        typer.echo("   ❌ 게시글을 찾을 수 없음")
        return []

    async def _extract_post_data_smart(self, element) -> Optional[dict[str, Any]]:
        """요소 타입에 따라 적절한 추출 메서드 선택"""
        element_type = await self._get_element_type(element)

        if element_type == "shreddit-post":
            return await self._extract_from_shreddit(element)
        return await self._extract_from_article(element)

    async def _get_element_type(self, element) -> str:
        """요소 타입 확인"""
        try:
            return await element.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            return "unknown"

    # ========== 데이터 추출 메서드 ==========

    async def _extract_from_shreddit(self, element) -> Optional[dict[str, Any]]:
        """shreddit-post 요소에서 데이터 추출"""
        try:
            attrs = await self._get_shreddit_attributes(element)

            title = await self._extract_title(element, attrs.get("post_title"))
            subreddit = self._format_subreddit(attrs.get("subreddit_name"))

            if not subreddit:
                subreddit = await self._extract_subreddit(element)

            timestamp = await self._extract_timestamp(element) or attrs.get("created_timestamp", "")
            upvotes = await self._extract_upvotes(element, attrs.get("score"))

            url = self._build_reddit_url(attrs.get("permalink"))
            comments = self._parse_safe_int(attrs.get("comment_count", 0))

            post_data = {
                "author": subreddit or "Unknown",
                "content": title or await self._extract_fallback_title(element) or "No title",
                "timestamp": timestamp,
                "url": url,
                "likes": upvotes,
                "comments": comments,
            }

            await self._dump_dom(element, subreddit or "unknown")

            return post_data

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ❌ shreddit 추출 실패: {e}")
            return None

    async def _extract_from_article(self, element) -> Optional[dict[str, Any]]:
        """article/generic 요소에서 데이터 추출"""
        try:
            title = await self._extract_title(element)
            subreddit = await self._extract_subreddit(element)
            url = await self._extract_url(element)
            timestamp = await self._extract_timestamp(element)
            likes, comments = await self._extract_interactions(element)

            if not title and not url:
                return None

            post_data = {
                "author": subreddit or "Unknown",
                "content": title or "No title",
                "timestamp": timestamp,
                "url": url,
                "likes": likes,
                "comments": comments,
            }

            await self._dump_dom(element, subreddit or "unknown")

            return post_data

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ❌ article 추출 실패: {e}")
            return None

    # ========== 개별 데이터 추출 헬퍼 ==========

    async def _extract_title(self, element, attr_title: Optional[str] = None) -> Optional[str]:
        """제목 추출"""
        if attr_title:
            return attr_title

        title_selectors = [
            "h3",
            "h2",
            "h1",
            'a[href*="/comments/"]',
            'heading[level="2"]',
        ]

        for selector in title_selectors:
            try:
                elements = await element.locator(selector).all()
                for el in elements:
                    text = await el.inner_text()
                    if text and len(text) > 5:
                        return text.strip()
            except Exception:
                continue

        return None

    async def _extract_subreddit(self, element) -> Optional[str]:
        """서브레딧 추출"""
        try:
            links = await element.locator('a[href^="/r/"]:not([href*="/comments/"])').all()
            for link in links:
                href = await link.get_attribute("href")
                if href:
                    match = re.search(r"/r/([^/]+)", href)
                    if match:
                        return f"r/{match.group(1)}"
        except Exception:
            pass
        return None

    async def _extract_url(self, element) -> Optional[str]:
        """URL 추출"""
        try:
            links = await element.locator('a[href*="/comments/"]').all()
            if links:
                href = await links[0].get_attribute("href")
                if href:
                    return self._build_reddit_url(href)
        except Exception:
            pass
        return None

    async def _extract_timestamp(self, element) -> Optional[str]:
        """시간 추출"""
        try:
            time_elements = await element.locator("time").all()
            if time_elements:
                return await time_elements[0].inner_text()
        except Exception:
            pass
        return None

    async def _extract_upvotes(self, element, attr_score: Optional[str] = None) -> int:
        """업보트 수 추출"""
        if attr_score:
            upvotes = self._parse_safe_int(attr_score)
            if upvotes > 0:
                return upvotes

        try:
            numbers = await element.locator("faceplate-number").all()
            for num in numbers:
                number_attr = await num.get_attribute("number")
                if number_attr:
                    upvotes = self._parse_safe_int(number_attr)
                    if upvotes > 0:
                        return upvotes
        except Exception:
            pass

        return await self._extract_upvotes_from_text(element)

    async def _extract_upvotes_from_text(self, element) -> int:
        """텍스트에서 업보트 수 추출"""
        try:
            text = await element.inner_text()
            patterns = [
                r"(\d+\.?\d*[KkMm]?)\s*upvote",
                r"Vote.*?(\d+\.?\d*[KkMm]?)",
                r"Upvote\s+(\d+\.?\d*[KkMm]?)\s+Downvote",
            ]

            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return self._extract_numbers_from_text(match.group(1))
        except Exception:
            pass
        return 0

    async def _extract_interactions(self, element) -> tuple[int, int]:
        """상호작용 데이터 추출 (좋아요, 댓글)"""
        try:
            text = await element.inner_text()

            likes = 0
            upvote_match = re.search(r"(\d+\.?\d*[KkMm]?)\s*upvote", text, re.IGNORECASE)
            if upvote_match:
                likes = self._extract_numbers_from_text(upvote_match.group(1))

            comments = 0
            comment_match = re.search(r"(\d+\.?\d*[KkMm]?)\s*comment", text, re.IGNORECASE)
            if comment_match:
                comments = self._extract_numbers_from_text(comment_match.group(1))

            return likes, comments

        except Exception:
            return 0, 0

    async def _extract_fallback_title(self, element) -> Optional[str]:
        """대체 제목 추출"""
        try:
            text = await element.inner_text()
            lines = [line.strip() for line in text.split("\n") if line.strip()]

            for line in lines:
                if (
                    not line.startswith("r/")
                    and not re.match(
                        r"^\d+\.?\d*[KkMm]?\s*(upvote|comment)",
                        line,
                        re.IGNORECASE,
                    )
                    and len(line) > 10
                ):
                    return line
        except Exception:
            pass
        return None

    # ========== 유틸리티 메서드 ==========

    async def _get_shreddit_attributes(self, element) -> dict[str, Any]:
        """shreddit-post 속성 가져오기"""
        attrs = {}
        attr_names = [
            "permalink",
            "comment-count",
            "created-timestamp",
            "post-title",
            "subreddit-name",
            "score",
        ]

        for attr in attr_names:
            key = attr.replace("-", "_")
            attrs[key] = await element.get_attribute(attr)

        return attrs

    async def _find_element_by_selectors(self, page: Page, selectors: list[str], element_name: str):
        """여러 선택자로 요소 찾기"""
        typer.echo(f"   - {element_name} 찾기...")

        for selector in selectors:
            try:
                element = page.locator(selector).first
                if await element.is_visible():
                    typer.echo(f"   ✅ {element_name} 찾음: {selector}")
                    return element
            except Exception:
                continue

        typer.echo(f"   ❌ {element_name}을 찾을 수 없음")
        return None

    async def _is_element_visible(self, page: Page, selectors: list[str]) -> bool:
        """요소 가시성 확인"""
        for selector in selectors:
            try:
                element = page.locator(selector).first
                if await element.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _get_error_message(self, page: Page) -> Optional[str]:
        """오류 메시지 가져오기"""
        for selector in self.ERROR_SELECTORS:
            try:
                element = page.locator(selector).first
                if await element.is_visible():
                    error_text = await element.inner_text()
                    if self.debug_mode:
                        await self._save_debug_html(page, "reddit_error.html")
                    return error_text
            except Exception:
                continue
        return None

    def _is_duplicate(
        self,
        post_data: dict[str, Any],
        seen_urls: set[str],
        seen_content_authors: set[tuple[str, str]],
    ) -> bool:
        """중복 게시글 확인 (set 기반 O(1) 검색)"""
        url = post_data.get("url")
        if url and url in seen_urls:
            return True

        content_author = (post_data.get("content", ""), post_data.get("author", ""))
        return content_author in seen_content_authors

    def _format_subreddit(self, subreddit: Optional[str]) -> Optional[str]:
        """서브레딧 이름 포맷"""
        if not subreddit:
            return None
        if not subreddit.startswith("r/"):
            return f"r/{subreddit}"
        return subreddit

    def _build_reddit_url(self, path: Optional[str]) -> Optional[str]:
        """Reddit URL 생성"""
        if not path:
            return None
        if path.startswith("http"):
            return path
        return f"https://www.reddit.com{path}"

    @staticmethod
    def _parse_safe_int(value: Any) -> int:
        """안전한 정수 변환"""
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    async def _scroll_page(self, page: Page):
        """페이지 스크롤"""
        try:
            typer.echo("   📜 페이지 스크롤...")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(self.PAGE_LOAD_WAIT_MS)
        except Exception as e:
            typer.echo(f"   ⚠️ 스크롤 중 오류: {e}")

    # ========== 세션 관리 ==========

    async def _try_load_session(self, page: Page) -> bool:
        """세션 로드 시도"""
        if not self.session_path.exists():
            return False

        typer.echo("💾 저장된 세션 로드 중...")

        try:
            with open(self.session_path, "r", encoding="utf-8") as f:
                session_data = json.load(f)

            await self._apply_session_data(page, session_data)

            if await self._validate_session(page):
                typer.echo("   ✅ 세션 유효함, 로그인 건너뜀")
                return True
            else:
                typer.echo("   ⚠️ 세션 만료됨, 재로그인 필요")
                return False

        except Exception as e:
            typer.echo(f"   ❌ 세션 로드 실패: {e}")
            self._cleanup_invalid_session()
            return False

    async def _apply_session_data(self, page: Page, session_data: dict[str, Any]):
        """세션 데이터 적용"""
        await page.context.add_cookies(session_data.get("cookies", []))

        if "origins" in session_data:
            for origin in session_data["origins"]:
                if origin.get("origin") == "https://www.reddit.com":
                    await self._apply_local_storage(page, origin.get("localStorage", []))

    async def _apply_local_storage(self, page: Page, local_storage_items: list[dict[str, Any]]):
        """localStorage 데이터 적용"""
        await page.goto("https://www.reddit.com", wait_until="domcontentloaded")

        for item in local_storage_items:
            await page.evaluate(
                f'window.localStorage.setItem("{item["name"]}", {json.dumps(item["value"])})'
            )

    async def _validate_session(self, page: Page) -> bool:
        """세션 유효성 확인"""
        await page.goto(self.base_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        return await self._is_element_visible(page, self.LOGIN_INDICATORS)

    async def _save_session(self, page: Page):
        """세션 저장"""
        try:
            typer.echo("💾 현재 세션 저장 중...")
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            await page.context.storage_state(path=str(self.session_path))
            typer.echo("   ✅ 세션 저장 완료")
        except Exception as e:
            typer.echo(f"   ❌ 세션 저장 실패: {e}")

    def _cleanup_invalid_session(self):
        """무효한 세션 파일 정리"""
        try:
            self.session_path.unlink()
            typer.echo("   🗑️ 손상된 세션 파일 삭제됨")
        except Exception:
            pass

    # ========== 디버그 ==========

    # _save_debug_html은 BrowserCrawler에서 상속
