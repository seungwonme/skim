"""
@file src/print.py
@description SNS 크롤링 애플리케이션용 로깅 및 출력 유틸리티

이 모듈은 크롤링 작업에 대한 일관된 로깅과 출력 기능을 제공합니다.

주요 기능:
1. 크롤링 작업 추적을 위한 데코레이터
2. 플랫폼별 통일된 출력 형식
3. 디버그 정보 및 에러 처리
4. 성능 모니터링 및 로깅

핵심 구현 로직:
- 데코레이터를 통한 횡단 관심사 분리
- 구조화된 로깅으로 분석 용이성 향상
- 타입 안전성을 위한 ParamSpec 사용
- 컨텍스트 정보 포함으로 추적성 향상

@dependencies
- typer: CLI 출력
- functools: 데코레이터 메타데이터 보존
- typing: 타입 힌트
- time: 성능 측정
- datetime: 타임스탬프
- json: 구조화된 로깅
- uuid: 고유 식별자 생성
"""

import functools
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, Optional, ParamSpec, TypeVar

import typer

# === Type Variables ===
P = ParamSpec("P")
T = TypeVar("T")


# === Logging Context Management ===
class LoggingContext:
    """로깅 컨텍스트 관리 클래스"""

    def __init__(self):
        self.operation_id: Optional[str] = None
        self.platform: Optional[str] = None
        self.start_time: Optional[float] = None

    def set_context(self, platform: str, operation_id: Optional[str] = None):
        """로깅 컨텍스트 설정"""
        self.platform = platform
        self.operation_id = operation_id or str(uuid.uuid4())[:8]
        self.start_time = time.time()

    def get_context_info(self) -> Dict[str, Any]:
        """현재 컨텍스트 정보 반환"""
        return {
            "operation_id": self.operation_id,
            "platform": self.platform,
            "timestamp": datetime.now().isoformat(),
            "elapsed_time": time.time() - self.start_time if self.start_time else 0,
        }


# 전역 로깅 컨텍스트
_logging_context = LoggingContext()


# === Logging Decorators ===
def log_crawl_operation(platform: str):
    """
    크롤링 작업을 추적하는 데코레이터

    Args:
        platform: 크롤링 플랫폼 이름 (threads, linkedin, x 등)
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # 컨텍스트 설정
            operation_id = str(uuid.uuid4())[:8]
            _logging_context.set_context(platform, operation_id)

            # 시작 로그
            count = kwargs.get("count", args[0] if args else "unknown")
            debug = kwargs.get("debug", False)

            typer.echo(f"🚀 [{operation_id}] {platform.upper()} 크롤링 시작")
            typer.echo(f"   📊 목표 게시글: {count}개")
            typer.echo(f"   🔧 디버그 모드: {'활성화' if debug else '비활성화'}")

            if debug:
                print_debug_mode_info(platform)

            try:
                # 원본 함수 실행
                result = func(*args, **kwargs)

                # 성공 로그
                execution_time = time.time() - (_logging_context.start_time or 0)
                typer.echo(
                    f"✅ [{operation_id}] {platform.upper()} 크롤링 완료 ({execution_time:.2f}초)"
                )

                return result

            except Exception as e:
                # 에러 로그
                execution_time = time.time() - (_logging_context.start_time or 0)
                typer.echo(
                    f"❌ [{operation_id}] {platform.upper()} 크롤링 실패 ({execution_time:.2f}초)"
                )
                typer.echo(f"   🔍 에러: {str(e)}")
                if debug:
                    print_error_debug_info(platform, str(e))
                raise

        return wrapper

    return decorator


def log_performance(threshold: float = 1.0):
    """
    함수 성능을 모니터링하는 데코레이터

    Args:
        threshold: 경고를 발생시킬 실행 시간 임계값 (초)
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            start_time = time.time()
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time

            if execution_time > threshold:
                context = _logging_context.get_context_info()
                typer.echo(
                    f"⚠️  [{context.get('operation_id', 'unknown')}] "
                    f"느린 함수 감지: {func.__name__} ({execution_time:.2f}초)"
                )

            return result

        return wrapper

    return decorator


# === Output Functions ===
def print_debug_mode_info(platform: str) -> None:
    """디버그 모드 정보 출력"""
    typer.echo("🐛 디버그 모드 활성화:")
    typer.echo("   - 브라우저가 표시됩니다")
    typer.echo("   - 상세한 로그가 출력됩니다")
    if platform == "threads":
        typer.echo("   - 스크린샷이 저장됩니다")
        typer.echo("   - data/debug_screenshots/ 폴더 확인")


def print_crawl_summary(
    platform: str, post_count: int, output_file: str, debug: bool = False
) -> None:
    """크롤링 완료 요약 출력"""
    context = _logging_context.get_context_info()

    typer.echo("\n📊 크롤링 완료 요약:")
    typer.echo(f"   - 작업 ID: {context.get('operation_id', 'unknown')}")
    typer.echo(f"   - 플랫폼: {platform.upper()}")
    typer.echo(f"   - 수집된 게시글: {post_count}개")
    typer.echo(f"   - 저장 위치: {output_file}")
    typer.echo(f"   - 실행 시간: {context.get('elapsed_time', 0):.2f}초")

    if debug:
        typer.echo(f"   - 디버그 세션: data/{platform}_session.json")


def print_post_preview(post, platform: str) -> None:
    """첫 번째 게시글 미리보기 출력"""
    if not post:
        return

    context = _logging_context.get_context_info()
    typer.echo(f"\n📄 [{context.get('operation_id', 'unknown')}] 첫 번째 게시글 미리보기:")
    typer.echo(f"   📝 작성자: {post.author}")
    preview = post.title or post.content_markdown or post.content or ""
    typer.echo(f"   📄 내용: {preview[:100]}...")
    typer.echo(f"   📅 시간: {post.timestamp}")

    # 플랫폼별 추가 정보
    if hasattr(post, "likes") and post.likes:
        emoji = "❤️" if platform == "threads" else "👍" if platform == "linkedin" else "🤍"
        typer.echo(f"   {emoji} 좋아요: {post.likes}")

    if hasattr(post, "comments") and post.comments:
        typer.echo(f"   💬 댓글: {post.comments}")

    if hasattr(post, "reposts") and post.reposts:
        typer.echo(f"   🔄 리포스트: {post.reposts}")

    if hasattr(post, "views") and post.views:
        typer.echo(f"   👀 조회수: {post.views}")


def print_error_debug_info(platform: str, error_message: str) -> None:
    """에러 디버그 정보 출력"""
    context = _logging_context.get_context_info()

    typer.echo(f"\n🔍 [{context.get('operation_id', 'unknown')}] 디버그 정보:")
    typer.echo(f"   - 플랫폼: {platform}")
    typer.echo(f"   - 에러 메시지: {error_message}")
    typer.echo(f"   - 발생 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 플랫폼별 디버그 가이드
    if platform == "threads":
        typer.echo("   💡 해결 방법:")
        typer.echo("     - data/debug_screenshots/ 폴더의 스크린샷 확인")
        typer.echo("     - uv run skim login threads 로 세션 갱신")
    elif platform == "linkedin":
        typer.echo("   💡 해결 방법:")
        typer.echo("     - 브라우저에서 수동 로그인 확인")
        typer.echo("     - uv run skim login linkedin 로 세션 갱신")

    typer.echo("     - 로그 메시지에서 추가 오류 원인 확인")


def print_no_posts_error(platform: str, debug: bool = False) -> None:
    """게시글 없음 에러 출력"""
    context = _logging_context.get_context_info()

    typer.echo(f"❌ [{context.get('operation_id', 'unknown')}] 크롤링된 게시글이 없습니다.")

    if debug:
        print_error_debug_info(platform, "No posts found")


# === Structured Logging ===
@contextmanager
def structured_logging(platform: str, operation: str):
    """구조화된 로깅을 위한 컨텍스트 매니저"""
    operation_id = str(uuid.uuid4())[:8]
    start_time = time.time()

    # 시작 로그
    log_entry: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "operation_id": operation_id,
        "platform": platform,
        "operation": operation,
        "status": "started",
    }

    try:
        yield operation_id

        # 성공 로그
        log_entry.update({"status": "completed", "duration": time.time() - start_time})

    except Exception as e:
        # 실패 로그
        log_entry.update(
            {"status": "failed", "error": str(e), "duration": time.time() - start_time}
        )
        raise
    finally:
        # 구조화된 로그 출력 (옵션)
        # print(json.dumps(log_entry, ensure_ascii=False, indent=2))
        pass


# === Legacy Support ===
def print_debug(count: int, debug: bool, platform: str = "unknown") -> None:
    """
    기존 print_debug 함수 (하위 호환성)

    Deprecated: log_crawl_operation 데코레이터 사용 권장
    """
    if debug:
        typer.echo(f"🐛 디버그 모드로 {platform.upper()} {count}개 게시글 크롤링을 시작합니다...")
        print_debug_mode_info(platform)
