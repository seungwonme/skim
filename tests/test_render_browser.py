"""렌더 폴백이 공유 캐시의 playwright 빌드 대신 설치된 Chrome을 쓰는지 확인한다.

playwright 빌드는 다른 프로젝트와 함께 쓰는 캐시에 있어 2026-10-15, 2026-10-20에
지워졌고, 그동안 렌더가 필요한 글의 본문이 비었다.
"""

from unittest.mock import MagicMock, patch

import pytest

from skim_core import enrichment
from skim_core.crawlers.auth import cdp


def test_render_browser_runs_the_installed_chrome():
    playwright = MagicMock()
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    with (
        patch.object(cdp, "get_chrome_path", return_value=chrome),
        patch.object(enrichment.shutil, "which", return_value=None),
    ):
        enrichment.launch_render_browser(playwright)

    playwright.chromium.launch.assert_called_once_with(
        executable_path=chrome, headless=True
    )


def test_linux_executable_name_is_resolved_to_a_path():
    playwright = MagicMock()

    with (
        patch.object(cdp, "get_chrome_path", return_value="google-chrome"),
        patch.object(enrichment.shutil, "which", return_value="/usr/bin/google-chrome"),
    ):
        enrichment.launch_render_browser(playwright)

    playwright.chromium.launch.assert_called_once_with(
        executable_path="/usr/bin/google-chrome", headless=True
    )


def test_missing_chrome_is_reported_without_falling_back():
    # 공유 캐시의 빌드로 돌아가지 않는다. 그쪽이 지워지는 게 이 변경의 이유다.
    playwright = MagicMock()

    with (
        patch.object(cdp, "get_chrome_path", return_value=None),
        pytest.raises(RuntimeError, match="Google Chrome"),
    ):
        enrichment.launch_render_browser(playwright)

    playwright.chromium.launch.assert_not_called()


def test_render_failure_returns_none_and_crawl_continues():
    with (
        patch.object(cdp, "get_chrome_path", return_value=None),
        patch("builtins.print"),
    ):
        assert enrichment._fetch_rendered_html_sync("https://example.com") is None
