#!/usr/bin/env python
"""SNS 크롤러를 바꾼 뒤 운영 회차와 나란히 비교한다 (#105).

운영 전용 프로필(`~/.skim/chrome-profile`)을 그대로 열고, 수집 결과만 임시 작업공간
(SKIM_WORKSPACE_ROOT)에 저장한다. 운영 DB와 데일리 JSON은 건드리지 않는다.
프로필을 복사해 시험하지 않는다. 사본에서 세션을 처음 쓰면 사이트가 로그인 쿠키를 다시
발급해 운영 프로필의 로그인이 풀린다(2026-10-20 LinkedIn `li_at`, 재로그인으로 복구).

찍는 것:
  - 글 수, 고유 id, 댓글이 있는 글과 그중 댓글 섹션이 붙은 글, 빈 본문, content_status.
    이번 실행과 운영 데이터의 마지막 회차 JSON을 나란히 놓는다.
  - 이번 실행이 보낸 요청. 브라우저의 문서 로드와 xhr/fetch, requests 호출을 종류별로 센다.
    x의 첫 구현이 트윗마다 페이지를 새로 받아 20건에 GraphQL 약 210건(예전 약 16건)을
    보낸 것을 이 숫자로 찾았다.
  - 소요 시간.

사용:
    uv run python scripts/compare_sns_crawl.py x --count 20
    uv run python scripts/compare_sns_crawl.py threads linkedin --count 20
    uv run python scripts/compare_sns_crawl.py reddit --count 20 --baseline data/reddit/<회차>.json
"""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = ("threads", "x", "linkedin", "reddit")

# 애그리게이터와 SNS 본문 뒤에 붙는 댓글 섹션 (`## X Replies` 등).
COMMENT_SECTION = re.compile(r"^## .*(?:Comments|Replies)\s*$", re.MULTILINE)

# 주소 경로에서 id로 보이는 조각. 숫자만이거나, 16자 이상에 숫자와 글자가 섞인 것.
# x GraphQL 경로(`/i/api/graphql/<id>/HomeTimeline`)의 쿼리 이름은 글자만이라 남는다.
_ID_SEGMENT = re.compile(r"^(?:\d+|(?=[^/]*\d)(?=[^/]*[A-Za-z])[A-Za-z0-9_-]{16,})$")

# 센 요청 종류. 스크립트, 이미지, 글꼴은 웹앱이 화면을 그리려고 받는 것이라 뺀다.
_COUNTED_TYPES = frozenset({"document", "xhr", "fetch"})


def request_key(url: str, kind: str, query_name: Optional[str] = None) -> str:
    """요청을 묶는 이름. id 조각은 `*`로 바꾸고, 쿼리 이름이 있으면 덧붙인다."""
    parts = urlsplit(url)
    path = "/".join(
        "*" if _ID_SEGMENT.match(segment) else segment
        for segment in parts.path.split("/")
    )
    key = f"{kind} {parts.netloc}{path}"
    return f"{key} {query_name}" if query_name else key


class RequestTally:
    """한 플랫폼 실행 동안 나간 요청을 종류별로 센다."""

    def __init__(
        self, query_name: Callable[[Optional[str], Dict[str, str]], Optional[str]]
    ):
        self.counts: Counter = Counter()
        self._query_name = query_name

    def browser(self, request: Any) -> None:
        kind = request.resource_type
        if kind not in _COUNTED_TYPES:
            return
        name = None
        if kind != "document":
            try:
                name = self._query_name(request.post_data, request.headers)
            except Exception:  # noqa: BLE001 - 본문을 못 읽는 요청은 이름 없이 센다
                name = None
        self.counts[request_key(request.url, kind, name)] += 1

    def http(self, url: str) -> None:
        self.counts[request_key(url, "http")] += 1

    def reset(self) -> None:
        self.counts.clear()

    def totals(self) -> Dict[str, int]:
        totals: Counter = Counter()
        for key, count in self.counts.items():
            totals[key.split(" ", 1)[0]] += count
        return dict(totals)


def load_posts(path: Path) -> List[dict]:
    """크롤이 남긴 JSON(`{"metadata", "posts"}`)이나 예전 목록 형식에서 글을 꺼낸다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    posts = data.get("posts", []) if isinstance(data, dict) else data
    return [post for post in posts if isinstance(post, dict)]


def summarize(posts: List[dict]) -> Dict[str, Any]:
    with_comments = [p for p in posts if (p.get("comments") or 0) > 0]
    statuses = Counter(p.get("content_status") or "-" for p in posts)
    return {
        "글": len(posts),
        "고유 id": len({p.get("external_id") or p.get("url") for p in posts}),
        "댓글 1개 이상": len(with_comments),
        "그중 댓글 섹션": sum(
            1
            for p in with_comments
            if COMMENT_SECTION.search(p.get("content_markdown") or "")
        ),
        "빈 본문": sum(1 for p in posts if not (p.get("content") or "").strip()),
        "content_status": ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())),
    }


def latest_json(directory: Path) -> Optional[Path]:
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    return files[-1] if files else None


def main_checkout() -> Path:
    """운영 데이터가 있는 기본 체크아웃. worktree에는 `data/`가 없다."""
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ROOT
    return Path(common).parent


def render(
    platform: str,
    baseline: Optional[Path],
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    elapsed: float,
    tally: RequestTally,
) -> str:
    lines = [f"\n[{platform}] 기준: {baseline or '없음'}"]
    rows = list((after or before or {}).keys())
    lines.append(f"  {'항목':<14} {'기준':>10}  {'이번':>10}")
    for row in rows:
        old = (before or {}).get(row, "-")
        new = (after or {}).get(row, "-")
        if row == "content_status":
            lines.append(f"  {row:<14} 기준 {old} / 이번 {new}")
        else:
            lines.append(f"  {row:<14} {old:>10}  {new:>10}")
    lines.append(f"  {'소요':<14} {'-':>10}  {elapsed:>9.1f}s")
    totals = tally.totals()
    summary = ", ".join(f"{kind} {count}" for kind, count in sorted(totals.items()))
    lines.append(f"  요청 합계: {summary or '없음'}")
    for key, count in tally.counts.most_common():
        lines.append(f"    {count:>5}  {key}")
    return "\n".join(lines)


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("platforms", nargs="+", choices=PLATFORMS)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="비교할 JSON. 플랫폼 하나일 때만. 없으면 운영 data/<플랫폼>/의 마지막 회차",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="결과를 저장할 작업공간. 없으면 임시 폴더를 새로 만든다",
    )
    args = parser.parse_args(argv)
    if args.baseline and len(args.platforms) > 1:
        parser.error("--baseline은 플랫폼 하나일 때만 쓸 수 있습니다")
    return args


def install_counters(tally: RequestTally) -> None:
    """전용 프로필 브라우저와 requests에 요청 세는 고리를 단다.

    크롤러 모듈은 `open_profile`을 이름으로 가져가므로 그 이름이 걸린 모듈마다 바꾼다.
    """
    # pylint: disable-next=import-outside-toplevel
    import requests

    # pylint: disable-next=import-outside-toplevel
    from skim_core.crawlers import browser_profile

    original = browser_profile.open_profile

    @contextlib.asynccontextmanager
    async def counting_open(*args, **kwargs):
        async with original(*args, **kwargs) as context:
            context.on("request", tally.browser)
            yield context

    for name, module in list(sys.modules.items()):
        if name.startswith(("skim_core", "skim_cli")):
            if getattr(module, "open_profile", None) is original:
                setattr(module, "open_profile", counting_open)

    send = requests.Session.request

    def counting_request(self, method, url, *args, **kwargs):
        tally.http(url)
        return send(self, method, url, *args, **kwargs)

    requests.Session.request = counting_request


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    production = main_checkout().resolve()
    workspace = (
        args.workspace.expanduser().resolve()
        if args.workspace
        else Path(tempfile.mkdtemp(prefix="skim-compare-"))
    )
    if workspace in (production, ROOT.resolve()):
        print("작업공간이 저장소 체크아웃입니다. 운영 DB에 쓰게 되므로 멈춥니다.")
        return 2
    (workspace / "data").mkdir(parents=True, exist_ok=True)
    # skim 모듈은 들여올 때 작업공간 경로를 정하므로 그 전에 바꾼다.
    os.environ["SKIM_WORKSPACE_ROOT"] = str(workspace)

    # pylint: disable-next=import-outside-toplevel
    from skim_cli.cli import app

    # pylint: disable-next=import-outside-toplevel
    from skim_core.crawlers.api.threads import request_query_name

    tally = RequestTally(request_query_name)
    install_counters(tally)
    print(f"작업공간: {workspace}")

    failed = False
    for platform in args.platforms:
        baseline = args.baseline or latest_json(production / "data" / platform)
        tally.reset()
        started = time.monotonic()
        try:
            app(
                ["crawl", platform, "--count", str(args.count)],
                standalone_mode=False,
            )
        except Exception as exc:  # noqa: BLE001 - 다음 플랫폼과 요청 집계는 이어서 보인다
            failed = True
            print(f"[{platform}] 크롤 실패: {exc}")
        elapsed = time.monotonic() - started
        produced = latest_json(workspace / "data" / platform)
        print(
            render(
                platform,
                baseline,
                summarize(load_posts(baseline)) if baseline else None,
                summarize(load_posts(produced)) if produced else None,
                elapsed,
                tally,
            )
        )
    print(f"\n결과 JSON과 DB는 {workspace} 에 남습니다. 확인한 뒤 지워도 됩니다.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
