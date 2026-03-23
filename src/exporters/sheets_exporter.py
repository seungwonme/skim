"""
@file sheets_exporter.py
@description Google Sheets 내보내기 클래스

이 모듈은 크롤링된 SNS 데이터를 Google Apps Script 웹앱을 통해
Google Sheets에 저장하는 기능을 제공합니다.

핵심 구현 로직:
- HTTP POST 요청으로 Apps Script 웹앱에 데이터 전송
- JSON 형태의 Post 데이터를 2D 테이블로 변환하여 저장
- 에러 처리 및 사용자 피드백 제공

@dependencies
- requests: HTTP 요청
- typing: 타입 힌트
- datetime: 타임스탬프 생성
"""

import json
import os
from datetime import datetime
from typing import List, Optional

import requests
import typer

from src.models import Post


class SheetsExporter:
    """Google Sheets로 데이터를 내보내는 클래스"""

    def __init__(self, webapp_url: Optional[str] = None):
        """
        SheetsExporter 초기화

        Args:
            webapp_url: Google Apps Script 웹앱 URL (None이면 환경변수에서 가져옴)
        """
        self.webapp_url = webapp_url or os.getenv("GOOGLE_WEBAPP_URL")

        if not self.webapp_url:
            raise ValueError(
                "GOOGLE_WEBAPP_URL 환경변수가 설정되지 않았습니다. "
                ".env 파일에 GOOGLE_WEBAPP_URL을 추가해주세요."
            )

    def export_posts(self, posts: List[Post], platform: str) -> bool:
        """
        Posts를 Google Sheets로 내보냅니다

        Args:
            posts: 내보낼 게시글 목록
            platform: 플랫폼 이름 (threads, linkedin, x, reddit)

        Returns:
            bool: 성공 여부
        """
        if not self.webapp_url:
            typer.echo("❌ 웹앱 URL이 설정되지 않았습니다.")
            return False

        typer.echo(f"📊 구글 시트에 {platform} 데이터 업로드 중...")

        # 요청 데이터 구성
        payload = {
            "metadata": {
                "platform": platform,
                "total_posts": len(posts),
                "crawled_at": datetime.now().isoformat(),
            },
            "posts": [self._serialize_post(post) for post in posts],
        }

        try:
            # Apps Script 웹앱에 POST 요청
            response = requests.post(
                self.webapp_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    sheet_url = result.get("sheetUrl", "N/A")
                    typer.echo("✅ 구글 시트 저장 완료!")
                    typer.echo(f"   📊 시트 URL: {sheet_url}")
                    return True
                else:
                    error_msg = result.get("error", "알 수 없는 오류")
                    typer.echo(f"❌ 구글 시트 저장 실패: {error_msg}")
                    return False
            else:
                typer.echo(f"❌ HTTP 오류 {response.status_code}: {response.text}")
                return False

        except requests.exceptions.Timeout:
            typer.echo("❌ 요청 시간 초과 (30초). 구글 시트 서버가 응답하지 않습니다.")
            return False
        except requests.exceptions.ConnectionError:
            typer.echo("❌ 연결 실패. 인터넷 연결 및 웹앱 URL을 확인해주세요.")
            return False
        except json.JSONDecodeError:
            typer.echo("❌ 응답 형식 오류. 웹앱에서 올바른 JSON을 반환하지 않습니다.")
            return False
        except Exception as e:
            typer.echo(f"❌ 예상치 못한 오류: {str(e)}")
            return False

    def _serialize_post(self, post: Post) -> dict:
        """
        Post 객체를 직렬화 가능한 딕셔너리로 변환

        Args:
            post: 변환할 Post 객체

        Returns:
            dict: 직렬화된 Post 데이터
        """
        return {
            "author": post.author or "",
            "content": post.content or "",
            "timestamp": post.timestamp or "",
            "likes": post.likes or 0,
            "comments": post.comments or 0,
            "reposts": post.reposts or 0,
            "views": post.views or 0,
            "url": post.url or "",
            "platform": post.platform or "",
        }
