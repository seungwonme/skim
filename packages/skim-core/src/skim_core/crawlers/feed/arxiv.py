"""
@file arxiv.py
@description arXiv cs.AI 논문 크롤러 (Atom API, 거절되면 RSS 공지 목록)
"""

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

import feedparser
import typer
from requests import RequestException

from ...enrichment import enrich_papers_with_content
from ...feed_config import (
    ARXIV_CATEGORIES,
    ARXIV_MAX_RESULTS_PER_CATEGORY,
    arxiv_api_url,
    arxiv_rss_url,
)
from ...feed_utils import (
    FEED_TIMEOUT_SECONDS,
    KST,
    RETRY_STATUSES,
    is_within_range,
    make_retrying_session,
)
from ...models import Post

# export.arxiv.org는 503을 흔하게 낸다. 재시도가 없으면 그날 그 카테고리 논문이
# 영영 안 들어온다 - 카테고리당 최신 50건만 받으므로 다음 날 창이 겹쳐도
# 그 사이 제출분까지 거슬러 가지 못한다.
#
# 2026-09 중순부터는 406도 섞어 돌려준다. 같은 회차 안에서 406 직후 다른 분야가
# 200으로 오기도 해서(9/26: cs.AI 406, 이어서 cs.CL 200) 짧은 재시도는 해 본다.
# 백오프는 arXiv 권고 간격(3초) 근처로 둔다.
_SESSION = make_retrying_session(
    retry_statuses=(406, *RETRY_STATUSES), backoff_factor=1.5
)

# 창이 한 장보다 넓을 때 이어서 받을 최대 페이지 수. 폭주 방지용 상한이다.
ARXIV_MAX_PAGES = 10
# arXiv API 권고 간격.
ARXIV_REQUEST_INTERVAL_SECONDS = 3

# RSS 공지 유형 중 API의 "최신 제출" 목록과 같은 뜻인 것만 쓴다. replace는 옛
# 논문의 새 버전이라 제출일 정렬 API에는 나오지 않는다.
_RSS_NEW_TYPES = frozenset({"new", "cross"})
_RSS_ABSTRACT_PREFIX = re.compile(
    r"^\s*arXiv:\S+\s+Announce Type:\s*\S+\s*Abstract:\s*", re.IGNORECASE
)
# RSS 엔트리 id는 `oai:arXiv.org:2609.31763v1`이다.
_RSS_PAPER_ID = re.compile(r"(\d{4})\.(\d{4,5})(v\d+)$")

_last_request = 0.0


def _wait_for_turn() -> None:
    """arXiv 요청 사이를 권고 간격만큼 띄운다.

    예전에는 같은 분야의 페이지 사이에만 기다렸다. 실패한 분야는 곧바로 다음 분야로
    넘어가서 거절이 이어지는 날에는 네 분야를 1초 안에 몰아쳤다.
    """
    global _last_request  # pylint: disable=global-statement
    waited = time.monotonic() - _last_request
    if waited < ARXIV_REQUEST_INTERVAL_SECONDS:
        time.sleep(ARXIV_REQUEST_INTERVAL_SECONDS - waited)
    _last_request = time.monotonic()


def _describe_refusal(resp) -> str:
    """거절 응답 한 줄. 다음 회차 로그에서 원인을 가릴 수 있게 헤더와 본문 앞부분을 남긴다.

    406의 사유가 로그에 없어서 9/24~9/29 여섯 회차 동안 원인을 추정만 했다.
    """
    body = re.sub(r"\s+", " ", resp.text or "").strip()
    return (
        f"HTTP {resp.status_code} server={resp.headers.get('server')} "
        f"type={resp.headers.get('content-type')} bytes={len(resp.content or b'')} "
        f"body={body[:120]!r}"
    )


def _parse_entry_dt(published: str):
    """엔트리 발행일을 aware datetime으로. 실패하면 None."""
    try:
        return datetime.fromisoformat((published or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _fetch_category(category: str, start: int = 0):
    """카테고리 피드를 받는다. 실패는 None으로 돌려 0건과 구분한다.

    feedparser에 URL을 직접 주면 실패해도 빈 피드를 조용히 돌려주기 때문에,
    4개 카테고리 중 하나가 죽어도 나머지가 정상 카운트를 만들어 0건 회귀 감지에
    걸리지 않는다.

    406 응답에 멀쩡한 Atom 피드가 실려 오는 경우가 다른 프로젝트에서 보고됐다
    (openags/paper-search-mcp#121). 엔트리가 있으면 상태 코드와 무관하게 쓴다.
    """
    _wait_for_turn()
    try:
        resp = _SESSION.get(
            arxiv_api_url(category, start=start), timeout=FEED_TIMEOUT_SECONDS
        )
    except RequestException as e:
        typer.echo(f"   [!] arXiv {category} 요청 실패: {e}")
        return None

    feed = feedparser.parse(resp.content)
    if resp.ok:
        return feed
    if feed.entries:
        typer.echo(
            f"   [!] arXiv {category}: HTTP {resp.status_code}지만 응답에 피드가 "
            f"있어 씁니다 ({len(feed.entries)}건)"
        )
        return feed
    typer.echo(f"   [!] arXiv {category} 요청 실패: {_describe_refusal(resp)}")
    return None


def _rss_paper_id(entry) -> Optional[str]:
    """`oai:arXiv.org:2609.31763v1` -> `2609.31763v1`.

    버전까지 붙여야 API가 주는 링크(`https://arxiv.org/abs/2609.31763v1`)와 같아진다.
    링크가 다르면 같은 논문이 두 행으로 갈라진다.
    """
    match = _RSS_PAPER_ID.search(entry.get("id") or "")
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2)}{match.group(3)}"


def _rss_sort_key(paper_id: str) -> Tuple[int, int]:
    head, _, tail = paper_id.partition(".")
    return int(head), int(tail.split("v")[0])


def _fetch_rss_entries(category: str, count: int) -> Optional[List[dict]]:
    """API가 거절한 분야를 RSS 공지 목록으로 받는다. 실패면 None, 공지가 없으면 [].

    RSS에는 제출 시각이 없고 발행일이 공지일(자정 ET) 하나라서, API처럼 최신
    제출부터 자르려면 arXiv id 역순으로 정렬한다. id는 제출 순서대로 붙는다.
    크롤 루프가 API 엔트리와 같은 키로 읽도록 dict로 옮긴다.
    """
    _wait_for_turn()
    try:
        resp = _SESSION.get(arxiv_rss_url(category), timeout=FEED_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except RequestException as e:
        typer.echo(f"   [!] arXiv {category} RSS 요청 실패: {e}")
        return None

    entries: List[dict] = []
    for entry in feedparser.parse(resp.content).entries:
        if entry.get("arxiv_announce_type") not in _RSS_NEW_TYPES:
            continue
        paper_id = _rss_paper_id(entry)
        if not paper_id:
            continue
        entries.append(
            {
                "title": entry.get("title", ""),
                "link": f"https://arxiv.org/abs/{paper_id}",
                "published": entry.get("published", ""),
                "authors": entry.get("authors", []),
                "summary": _RSS_ABSTRACT_PREFIX.sub("", entry.get("summary", "")),
                "arxiv_feed": "rss",
                "paper_id": paper_id,
            }
        )
    entries.sort(key=lambda e: _rss_sort_key(e["paper_id"]), reverse=True)
    return entries[:count]


class ArxivCrawler:
    platform = "arxiv"

    def _collect_api(self, category: str, count: int, since) -> Tuple[list, bool]:
        """카테고리 엔트리를 창이 끝날 때까지 페이지 단위로 모은다.

        한 장(50건)이 실측 4시간25분치라(2026-08-10, cs.AI) count를 그보다 크게
        잡으면 페이징 없이는 못 채운다. count가 한 장 안에 들어오면 요청은
        지금과 똑같이 1회다.

        두 번째 값은 API를 쓸 수 있었는지다. 첫 장부터 거절되면 False라서 호출자가
        RSS로 넘어간다. 뒤 장에서 끊긴 건 이미 받은 만큼 쓴다.
        """
        entries: list = []
        for page in range(ARXIV_MAX_PAGES):
            if len(entries) >= count:
                break
            feed = _fetch_category(
                category, start=page * ARXIV_MAX_RESULTS_PER_CATEGORY
            )
            if feed is None:
                return entries, page > 0
            if not feed.entries:
                break
            entries.extend(feed.entries)

            # 이 페이지의 마지막 항목이 창보다 오래됐으면 다음 페이지는 전부 창 밖이다.
            last_dt = _parse_entry_dt(feed.entries[-1].get("published", ""))
            if last_dt is not None and since is not None and last_dt < since:
                break
        return entries, True

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 50)
        since = options.get("since")
        no_content = options.get("no_content", False)

        if not since:
            since = (datetime.now(KST) - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        items: List[dict] = []
        seen_urls: set[str] = set()
        failed: List[str] = []
        for category in ARXIV_CATEGORIES:
            entries, api_ok = self._collect_api(category, count, since)
            if not api_ok:
                entries = _fetch_rss_entries(category, count)
                if entries is None:
                    failed.append(category)
                    continue
                typer.echo(
                    f"   -> arXiv {category}: API 대신 RSS 공지 목록 {len(entries)}건"
                )

            for entry in entries:
                pub = entry.get("published", "")
                try:
                    entry_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
                if not is_within_range(entry_dt, since):
                    continue

                url = entry.get("link", "")
                # 논문은 여러 카테고리에 교차 등록된다. 같은 abs 링크가 두 번 들어오면
                # enrichment도 두 번 돌고 정렬 뒤 상한만 잡아먹는다.
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
                items.append(
                    {
                        "platform": "arxiv",
                        "title": re.sub(r"\s+", " ", entry.get("title", "")).strip(),
                        "url": url,
                        "author": authors,
                        "published": entry_dt.astimezone(timezone.utc).isoformat(),
                        "summary": re.sub(
                            r"\s+", " ", entry.get("summary", "")
                        ).strip()[:500],
                        "abstract": re.sub(
                            r"\s+", " ", entry.get("summary", "")
                        ).strip(),
                        "arxiv_category": category,
                        "arxiv_feed": entry.get("arxiv_feed"),
                    }
                )

        # 빈 리스트로 끝내면 run에는 "0건"으로만 남아 주말의 정상 0건과 구분되지 않는다.
        # 9/24~9/29 여섯 회차가 그렇게 degraded로만 기록돼 실패로 드러나지 않았다.
        if len(failed) == len(ARXIV_CATEGORIES):
            raise RuntimeError(
                f"arXiv {len(failed)}개 분야 모두 API와 RSS 요청이 실패했습니다"
            )
        if failed:
            typer.echo(f"   [!] arXiv 일부 분야 수집 실패: {', '.join(failed)}")

        # RSS로 받은 항목은 발행일이 공지일(자정 ET)이라 API 항목(제출 시각)보다 앞에
        # 정렬된다. 폴백이 섞인 회차에서는 RSS 분야 비중이 커지지만, 새 논문이라는
        # 점은 같아서 그대로 둔다.
        items.sort(key=lambda x: x.get("published", ""), reverse=True)
        items = items[:count]

        if not no_content and items:
            enrich_papers_with_content(items)

        return [self._item_to_post(item) for item in items]

    def _item_to_post(self, item: dict) -> Post:
        extras = {
            key: value
            for key, value in item.items()
            if key
            in ("enrichment_method", "enrichment_error", "arxiv_category", "arxiv_feed")
            and value is not None
        }
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content="",
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
            **extras,
        )
