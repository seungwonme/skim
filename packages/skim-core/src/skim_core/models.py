"""
@file models.py
@description SNS 게시글 데이터 모델 정의

이 모듈은 SNS 플랫폼에서 크롤링한 게시글 데이터를 표현하는 Pydantic 모델을 제공합니다.

주요 기능:
1. Post 데이터 모델 - 모든 SNS 플랫폼의 공통 게시글 구조
2. 플랫폼별 추가 필드 지원 (extra = "allow")
3. 데이터 유효성 검증 및 직렬화

핵심 구현 로직:
- Pydantic BaseModel을 상속하여 타입 안전성 보장
- Optional 필드로 플랫폼별 차이점 수용
- JSON 직렬화/역직렬화 자동 지원

@dependencies
- pydantic: 데이터 모델링 및 유효성 검증

@see {@link /docs/data-models.md} - 데이터 모델 상세 문서
"""

from typing import Optional

from pydantic import BaseModel


class Post(BaseModel):
    """
    SNS 게시글 데이터 모델

    모든 SNS 플랫폼의 게시글을 표현하는 공통 데이터 구조입니다.
    플랫폼별 특성에 따라 일부 필드는 None일 수 있습니다.

    본문 필드 규약 (플랫폼 유형별로 다르므로 소비 시 주의):
    - API형 (linkedin, threads, x, reddit): 본문 전체가 `content`에 담긴다.
      `content_markdown`/`summary`는 비어 있다.
    - Feed형 (hackernews, geeknews, arxiv, huggingface, youtube 등): `content`에는
      제목 수준 스니펫만 있고, 본문 전체는 `content_markdown`, 요약은 `summary`에 담긴다.
    즉 "본문"을 읽으려면 `content_markdown or content` 순으로 접근한다.

    Attributes:
        platform (str): 플랫폼 이름 (threads, linkedin, x, reddit, geeknews 등)
        author (str): 게시글 작성자 이름 또는 핸들
        content (str): API형은 본문 전체, Feed형은 제목 스니펫 (위 규약 참고)
        timestamp (str): 게시 시간 (ISO 8601 문자열)
        url (Optional[str]): 게시글 직접 링크
        likes (Optional[int]): 좋아요/추천 수
        comments (Optional[int]): 댓글 수
        reposts (Optional[int]): 리포스트/공유 수
        views (Optional[int]): 조회수 (X 등 지원 플랫폼만)
        title (Optional[str]): 게시글 제목 (HN, GeekNews, YouTube 등)
        summary (Optional[str]): 요약 (RSS 피드 등)
        content_markdown (Optional[str]): Feed형 enrichment 본문 마크다운
        word_count (Optional[int]): 본문 단어 수 (content_markdown or content 기준)
        source (Optional[str]): 세부 소스 (YouTube 채널명, 서브레딧 등)
        external_id (Optional[str]): 플랫폼 고유 ID
    """

    platform: str
    author: str
    content: str
    timestamp: str
    url: Optional[str] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    reposts: Optional[int] = None
    views: Optional[int] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    content_markdown: Optional[str] = None
    word_count: Optional[int] = None
    source: Optional[str] = None
    external_id: Optional[str] = None

    class Config:
        extra = "allow"  # 플랫폼별 추가 필드 허용

    def __str__(self) -> str:
        """게시글 정보를 읽기 쉬운 형태로 반환"""
        return f"[{self.platform}] @{self.author}: {self.content[:50]}..."
