"""Skim CLI entrypoint."""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import typer

from skim_core.crawlers import REGISTRY
from skim_core.crawlers.auth.cdp import login as cdp_login
from skim_core.db import finish_run, init_db, save_posts, save_run, update_run_progress
from skim_core.models import Post
from skim_core.paths import DATA_DIR
from skim_core.research.refresh import run_research
from skim_core.research.search import search_posts
from skim_core.research.serializer import build_response, utc_now_iso
from skim_core.research.types import SearchStats
from skim_core.utils import save_posts_to_file

KST = timezone(timedelta(hours=9))

# SNS 크롤러: count 기반 (타임라인에서 N개 수집)
SNS_PLATFORMS = {"threads", "linkedin", "x", "reddit"}
SNS_DEFAULT_COUNT = 50

# Feed 크롤러: since 기반 (기간 내 모든 게시글 수집)
FEED_PLATFORMS = set(REGISTRY.keys()) - SNS_PLATFORMS


def platform_help(include_all: bool = False) -> str:
    """User-facing platform list derived from the crawler registry."""
    names = list(REGISTRY.keys())
    if include_all:
        names = ["all", *names]
    return ", ".join(names)


app = typer.Typer(
    name="crawl-sns",
    help=f"SNS 크롤링 도구 — {len(REGISTRY)}개 플랫폼 지원",
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
        help=f"크롤링할 플랫폼 ({platform_help(include_all=True)})",
    ),
    count: Optional[int] = typer.Option(
        None, "--count", "-c", help="수집할 게시글 수 (SNS 기본 50)"
    ),
    days: Optional[int] = typer.Option(None, "--days", help="최근 N일 이내 게시글 (feed 기본 1)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="출력 파일명"),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
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
                typer.echo(f"지원 플랫폼: {platform_help()}")
                raise typer.Exit(1)
            targets.append(p)

    init_db()
    run_id = save_run()
    total_saved = 0
    now = datetime.now(KST)
    active_platform: Optional[str] = None
    failed_platforms: list[str] = []
    completed_platforms: list[str] = []

    try:
        for platform in targets:
            active_platform = platform
            update_run_progress(run_id, platform, f"{platform} 크롤링 시작")
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
                failed_platforms.append(platform)
                update_run_progress(run_id, platform, f"{platform} 크롤링 실패: {e}")
                typer.echo(f"  [!] {platform} 크롤링 실패: {e}")
                if debug:
                    import traceback  # pylint: disable=import-outside-toplevel

                    traceback.print_exc()
                continue

            if count is not None:
                posts = posts[:count]

            completed_platforms.append(platform)
            if not posts:
                update_run_progress(run_id, platform, f"{platform} 수집된 게시글 없음")
                typer.echo("  -> 수집된 게시글 없음")
                continue

            typer.echo(f"  -> {len(posts)}개 수집")

            # DB 저장
            saved = save_posts(posts, platform)
            total_saved += saved
            if saved:
                typer.echo(f"  -> DB 반영: {saved}개 (신규/보강)")

            # JSON 파일 저장
            if output and len(targets) == 1:
                filepath = output
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = DATA_DIR / platform / f"{timestamp}.json"
            save_posts_to_file(posts, filepath)
            typer.echo(f"  -> 파일: {filepath}")

            update_run_progress(run_id, platform, f"{platform} 처리 완료: {saved}개 DB 반영")

            if platform != targets[-1]:
                typer.echo()
    except Exception as e:
        summary = (
            f"예상치 못한 오류로 중단"
            f"{f' (플랫폼: {active_platform})' if active_platform else ''}: {e}"
        )
        finish_run(run_id, "failed", total_saved, summary)
        raise

    if failed_platforms:
        summary = f"실패 플랫폼: {', '.join(failed_platforms)}"
        if completed_platforms:
            summary += f"; 완료 플랫폼: {', '.join(completed_platforms)}"
        finish_run(run_id, "failed", total_saved, summary)
        typer.echo(
            f"\n완료: 총 {total_saved}개 저장 (run #{run_id}, 실패: {', '.join(failed_platforms)})"
        )
        return

    finish_run(run_id, "success", total_saved, "전체 플랫폼 처리 완료")
    typer.echo(f"\n완료: 총 {total_saved}개 저장 (run #{run_id})")


@app.command()
def login(
    platform: str = typer.Argument(
        "threads", help="로그인할 플랫폼 (threads, x, linkedin, reddit)"
    ),
    identifier: Optional[str] = typer.Option(
        None, "--identifier", "-u", help="로그인 ID/email/username"
    ),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="로그인 password"),
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help="stdin 첫 줄에서 password 읽기"
    ),
    save_credential: bool = typer.Option(
        False, "--save-credential", help="입력한 credential을 macOS Keychain에 저장"
    ),
    no_keychain: bool = typer.Option(False, "--no-keychain", help="macOS Keychain 조회 안 함"),
):
    """SNS 플랫폼 로그인 (저장된 credential 자동 입력 후 쿠키 추출, 실패 시 수동 로그인 fallback)

    예시:
        uv run skim login threads
        uv run skim login x
        uv run skim login threads --identifier user@example.com --password-stdin
    """
    if password_stdin:
        if password:
            typer.echo("--password와 --password-stdin은 함께 쓸 수 없습니다.")
            raise typer.Exit(1)
        password = sys.stdin.readline().rstrip("\r\n")

    cdp_login(
        platform,
        login_identifier=identifier,
        password=password,
        use_keychain=not no_keychain,
        save_credential=save_credential,
    )


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


VALID_REFRESH_MODES = {"auto", "never", "force"}
VALID_EMIT_MODES = {"json", "jsonl", "summary"}


@app.command()
def research(
    topic: str = typer.Argument(..., help="검색 topic (공백 구분 토큰화)"),
    days: int = typer.Option(7, "--days", help="최근 N일 (UTC)"),
    sources: str = typer.Option("all", "--sources", help="쉼표 구분 플랫폼, 또는 'all'"),
    limit: int = typer.Option(50, "--limit", help="플랫폼별 최대 반환 수"),
    emit: str = typer.Option("json", "--emit", help="출력 포맷: json|jsonl|summary"),
    refresh: str = typer.Option(
        "never",
        "--refresh",
        help="auto|never|force (Phase 1 에서는 never 고정)",
    ),
):
    """topic 으로 posts 를 필터링해 구조화 JSON 으로 반환.

    Phase 1 단독 실행: `--refresh never`. auto/force 는 Phase 2 에서 활성화.
    """
    if not topic.strip():
        typer.echo("Usage: skim research TOPIC [OPTIONS]", err=True)
        raise typer.Exit(code=2)

    if refresh not in VALID_REFRESH_MODES:
        typer.echo(
            f"[skim] invalid --refresh value: {refresh!r}. "
            f"choose from {sorted(VALID_REFRESH_MODES)}",
            err=True,
        )
        raise typer.Exit(code=2)

    if emit not in VALID_EMIT_MODES:
        typer.echo(
            f"[skim] invalid --emit value: {emit!r}. " f"choose from {sorted(VALID_EMIT_MODES)}",
            err=True,
        )
        raise typer.Exit(code=2)

    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    explicit_sources = source_list != ["all"]
    if explicit_sources:
        unknown = [p for p in source_list if p not in REGISTRY]
        if unknown:
            typer.echo(f"[skim] unknown sources: {unknown}", err=True)
            typer.echo(f"supported: {', '.join(REGISTRY.keys())}", err=True)
            raise typer.Exit(code=2)
        sources_for_search: Optional[List[str]] = source_list
    else:
        sources_for_search = None

    init_db()
    tokens = [t.lower() for t in topic.split() if t.strip()]

    # Phase 2: refresh!=never 면 run_research 로 위임 (auto-refresh + lock + research_runs)
    if refresh != "never":
        if not tokens:
            # 토큰 0개면 refresh 무의미 — Phase 1 동작 (warning + 빈 결과)
            now_utc = datetime.now(timezone.utc)
            since_utc_iso = (now_utc - timedelta(days=days)).isoformat()

            response = build_response(
                topic=topic,
                tokens=tokens,
                date_range={"from": since_utc_iso, "to": utc_now_iso()},
                sources_requested=source_list,
                posts=[],
                search_stats=SearchStats(),
                days_requested=days,
                warnings=["no searchable tokens in topic"],
            )
            _emit_response(response, emit)
            return

        sources_for_search_resolved = sources_for_search or list(REGISTRY.keys())
        exit_code, response = asyncio.run(
            run_research(
                topic=topic,
                sources=sources_for_search_resolved,
                days=days,
                limit=limit,
                refresh_mode=refresh,
                explicit=explicit_sources,
            )
        )
        if exit_code != 0:
            sys.stderr.write(f"[skim] research exited with code {exit_code}\n")
            raise typer.Exit(code=exit_code)

        if response.get("stats"):
            s = response["stats"]
            sys.stderr.write(
                f"[skim research stats] topic={topic!r} tokens={len(tokens)} "
                f"rows_scanned={s['rows_scanned']} rows_returned={s['rows_returned']} "
                f"latency_ms={s['latency_ms']} newly_fetched={s['newly_fetched']}\n"
            )
            # short_tokens 경고는 Phase 1 path 와 일관되게 응답 warnings 에 추가
            if s.get("short_tokens"):
                msg = (
                    f"short tokens (<=2 chars): {s['short_tokens']}. "
                    "substring false positives likely."
                )
                if msg not in response["warnings"]:
                    response["warnings"].append(msg)
        _emit_response(response, emit)
        return

    # refresh == 'never' 경로 (Phase 1 동작)
    now_utc = datetime.now(timezone.utc)
    since_utc_iso = (now_utc - timedelta(days=days)).isoformat()

    warnings: List[str] = []
    if not tokens:
        warnings.append("no searchable tokens in topic")
        posts_raw: list = []

        stats = SearchStats()
        search_warnings: List[str] = []
    else:
        posts_raw, stats, search_warnings = search_posts(
            topic, since_utc_iso, sources_for_search, limit
        )

    warnings.extend(search_warnings)

    if stats.short_tokens:
        warnings.append(
            f"short tokens (<=2 chars): {stats.short_tokens}. substring false positives likely."
        )

    sys.stderr.write(
        f"[skim research stats] topic={topic!r} tokens={len(tokens)} "
        f"rows_scanned={stats.rows_scanned} rows_returned={stats.rows_returned} "
        f"latency_ms={stats.latency_ms}\n"
    )

    response = build_response(
        topic=topic,
        tokens=tokens,
        date_range={"from": since_utc_iso, "to": utc_now_iso()},
        sources_requested=source_list,
        posts=posts_raw,
        search_stats=stats,
        days_requested=days,
        warnings=warnings,
    )
    _emit_response(response, emit)


def _emit_response(response: dict, emit: str) -> None:
    if emit == "json":
        typer.echo(json.dumps(response, ensure_ascii=False, indent=2))
    elif emit == "jsonl":
        typer.echo(json.dumps(response, ensure_ascii=False))
    elif emit == "summary":
        typer.echo(
            f"topic={response.get('topic')!r} total={response['stats']['total']} "
            f"by_platform={response['stats']['by_platform']}"
        )


if __name__ == "__main__":
    app()
