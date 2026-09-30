"""
@file utils.py
@description 크롤링 관련 유틸리티 함수 모음

이 모듈은 SNS 크롤링 과정에서 사용되는 공통 유틸리티 함수들을 제공합니다.

주요 기능:
1. 게시글 데이터를 JSON 파일로 저장
2. 메타데이터 포함 저장 형식

핵심 구현 로직:
- 크롤링 결과를 구조화된 JSON 형태로 저장
- 메타데이터(총 게시글 수, 크롤링 시간, 플랫폼) 자동 포함
- UTF-8 인코딩으로 한글 안전 저장

@dependencies
- json: JSON 직렬화
- datetime: 타임스탬프 생성
- pathlib: 파일 경로 처리

@see {@link /docs/file-formats.md} - 저장 파일 형식 문서
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List

from .models import Post


def save_posts_to_file(posts: List[Post], filepath: str | Path) -> None:
    """
    게시글 목록을 JSON 파일로 저장합니다.

    Args:
        posts (List[Post]): 저장할 게시글 목록
        filepath (str): 저장할 파일 경로

    Note:
        - 메타데이터(총 게시글 수, 크롤링 시간, 플랫폼)를 자동으로 포함
        - UTF-8 인코딩으로 한글을 안전하게 저장
        - JSON 형태로 구조화하여 저장
    """
    output_data = {
        "metadata": {
            "total_posts": len(posts),
            "crawled_at": datetime.now().isoformat(),
            "platform": posts[0].platform if posts else "unknown",
        },
        "posts": [post.model_dump() for post in posts],
    }

    # 상위 디렉토리 생성
    target_path = Path(filepath)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
