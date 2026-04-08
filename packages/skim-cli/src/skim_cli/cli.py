"""Skim CLI entrypoint."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import typer

from skim_core.crawlers import REGISTRY
from skim_core.db import finish_run, init_db, save_posts, save_run
from skim_core.exporters import SheetsExporter
from skim_core.models import Post
from skim_core.paths import DATA_DIR
from skim_core.utils import save_posts_to_file

KST = timezone(timedelta(hours=9))

# SNS 크롤러: count 기반 (타임라인에서 N개 수집)
SNS_PLATFORMS = {"threads", "linkedin", "x", "reddit"}
SNS_DEFAULT_COUNT = 50

# Feed 크롤러: since 기반 (기간 내 모든 게시글 수집)
FEED_PLATFORMS = set(REGISTRY.keys()) - SNS_PLATFORMS

app = typer.Typer(
    name="crawl-sns",
    help="SNS 크롤링 도구 — 11개 플랫폼 지원",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

__version__ = "0.2.0"


async def run_single_crawler(platform: str, options: dict) -> List[Post]:
    """단일 크롤러를 실행합니다."""
    crawler_cls = REGISTRY[platform]
    crawler = crawler_cls()
    return await crawler.crawl(**options)


# === Commands ===


@app.command()
def crawl(  # noqa: C901 — CLI 진입점으로 플랫폼별 분기가 불가피
    platforms: List[str] = typer.Argument(
        None,
        help="크롤링할 플랫폼 (all, threads, linkedin, x, reddit, hackernews, geeknews, youtube, producthunt, arxiv, huggingface, everyto)",
    ),
    count: Optional[int] = typer.Option(
        None, "--count", "-c", help="수집할 게시글 수 (SNS 기본 50)"
    ),
    days: Optional[int] = typer.Option(None, "--days", help="최근 N일 이내 게시글 (feed 기본 1)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="출력 파일명"),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
    sheets: bool = typer.Option(False, "--sheets", "-s", help="구글 시트에 저장"),
    no_content: bool = typer.Option(False, "--no-content", help="콘텐츠 enrichment 스킵"),
    user_id: Optional[str] = typer.Option(
        None, "--user-id", "-u", help="특정 사용자 ID/screen name"
    ),
    subreddit: Optional[str] = typer.Option(
        None, "--subreddit", help="reddit 전용 subreddit slug (예: python)"
    ),
    sort: str = typer.Option("hot", "--sort", help="reddit 전용 정렬값 (hot, new)"),
):
    """하나 이상의 플랫폼에서 게시글을 크롤링합니다.

    예시:
        uv run skim crawl hackernews --count 10
        uv run skim crawl all --days 1 --no-content
        uv run skim crawl threads --user-id 314216 --count 5
        uv run skim crawl hackernews geeknews --days 1
    """
    if not platforms:
        platforms = ["all"]

    # 'all' 확장
    if "all" in platforms:
        targets = list(REGISTRY.keys())
    else:
        targets = []
        for p in platforms:
            if p not in REGISTRY:
                typer.echo(f"알 수 없는 플랫폼: {p}")
                typer.echo(f"지원 플랫폼: {', '.join(REGISTRY.keys())}")
                raise typer.Exit(1)
            targets.append(p)

    init_db()
    run_id = save_run()
    total_saved = 0
    now = datetime.now(KST)

    for platform in targets:
        typer.echo(f"[{platform.upper()}] 크롤링 시작...")

        is_sns = platform in SNS_PLATFORMS
        options: dict = {
            "debug": debug,
            "no_content": no_content,
        }

        if is_sns:
            # SNS: count 기반 (기본 50)
            options["count"] = count if count is not None else SNS_DEFAULT_COUNT
        else:
            # Feed: since 기반 (기본 전날 0시부터)
            if days is not None:
                d = days
            elif platform == "arxiv":
                # arXiv는 주말에 새 논문을 게시하지 않으므로 월/토/일은 4일 전까지 확인
                weekday = now.weekday()  # 0=Mon ... 6=Sun
                d = 4 if weekday in (0, 5, 6) else 2
            else:
                d = 1
            since = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
            options["since"] = since
            if count is not None:
                options["count"] = count

        if user_id:
            options["user_id"] = user_id
        if platform == "reddit":
            if subreddit:
                options["subreddit"] = subreddit
            options["sort"] = sort

        try:
            posts = asyncio.run(run_single_crawler(platform, options))
        except Exception as e:
            typer.echo(f"  [!] {platform} 크롤링 실패: {e}")
            if debug:
                import traceback  # pylint: disable=import-outside-toplevel

                traceback.print_exc()
            continue

        if not posts:
            typer.echo("  -> 수집된 게시글 없음")
            continue

        typer.echo(f"  -> {len(posts)}개 수집")

        # DB 저장
        saved = save_posts(posts, platform)
        total_saved += saved
        if saved:
            typer.echo(f"  -> DB 저장: {saved}개 (중복 제외)")

        # JSON 파일 저장
        if output and len(targets) == 1:
            filepath = output
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = DATA_DIR / platform / f"{timestamp}.json"
        save_posts_to_file(posts, filepath)
        typer.echo(f"  -> 파일: {filepath}")

        # 구글 시트 저장
        if sheets:
            try:
                exporter = SheetsExporter()
                exporter.export_posts(posts, platform)
                typer.echo("  -> 구글 시트 저장 완료")
            except Exception as e:
                typer.echo(f"  -> 구글 시트 저장 실패: {e}")

        if platform != targets[-1]:
            typer.echo()

    finish_run(run_id, "success", total_saved)
    typer.echo(f"\n완료: 총 {total_saved}개 저장 (run #{run_id})")


@app.command()
def login(
    platform: str = typer.Argument(
        "threads", help="로그인할 플랫폼 (threads, x, linkedin, reddit)"
    ),
):
    """SNS 플랫폼 로그인 (저장된 credential 자동 입력 후 쿠키 추출, 실패 시 수동 로그인 fallback)

    예시:
        uv run skim login threads
        uv run skim login x
    """
    from skim_core.crawlers.auth.cdp import login as cdp_login  # pylint: disable=import-outside-toplevel

    cdp_login(platform)


@app.command()
def platforms():
    """지원하는 플랫폼 목록을 출력합니다."""
    typer.echo("지원 플랫폼:")
    for name, crawler_cls in REGISTRY.items():
        module = crawler_cls.__module__
        category = module.split(".")[-2] if "." in module else "unknown"
        typer.echo(f"  {name:15s} ({category})")


@app.command()
def version():
    """버전 정보를 출력합니다."""
    typer.echo(f"SNS Crawler v{__version__}")


if __name__ == "__main__":
    app()
