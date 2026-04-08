"""
@file base.py
@description 크롤러 공통 인터페이스 (Protocol)
"""

from typing import Any, List, Protocol, runtime_checkable

from ..models import Post


@runtime_checkable
class Crawler(Protocol):
    """모든 크롤러가 구현하는 인터페이스."""

    platform: str

    async def crawl(self, **options: Any) -> List[Post]:
        """
        크롤링을 실행하고 Post 리스트를 반환합니다.

        Common options:
            count (int): 수집할 게시글 수
            since (datetime): 이 시점 이후의 게시글만 수집
            debug (bool): 디버그 모드
            no_content (bool): 콘텐츠 enrichment 스킵

        Platform-specific options are passed as additional kwargs.
        """
