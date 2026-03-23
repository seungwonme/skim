"""
@file main.py
@description SNS 크롤링 CLI 인터페이스

이 모듈은 여러 SNS 플랫폼의 게시글을 크롤링하기 위한 명령줄 인터페이스를 제공합니다.

주요 기능:
1. 플랫폼별 크롤링 명령어 (threads, linkedin, x, reddit)
2. 통일된 크롤링 옵션 설정 (게시글 수, 저장 위치, 디버그 모드)
3. 일관된 결과 출력 및 저장

핵심 구현 로직:
- Typer를 사용한 직관적인 CLI 인터페이스
- 로깅 데코레이터를 통한 통일된 작업 추적
- 모든 플랫폼에서 동일한 출력 형식 제공

@dependencies
- typer: CLI 프레임워크
- asyncio: 비동기 처리
- datetime: 파일명 생성용
- pathlib: 디렉토리 관리
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer

from src.crawlers.geeknews import fetch_geeknews
from src.crawlers.hackernews import fetch_hackernews
from src.crawlers.linkedin import LinkedInAPICrawler
from src.crawlers.reddit import RedditCrawler
from src.crawlers.threads_api import ThreadsAPICrawler
from src.crawlers.x_api import XAPICrawler
from src.exporters import SheetsExporter
from src.models import Post
from src.print import (
    log_crawl_operation,
    print_crawl_summary,
    print_no_posts_error,
    print_post_preview,
)

# === App Configuration ===
app = typer.Typer(
    name="crawl-sns",
    help="SNS 플랫폼(Threads, LinkedIn, X, Reddit) 크롤링 도구",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

__version__ = "0.1.0"


# === Utility Functions ===
def save_posts_to_file(posts: List[Post], filepath: str) -> None:
    """게시글 목록을 JSON 파일로 저장합니다."""
    output_data = {
        "metadata": {
            "total_posts": len(posts),
            "crawled_at": datetime.now().isoformat(),
            "platform": posts[0].platform if posts else "unknown",
        },
        "posts": [post.model_dump() for post in posts],
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)


def generate_output_filename(platform: str, custom_output: Optional[str] = None) -> str:
    """출력 파일명을 생성합니다."""
    if custom_output:
        return custom_output

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"data/{platform}/{timestamp}.json"


def ensure_data_directory(platform: str = None) -> None:
    """data 디렉토리와 플랫폼별 하위 디렉토리가 존재하는지 확인하고 생성합니다."""
    Path("data").mkdir(exist_ok=True)
    if platform:
        Path(f"data/{platform}").mkdir(exist_ok=True)


# === Platform Crawling Commands ===
@app.command()
@log_crawl_operation("threads")
def threads(
    count: int = typer.Option(5, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드 활성화 (상세 로그)"),
    sheets: bool = typer.Option(
        False, "--sheets", "-s", help="구글 시트에 저장 (GOOGLE_WEBAPP_URL 환경변수 필요)"
    ),
    user_id: Optional[str] = typer.Option(
        None, "--user-id", "-u", help="특정 사용자 ID로 피드 조회 (없으면 For You 타임라인)"
    ),
):
    """
    Threads에서 게시글을 크롤링합니다 (API 모드).

    세션이 없으면 먼저 로그인하세요: python main.py login

    예시:
    python main.py threads --count 10
    python main.py threads -c 3 -o my_threads.json
    python main.py threads --debug
    python main.py threads --sheets
    python main.py threads --user-id 314216  # 특정 사용자 피드
    """
    crawler = ThreadsAPICrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count, user_id=user_id))

    if not posts:
        print_no_posts_error("threads", debug)
        raise typer.Exit(1)

    # JSON 파일 저장 (기본)
    ensure_data_directory("threads")
    output_file = generate_output_filename("threads", output)
    save_posts_to_file(posts, output_file)

    # 구글 시트 저장 (옵션)
    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "threads")
        except ValueError as e:
            typer.echo(f"❌ 구글 시트 설정 오류: {str(e)}")
            sheets_success = False
        except Exception as e:
            typer.echo(f"❌ 구글 시트 저장 중 오류: {str(e)}")
            sheets_success = False

    # 결과 출력
    print_crawl_summary("threads", len(posts), output_file, debug)
    if sheets:
        if sheets_success:
            typer.echo("   📊 구글 시트 저장: ✅ 성공")
        else:
            typer.echo("   📊 구글 시트 저장: ❌ 실패 (JSON 파일은 저장됨)")

    print_post_preview(posts[0], "threads")


@app.command()
@log_crawl_operation("linkedin")
def linkedin(
    count: int = typer.Option(5, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드 활성화 (상세 로그)"),
    sheets: bool = typer.Option(
        False, "--sheets", "-s", help="구글 시트에 저장 (GOOGLE_WEBAPP_URL 환경변수 필요)"
    ),
):
    """
    LinkedIn에서 게시글을 크롤링합니다 (API 모드).

    세션이 없으면 먼저 로그인하세요: python main.py login linkedin

    예시:
    python main.py linkedin --count 10
    python main.py linkedin --debug
    python main.py linkedin --sheets
    """
    crawler = LinkedInAPICrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if not posts:
        print_no_posts_error("linkedin", debug)
        raise typer.Exit(1)

    # JSON 파일 저장 (기본)
    ensure_data_directory("linkedin")
    output_file = generate_output_filename("linkedin", output)
    save_posts_to_file(posts, output_file)

    # 구글 시트 저장 (옵션)
    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "linkedin")
        except ValueError as e:
            typer.echo(f"❌ 구글 시트 설정 오류: {str(e)}")
            sheets_success = False
        except Exception as e:
            typer.echo(f"❌ 구글 시트 저장 중 오류: {str(e)}")
            sheets_success = False

    # 결과 출력
    print_crawl_summary("linkedin", len(posts), output_file, debug)
    if sheets:
        if sheets_success:
            typer.echo("   📊 구글 시트 저장: ✅ 성공")
        else:
            typer.echo("   📊 구글 시트 저장: ❌ 실패 (JSON 파일은 저장됨)")

    print_post_preview(posts[0], "linkedin")


@app.command()
@log_crawl_operation("x")
def x(
    count: int = typer.Option(10, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
    sheets: bool = typer.Option(
        False, "--sheets", "-s", help="구글 시트에 저장 (GOOGLE_WEBAPP_URL 환경변수 필요)"
    ),
    user: Optional[str] = typer.Option(
        None, "--user", "-u", help="특정 사용자 screen name (없으면 For You 타임라인)"
    ),
):
    """
    X (Twitter)에서 게시글을 크롤링합니다 (API 모드).

    세션이 없으면 먼저 로그인하세요: python main.py login x

    예시:
    python main.py x --count 10
    python main.py x --user elonmusk --count 5
    python main.py x --debug
    python main.py x --sheets
    """
    crawler = XAPICrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count, user_id=user))

    if not posts:
        print_no_posts_error("x", debug)
        raise typer.Exit(1)

    # JSON 파일 저장 (기본)
    ensure_data_directory("x")
    output_file = generate_output_filename("x", output)
    save_posts_to_file(posts, output_file)

    # 구글 시트 저장 (옵션)
    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "x")
        except ValueError as e:
            typer.echo(f"❌ 구글 시트 설정 오류: {str(e)}")
            sheets_success = False
        except Exception as e:
            typer.echo(f"❌ 구글 시트 저장 중 오류: {str(e)}")
            sheets_success = False

    # 결과 출력
    print_crawl_summary("x", len(posts), output_file, debug)
    if sheets:
        if sheets_success:
            typer.echo("   📊 구글 시트 저장: ✅ 성공")
        else:
            typer.echo("   📊 구글 시트 저장: ❌ 실패 (JSON 파일은 저장됨)")

    print_post_preview(posts[0], "x")


@app.command()
@log_crawl_operation("reddit")
def reddit(
    count: int = typer.Option(10, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
    sheets: bool = typer.Option(
        False, "--sheets", "-s", help="구글 시트에 저장 (GOOGLE_WEBAPP_URL 환경변수 필요)"
    ),
):
    """
    Reddit에서 게시글을 크롤링합니다.

    예시:
    python main.py reddit --count 10
    python main.py reddit -c 5 -o my_reddit_posts.json
    python main.py reddit --debug
    python main.py reddit --sheets  # 구글 시트에 저장
    python main.py reddit -c 5 -s  # 5개 게시글을 구글 시트에 저장
    """
    crawler = RedditCrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if not posts:
        print_no_posts_error("reddit", debug)
        raise typer.Exit(1)

    # JSON 파일 저장 (기본)
    ensure_data_directory("reddit")
    output_file = generate_output_filename("reddit", output)
    save_posts_to_file(posts, output_file)

    # 구글 시트 저장 (옵션)
    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "reddit")
        except ValueError as e:
            typer.echo(f"❌ 구글 시트 설정 오류: {str(e)}")
            sheets_success = False
        except Exception as e:
            typer.echo(f"❌ 구글 시트 저장 중 오류: {str(e)}")
            sheets_success = False

    # 결과 출력
    print_crawl_summary("reddit", len(posts), output_file, debug)
    if sheets:
        if sheets_success:
            typer.echo("   📊 구글 시트 저장: ✅ 성공")
        else:
            typer.echo("   📊 구글 시트 저장: ❌ 실패 (JSON 파일은 저장됨)")

    print_post_preview(posts[0], "reddit")


@app.command()
@log_crawl_operation("hackernews")
def hackernews(
    count: int = typer.Option(10, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
    sheets: bool = typer.Option(False, "--sheets", "-s", help="구글 시트에 저장"),
):
    """
    Hacker News에서 Top Stories를 가져옵니다. (API 사용, 브라우저 불필요)

    예시:
    python main.py hackernews --count 20
    python main.py hackernews -c 5 -o my_hn.json
    """
    posts = fetch_hackernews(count)

    if not posts:
        print_no_posts_error("hackernews", debug)
        raise typer.Exit(1)

    ensure_data_directory("hackernews")
    output_file = generate_output_filename("hackernews", output)
    save_posts_to_file(posts, output_file)

    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "hackernews")
        except Exception as e:
            typer.echo(f"구글 시트 저장 실패: {e}")

    print_crawl_summary("hackernews", len(posts), output_file, debug)
    if sheets:
        typer.echo(f"   구글 시트 저장: {'성공' if sheets_success else '실패'}")
    print_post_preview(posts[0], "hackernews")


@app.command()
@log_crawl_operation("geeknews")
def geeknews(
    count: int = typer.Option(10, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
    sheets: bool = typer.Option(False, "--sheets", "-s", help="구글 시트에 저장"),
):
    """
    GeekNews (news.hada.io)에서 게시글을 가져옵니다. (스크래핑, 브라우저 불필요)

    예시:
    python main.py geeknews --count 20
    python main.py geeknews -c 5 -o my_geeknews.json
    """
    posts = fetch_geeknews(count)

    if not posts:
        print_no_posts_error("geeknews", debug)
        raise typer.Exit(1)

    ensure_data_directory("geeknews")
    output_file = generate_output_filename("geeknews", output)
    save_posts_to_file(posts, output_file)

    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "geeknews")
        except Exception as e:
            typer.echo(f"구글 시트 저장 실패: {e}")

    print_crawl_summary("geeknews", len(posts), output_file, debug)
    if sheets:
        typer.echo(f"   구글 시트 저장: {'성공' if sheets_success else '실패'}")
    print_post_preview(posts[0], "geeknews")


# === Auth Commands ===
@app.command()
def login(
    platform: str = typer.Argument("threads", help="로그인할 플랫폼 (threads, x)"),
):
    """SNS 플랫폼 로그인 (Chrome에서 수동 로그인 후 쿠키 자동 추출)

    Chrome이 열리면 해당 플랫폼에 로그인하세요.
    로그인이 완료되면 자동으로 쿠키가 추출되어 저장됩니다.

    예시:
    python main.py login threads
    python main.py login x
    """
    from src.crawlers.cdp_auth import login as cdp_login  # pylint: disable=import-outside-toplevel

    cdp_login(platform)


# === Utility Commands ===
@app.command()
def version():
    """버전 정보를 출력합니다."""
    typer.echo(f"SNS Crawler v{__version__}")
    typer.echo("SNS 크롤링 도구 (API + Playwright)")


@app.command()
def status():
    """현재 크롤러 상태를 확인합니다."""
    typer.echo("📋 SNS Crawler 상태:")
    typer.echo("   ✅ Threads 크롤러 - 구현 완료")
    typer.echo("   ✅ LinkedIn 크롤러 - 구현 완료")
    typer.echo("   🔧 X 크롤러 - 구현 완료")
    typer.echo("   🔧 Reddit 크롤러 - 구현 완료")
    typer.echo("   ✅ Hacker News 크롤러 - 구현 완료 (API)")
    typer.echo("   ✅ GeekNews 크롤러 - 구현 완료 (스크래핑)")


if __name__ == "__main__":
    app()
