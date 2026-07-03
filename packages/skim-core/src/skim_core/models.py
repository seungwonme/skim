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

    필드 규약 (모든 플랫폼 공통):
    - `content_markdown`: **정본 본문**. 모든 플랫폼에서 게시글 본문이 여기 담긴다.
      Feed형은 enrichment된 원문 마크다운, API형(linkedin/threads/x/reddit)은 게시글 텍스트.
      본문을 읽는 소비자는 항상 이 필드를 본다.
    - `title`: **정본 제목**. 제목이 있는 플랫폼(HN, GeekNews, arXiv, YouTube 등)에서 채워진다.
    - `content`: 플랫폼 원본 텍스트. API형은 본문 원문(= content_markdown과 동일), Feed형은
      비운다(제목은 `title`, 본문은 `content_markdown`에 있으므로). 과거 데이터 하위호환용.
    - `summary`: 요약/발췌 (RSS 피드 등).

    Attributes:
        platform (str): 플랫폼 이름 (threads, linkedin, x, reddit, geeknews 등)
        author (str): 게시글 작성자 이름 또는 핸들
        content (str): 플랫폼 원본 텍스트 (본문은 content_markdown 참조)
        timestamp (str): 게시 시간 (ISO 8601 문자열)
        url (Optional[str]): 게시글 직접 링크
        likes (Optional[int]): 좋아요/추천 수
        comments (Optional[int]): 댓글 수
        reposts (Optional[int]): 리포스트/공유 수
        views (Optional[int]): 조회수 (X 등 지원 플랫폼만)
        title (Optional[str]): 정본 제목 (HN, GeekNews, YouTube 등)
        summary (Optional[str]): 요약 (RSS 피드 등)
        content_markdown (Optional[str]): 정본 본문 (전 플랫폼)
        word_count (Optional[int]): 본문 단어 수 (content_markdown 기준)
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
        preview = (self.title or self.content_markdown or self.content or "")[:50]
        return f"[{self.platform}] @{self.author}: {preview}..."
