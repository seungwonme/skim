"""
@file geeknews.py
@description GeekNews 크롤러 (RSS + /newest 목록 + 토픽 페이지)

news.hada.io로 가는 요청은 이 모듈에만 둔다. 토픽 페이지는 요청량으로 막히는
자리라, 간격과 서킷브레이커를 한 곳에서 지켜야 한다. 예전에는 본문 추출이
enrichment.py에서 같은 토픽 페이지를 글마다 두 번씩 간격 없이 열어서, 매 회차
24요청쯤에서 막히고 나머지 글이 RSS 요약 조각으로 저장됐다 (2026-09, #29).
"""

import json
import re
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import requests
import typer
from bs4 import BeautifulSoup

from ...comments import Comment, append_comment_section, render_comment_section
from ...enrichment import (
    _is_content_usable,
    _pdf_fallback,
    defuddle,
    extract_article_content,
)
from ...feed_config import GEEKNEWS_RSS
from ...feed_utils import CHALLENGE_MARKER, FEED_HEADERS, fetch_feed
from ...models import Post
from ...paths import DATA_DIR
from ...timestamp import _REL_KO, relative_ko_to_iso

GEEKNEWS_URL = "https://news.hada.io/"
_TOPIC_ID = re.compile(r"topic\?id=(\d+)")
MAX_COMMENTS = 15

# news.hada.io는 토픽 페이지를 (IP, UA) 단위 요청 수로 막는다. 간격은 상관없다:
# 1초 간격 33건(2026-09-22), 3초 간격 31건(2026-09-29)에서 똑같이 막혔다. 3초 간격으로
# 360건을 버틴 2026-08 기록은 브라우저 확인이 생기기 전 값이다. 9월 말 데일리도
# 매일 밤 24요청 안팎에서 막혔다. 막힌 뒤 풀리는 시간은 들쭉날쭉하다: 2026-09-29
# 00:14에 막힌 것은 10분 안에 풀렸고, 같은 날 21:24에 막힌 것은 다음 날 00:33에도
# 막혀 있었다 (#33). 그래서 요청 수를 하루(20시간 창) 한도 아래로 둔다. 창을 24시간보다
# 조금 짧게 잡아 매일 밤 크롤이 새 한도로 시작한다. 간격은 한 번에 몰리지 않게 하는 정도다.
TOPIC_REQUEST_INTERVAL_SECONDS = 3.0
TOPIC_BUDGET = 25
TOPIC_BUDGET_WINDOW_SECONDS = 20 * 3600
# 크롤, 백필, 수동 실행은 다른 프로세스라 한도를 파일로 나눠 쓴다. 최근 요청 시각만 담는다.
TOPIC_BUDGET_FILE = DATA_DIR / "geeknews_topic_budget.json"

# 막힌 뒤에도 계속 두드리면 차단이 갱신돼 풀리지 않는다. 2026-08-29~09-22에 댓글과
# 지표가 3주간 통째로 빈 게 그 상태다: 매 회차 50건을 전부 403으로 맞으면서 매일
# 차단을 새로 걸었다. 반대로 쓰지 않으면 풀린다 (8-09에 막힌 UA가 9-22에 정상 응답).
# 그래서 한 번 막히면 남은 요청을 포기하고, 한도 파일도 다 쓴 것으로 적어 다른
# 프로세스도 창이 지날 때까지 요청하지 않게 한다. 게시글 저장은 영향받지 않는다.
MAX_CONSECUTIVE_BLOCKS = 1

# 목록 한 장이 20건. RSS가 주는 50건을 세 장이면 덮는다. 필요한 id를 다 찾으면
# 남은 장은 받지 않으므로 이건 상한일 뿐이다.
METRICS_INDEX_MAX_PAGES = 3

# 목록(`/newest`)은 토픽 페이지와 달리 브라우저 확인이 걸린 적이 없다. 간격은
# 토픽 요청과 따로 둔다.
LISTING_REQUEST_INTERVAL_SECONDS = 3.0

# 홈(`/`)은 인기순이라 최근 글을 빠뜨린다(실측: RSS 50건 중 23건만 겹쳤다).
# `/newest`는 시간순이라 RSS 창과 그대로 맞는다.
GEEKNEWS_NEWEST_URL = f"{GEEKNEWS_URL}newest"

_last_topic_request = 0.0
_consecutive_blocks = 0


def reset_topic_throttle() -> None:
    """프로세스 안에서 차단 상태를 지운다 (테스트와 재시도용)."""
    global _last_topic_request, _consecutive_blocks  # pylint: disable=global-statement
    _last_topic_request = 0.0
    _consecutive_blocks = 0


# 토픽 페이지는 2026-09-22 확인 시점에 Cloudflare Turnstile "브라우저 확인"을 태운다.
# 이 페이지는 200에 정상 HTML로 오기 때문에 상태 코드로는 성공과 구분되지 않고,
# 셀렉터가 전부 None을 돌려줘 빈 지표가 조용히 저장된다. 사이트가 의도해서 건
# 봇 차단이므로 풀지 않고, 차단으로 인식해 물러난다 (표지는 feed_utils.CHALLENGE_MARKER).


def _block_kind(resp: requests.Response) -> Optional[str]:
    """차단 응답의 종류. 403, 200+"Forbidden", 200+브라우저 확인 세 가지이고 아니면 None.

    종류를 로그에 남긴다. 10분 만에 풀린 차단과 3시간 넘게 간 차단(2026-09-29)이
    어느 종류였는지 지금 로그로는 가릴 수 없다 (#33).
    """
    if resp.status_code == 403:
        return "403"
    if resp.status_code != 200:
        return None
    if CHALLENGE_MARKER in resp.text:
        return "브라우저 확인"
    if resp.text.strip().startswith("Forbidden"):
        return "Forbidden"
    return None


def _is_blocked(resp: requests.Response) -> bool:
    """차단 응답인지 본다."""
    return _block_kind(resp) is not None


def topic_blocked() -> bool:
    """이 프로세스에서 서킷브레이커가 열렸는지. 열리면 남은 토픽 요청을 보내지 않는다."""
    return _consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS


def _load_topic_budget() -> dict:
    """한도 파일. `{"requests": [요청 시각...], "blocked_at": 막힌 시각 또는 null}`."""
    try:
        state = json.loads(TOPIC_BUDGET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _within_window(stamp: Any, now: float) -> bool:
    return (
        isinstance(stamp, (int, float))
        and 0 <= now - stamp < TOPIC_BUDGET_WINDOW_SECONDS
    )


def _recent_topic_requests(state: dict, now: float) -> List[float]:
    stamps = state.get("requests")
    if not isinstance(stamps, list):
        return []
    return [t for t in stamps if _within_window(t, now)]


def last_topic_block(now: Optional[float] = None) -> Optional[float]:
    """창 안에서 토픽 페이지가 막힌 시각. 없으면 None. doctor가 요청 없이 읽는다."""
    now = time.time() if now is None else now
    blocked_at = _load_topic_budget().get("blocked_at")
    return blocked_at if _within_window(blocked_at, now) else None


def topic_budget_left(now: Optional[float] = None) -> int:
    """창(`TOPIC_BUDGET_WINDOW_SECONDS`) 안에서 이 작업공간이 쓰고 남은 토픽 요청 수.

    창 안에서 한 번이라도 막혔으면 0이다.
    """
    now = time.time() if now is None else now
    state = _load_topic_budget()
    if _within_window(state.get("blocked_at"), now):
        return 0
    return max(0, TOPIC_BUDGET - len(_recent_topic_requests(state, now)))


def _clock(stamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


def topic_pause_reason(now: Optional[float] = None) -> str:
    """토픽 요청을 보내지 않는 이유와 다시 보내는 시각. 로그에서 차단과 한도 소진을 가른다.

    2026-09-30 데일리의 백필은 차단 때문에 멈췄는데 "한도를 다 썼습니다"라고 찍었다.
    """
    now = time.time() if now is None else now
    state = _load_topic_budget()
    blocked_at = state.get("blocked_at")
    if _within_window(blocked_at, now):
        return (
            f"토픽 페이지가 {_clock(blocked_at)}에 막혀 "
            f"{_clock(blocked_at + TOPIC_BUDGET_WINDOW_SECONDS)}까지 요청하지 않습니다."
        )
    recent = _recent_topic_requests(state, now)
    if len(recent) >= TOPIC_BUDGET:
        return (
            f"토픽 요청 한도({TOPIC_BUDGET}건)를 다 썼습니다. "
            f"{_clock(min(recent) + TOPIC_BUDGET_WINDOW_SECONDS)}부터 다시 요청합니다."
        )
    return "이 실행에서는 토픽 요청을 멈췄습니다."


def _spend_topic_budget(now: float, blocked: bool = False) -> None:
    """요청 한 건을 적는다. blocked면 요청은 보내기 전에 이미 적었으므로 차단 시각만 적는다."""
    state = _load_topic_budget()
    blocked_at = now if blocked else state.get("blocked_at")
    record = {
        "requests": _recent_topic_requests(state, now) + ([] if blocked else [now]),
        "blocked_at": blocked_at if _within_window(blocked_at, now) else None,
    }
    try:
        TOPIC_BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOPIC_BUDGET_FILE.write_text(json.dumps(record), encoding="utf-8")
    except OSError as e:
        typer.echo(f"   [!] GeekNews 토픽 한도 기록 실패: {e}")


def topic_id_from_url(url: str) -> Optional[str]:
    """GeekNews 토픽 URL에서 숫자 id를 뽑는다."""
    match = _TOPIC_ID.search(url or "")
    return match.group(1) if match else None


def _digits_to_int(raw: str) -> Optional[int]:
    digits = "".join(c for c in raw or "" if c.isdigit())
    return int(digits) if digits else None


def _parse_comment_section(soup: BeautifulSoup) -> Optional[str]:
    """토픽 페이지의 댓글 목록을 본문용 마크다운 섹션으로 만든다.

    지표를 받으려고 어차피 토픽 HTML을 통째로 받으므로 추가 요청이 들지 않는다.
    GeekNews는 댓글 점수를 노출하지 않아 작성자와 작성시각까지가 가용 메타데이터다.
    """
    collected: List[Comment] = []
    for row in soup.select(".comment_row"):
        body_el = row.select_one(".comment_contents")
        if not body_el:
            continue
        text = body_el.get_text(" ", strip=True)
        if not text:
            continue

        author_el = row.select_one('.commentinfo a[href^="/@"]')
        author = author_el.get_text(strip=True) if author_el else "unknown"

        # 상대시각("14시간전")보다 title의 절대시각이 안정적이다.
        time_el = row.select_one(".commentinfo time")
        created = time_el.get("title") if time_el else None

        # 들여쓰기는 `style="--depth:N"`으로만 노출된다.
        depth = 0
        depth_match = re.search(r"--depth:\s*(\d+)", row.get("style") or "")
        if depth_match:
            depth = min(int(depth_match.group(1)), 3)

        collected.append(
            Comment(author=author, text=text, created=created, depth=depth)
        )

    return render_comment_section(
        "GeekNews Comments", collected, max_comments=MAX_COMMENTS
    )


def _row_metrics(soup: BeautifulSoup, topic_id: str) -> dict:
    """목록/토픽 공통 마크업에서 포인트와 댓글 수를 읽는다."""
    # 포인트는 `<span id='tp{id}'>3</span>P` 형태로만 노출된다.
    point_el = soup.select_one(f"#tp{topic_id}")
    comment_el = soup.select_one(f"[data-topic-comment-topic-id='{topic_id}']")
    return {
        "likes": _digits_to_int(point_el.get_text(strip=True)) if point_el else None,
        "comments": (
            _digits_to_int(comment_el.get("data-topic-comment-count"))
            if comment_el
            else None
        ),
    }


def _original_url_from(anchor) -> Optional[str]:
    """제목 링크가 외부 원문이면 그 URL. Show GN, Ask GN 같은 자체 글은 토픽 경로라 None."""
    if anchor is None:
        return None
    href = (anchor.get("href") or "").strip()
    if href.startswith("http") and "news.hada.io" not in href:
        return href
    return None


def fetch_listing_index(
    topic_ids: Optional[List[str]] = None, max_pages: int = METRICS_INDEX_MAX_PAGES
) -> dict:
    """`/newest` 목록에서 `{topic_id: {"likes", "comments", "original_url"}}`를 모은다.

    목록에는 토픽 페이지와 같은 마크업(`#tp{id}`, `data-topic-comment-count`)이 그대로
    있고 브라우저 확인도 걸리지 않는다. 글마다 토픽 페이지를 열던 때는 같은 값을
    받으려고 회차당 50건을 요청했고, 그 물량이 차단을 불렀다.

    원문 링크도 여기 있다(제목 앵커). 그래서 원문 추출에는 토픽 페이지가 필요 없고,
    토픽 페이지가 막힌 날에도 원문 본문은 원래 사이트에서 받는다.
    """
    wanted = set(topic_ids or [])
    index: dict = {}
    for page in range(1, max_pages + 1):
        url = GEEKNEWS_NEWEST_URL if page == 1 else f"{GEEKNEWS_NEWEST_URL}?page={page}"
        try:
            resp = requests.get(url, headers=FEED_HEADERS, timeout=10)
            if _is_blocked(resp):
                typer.echo(f"   [!] GeekNews 목록 {page}쪽이 막혔습니다.")
                break
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for row in soup.select(".topic_row"):
                topic_id = row.get("data-topic-state-id")
                if topic_id:
                    index[topic_id] = {
                        **_row_metrics(soup, topic_id),
                        "original_url": _original_url_from(
                            row.select_one(".topictitle a")
                        ),
                    }
        except Exception as e:  # pylint: disable=broad-except
            typer.echo(f"   [!] GeekNews 목록 {page}쪽 수집 실패: {e}")
            break
        # 필요한 글을 다 찾았으면 남은 장은 받지 않는다.
        if wanted and wanted <= index.keys():
            break
        time.sleep(LISTING_REQUEST_INTERVAL_SECONDS)
    return index


@dataclass
class TopicPage:
    """토픽 페이지 한 장에서 얻는 것 전부. 글당 요청을 1건으로 묶으려고 한 번에 읽는다."""

    summary: Optional[str]
    original_url: Optional[str]
    likes: Optional[int]
    comments: Optional[int]
    comment_section: Optional[str]


def _topic_summary(soup: BeautifulSoup) -> Optional[str]:
    """큐레이터 요약(.topic_contents)을 마크다운으로. Show GN이면 작성자 본문이다."""
    node = soup.select_one(".topic_contents")
    if not node:
        return None
    lines = []
    for el in node.find_all(["p", "li", "h1", "h2", "h3", "blockquote"]):
        text = el.get_text(" ", strip=True)
        if text:
            lines.append(f"- {text}" if el.name == "li" else text)
    body = "\n".join(dict.fromkeys(lines)) if lines else node.get_text(" ", strip=True)
    return body.strip() or None


def parse_topic_page(html: str, topic_id: str) -> TopicPage:
    """토픽 HTML에서 요약, 원문 링크, 지표, 댓글을 함께 읽는다."""
    soup = BeautifulSoup(html, "html.parser")
    # 포인트는 `<span id='tp{id}'>3</span>P` 형태로만 노출된다.
    point_el = soup.select_one(f"#tp{topic_id}")
    # 댓글 수는 링크 텍스트("댓글 3개")보다 data 속성이 안정적이다.
    comment_el = soup.select_one("[data-topic-comment-count]")
    return TopicPage(
        summary=_topic_summary(soup),
        original_url=_original_url_from(soup.select_one(".topictitle a")),
        likes=_digits_to_int(point_el.get_text(strip=True)) if point_el else None,
        comments=(
            _digits_to_int(comment_el.get("data-topic-comment-count"))
            if comment_el
            else None
        ),
        comment_section=_parse_comment_section(soup),
    )


def fetch_topic(topic_id: Optional[str]) -> Tuple[Optional[TopicPage], str]:
    """토픽 페이지를 한 번 받는다. `(page, outcome)`을 돌려준다.

    outcome은 ok, skipped(id 없음, 서킷브레이커 열림), budget(한도 소진, 요청 안 함),
    blocked, gone(404, 410), empty(200인데 아무것도 못 읽음), error(네트워크, 5xx,
    파싱 예외) 중 하나다. 글 탓인 실패는 gone과 empty뿐이다. 백필은 이 둘만 글에
    기록해, 삭제된 글을 매일 밤 다시 두드리다 멈추는 일을 막는다.

    요청 수는 `TOPIC_BUDGET`(20시간 창)을 넘기지 않는다. 막히면 이 프로세스의 남은
    토픽 요청을 건너뛰고 한도도 다 쓴 것으로 적는다. 막힌 뒤에 두드리면 차단이 길어진다.
    """
    global _last_topic_request, _consecutive_blocks  # pylint: disable=global-statement
    if not topic_id or topic_blocked():
        return None, "skipped"
    if topic_budget_left() <= 0:
        return None, "budget"

    waited = time.monotonic() - _last_topic_request
    if waited < TOPIC_REQUEST_INTERVAL_SECONDS:
        time.sleep(TOPIC_REQUEST_INTERVAL_SECONDS - waited)
    _last_topic_request = time.monotonic()
    # 막힌 요청도 한도를 쓴다. 보내기 전에 적어야 예외가 나도 빠지지 않는다.
    _spend_topic_budget(time.time())

    # 파싱까지 try 안에 둔다. HTTP만 감싸면 news.hada.io의 마크업이 바뀔 때
    # 셀렉터 예외가 crawl 루프로 올라가 그 회차 GeekNews가 통째로 저장 0건이 된다.
    try:
        resp = requests.get(
            f"{GEEKNEWS_URL}topic?id={topic_id}",
            headers=FEED_HEADERS,
            timeout=10,
        )
        kind = _block_kind(resp)
        if kind:
            _consecutive_blocks += 1
            _spend_topic_budget(time.time(), blocked=True)
            typer.echo(
                f"   [!] GeekNews가 요청을 막았습니다({kind}). {topic_pause_reason()}"
            )
            return None, "blocked"
        # 지워진 글은 404("아마도 글이 지워진거 같습니다!")로 온다 (2026-09-29 확인).
        if resp.status_code in (404, 410):
            _consecutive_blocks = 0
            typer.echo(f"   [!] GeekNews 토픽이 없습니다(id={topic_id}, 삭제된 글).")
            return None, "gone"
        resp.raise_for_status()
        page = parse_topic_page(resp.text, topic_id)
    except Exception as e:  # pylint: disable=broad-except
        typer.echo(f"   [!] GeekNews 토픽 수집 실패(id={topic_id}): {e}")
        return None, "error"

    _consecutive_blocks = 0
    # 마크업이 바뀌어 아무것도 못 읽었으면 성공으로 치지 않는다. 빈 값이 조용히 저장된다.
    if page.summary is None and page.likes is None and page.comments is None:
        return None, "empty"
    return page, "ok"


def fetch_topic_page(topic_id: Optional[str]) -> Optional[TopicPage]:
    """토픽 페이지를 받는다. 못 받으면 이유와 상관없이 None."""
    return fetch_topic(topic_id)[0]


def fetch_geeknews_metrics(topic_id: str) -> Optional[dict]:
    """토픽 페이지의 포인트, 댓글 수, 댓글 본문. `backfill_feed_metrics.py`가 쓴다."""
    page = fetch_topic_page(topic_id)
    if page is None:
        return None
    return {
        "likes": page.likes,
        "comments": page.comments,
        "comment_section": page.comment_section,
    }


# 본문은 `GN 요약 --- ## Original Article 원문 --- ## GeekNews Comments 댓글` 순서다.
ORIGINAL_HEADER = "## Original Article"
COMMENT_HEADER = "## GeekNews Comments"


def saved_original_from(body: Optional[str]) -> Optional[str]:
    """본문에 이미 붙어 있는 원문 섹션. 뒤에 붙은 댓글 섹션은 뺀다."""
    if not body or ORIGINAL_HEADER not in body:
        return None
    original = body.split(ORIGINAL_HEADER, 1)[1]
    original = original.split(f"\n\n---\n\n{COMMENT_HEADER}", 1)[0]
    return original.strip() or None


def extract_original(url: str, title: str) -> tuple:
    """원문 본문을 추출한다. (data, method, error). 쓸 만한 본문이 없으면 data는 None.

    defuddle을 먼저 보고, 모자라면 3단 사다리(HTTP, 렌더, defuddle)로 넘어간다.
    사다리는 실패해도 얇은 결과를 돌려주므로 공통 품질 게이트를 한 번 더 거친다.
    차단 화면 문구가 원문으로 붙는 걸 막는다.

    링크가 PDF면 HTML 추출기는 늘 실패하므로 마지막에 PDF 추출을 본다. hackernews와
    lobsters가 타는 `_pdf_fallback`인데 이 경로에만 빠져 있어서, 2026-07 이후 원문이
    PDF인 GeekNews 글 6건 중 5건이 failed로 남았다.
    """
    data = defuddle(url)
    if _is_content_usable(data, title, min_words=3):
        return data, "defuddle", None
    data, method, error = extract_article_content(url, title)
    if _is_content_usable(data, title, min_words=3):
        return data, method, error
    pdf = _pdf_fallback(url)
    if pdf:
        return pdf, "pdf", None
    return None, "failed", error or "content not usable"


def enrich_geeknews_item(
    item: dict, topic: Optional[TopicPage], saved_original: Optional[str] = None
) -> None:
    """GeekNews 한 건의 본문을 만든다. 순서는 GN 요약, 원문, 댓글이다.

    원문 링크는 목록에서 먼저 온다(`item["original_url"]`). 토픽 페이지가 막혀도
    원문은 원래 사이트에서 받으므로 news.hada.io로 요청이 더 가지 않는다.

    토픽 페이지를 못 받은 글은 GN 요약 대신 RSS 요약 조각이 들어가고 댓글이 빠진다.
    `content_status="partial"`로 표시해 `scripts/backfill_geeknews_topics.py`가 채운다.
    본문이 요약 조각뿐이면 `enrichment_method="failed"`도 남긴다. save_posts는 이
    마커가 있는 행만 다음 크롤에서 덮어쓴다.

    `saved_original`은 이미 받아 둔 원문 본문이다. 주면 원문을 다시 추출하지 않는다.
    백필이 partial 행을 채울 때 쓴다. 다시 추출하다 실패하면 있던 원문이 사라진다.
    """
    title = item.get("title", "")
    if topic is not None:
        item["geeknews_topic"] = "ok"
        item.pop("content_status", None)
        if not item.get("original_url") and topic.original_url:
            item["original_url"] = topic.original_url
        if item.get("likes") is None:
            item["likes"] = topic.likes
        if item.get("comments") is None:
            item["comments"] = topic.comments
    else:
        item["content_status"] = "partial"

    original_md = (saved_original or "").strip()
    original_url = item.get("original_url")
    if original_url and not original_md:
        print(f"    -> 원문: {original_url[:60]}")
        data, method, error = extract_original(original_url, title)
        item["enrichment_method"] = method
        if error:
            item["enrichment_error"] = error
        else:
            item.pop("enrichment_error", None)
        if data:
            original_md = (data.get("content_markdown") or "").strip()
            item["description"] = data.get("description", "")
            item["image"] = data.get("image", "")

    summary = (topic.summary if topic is not None else None) or ""
    fragment_only = not summary and not original_md
    if not summary:
        # GN 요약을 못 받았으면 RSS 요약 조각을 최저선으로 둔다. 없으면 본문이 통째로 빈다.
        summary = (item.get("summary") or "").strip()

    body = summary
    if original_md:
        body = (
            f"{summary}\n\n---\n\n{ORIGINAL_HEADER}\n\n{original_md}"
            if summary
            else original_md
        )
    body = append_comment_section(
        body, topic.comment_section if topic is not None else None
    )

    # 요약이 제목을 반복할 뿐이면 채울 정보가 없는 항목이다. 공통 게이트로 걷어낸다.
    if not _is_content_usable({"content_markdown": body}, title, min_words=3):
        body = ""
    if fragment_only or not body:
        item["enrichment_method"] = "failed"
    item["content_markdown"] = body
    item["word_count"] = len(body.split())


def _topic_priority(item: dict) -> tuple:
    """토픽 요청이 모자랄 때 먼저 받을 글. 토픽 페이지에만 있는 것이 많은 순서다.

    원문 링크가 없는 자체 글(Show GN, Ask GN)은 토픽 페이지가 본문 전부이고, 댓글은
    토픽 페이지에만 있다. 나머지 글도 GN 요약을 받지만, 원문은 목록 링크로 이미 받는다.
    """
    return (bool(item.get("original_url")), -(item.get("comments") or 0))


def enrich_geeknews_items(items: List[dict]) -> List[dict]:
    """글마다 토픽 페이지를 한 번만 열어 본문을 만든다. 순서는 `_topic_priority`다."""
    if not items:
        return items
    print(f"\n[콘텐츠] {len(items)}개 GeekNews 글의 본문을 만듭니다...")
    out_of_budget = False
    for n, item in enumerate(sorted(items, key=_topic_priority), 1):
        print(f"  [{n}/{len(items)}] {(item.get('title') or '')[:50]}...")
        topic, outcome = fetch_topic(topic_id_from_url(item.get("url", "")))
        if outcome == "budget" and not out_of_budget:
            out_of_budget = True
            print(
                f"   [!] {topic_pause_reason()} 남은 글은 원문만 붙여 partial로 저장합니다."
            )
        enrich_geeknews_item(item, topic)
    partial = sum(1 for it in items if it.get("content_status") == "partial")
    print(
        f"  -> 토픽 페이지 {len(items) - partial}/{len(items)}건"
        + (f", {partial}건은 partial로 저장 (다음 백필이 채운다)" if partial else "")
    )
    return items


class GeekNewsCrawler:
    platform = "geeknews"

    async def crawl(self, **options: Any) -> List[Post]:
        count = options.get("count", 30)
        since = options.get("since")
        no_content = options.get("no_content", False)

        if since:
            items = fetch_feed(GEEKNEWS_RSS, "geeknews", since)
            items.sort(key=lambda x: x.get("published", ""), reverse=True)
            # CLI가 마지막에 posts[:count]로 자르므로, 버려질 항목을 enrichment하지 않는다.
            if options.get("count") is not None:
                items = items[: options["count"]]
            if not no_content:
                # 목록을 먼저 본다. 지표와 원문 링크가 있고 브라우저 확인이 걸리지 않는다.
                index = fetch_listing_index(
                    [topic_id_from_url(i.get("url", "")) for i in items]
                )
                for item in items:
                    entry = index.get(topic_id_from_url(item.get("url", "")))
                    if entry:
                        item["likes"] = entry["likes"]
                        item["comments"] = entry["comments"]
                        if entry.get("original_url"):
                            item["original_url"] = entry["original_url"]
                # GN 요약과 댓글만 토픽 페이지에 있다. 글당 한 번만 연다.
                enrich_geeknews_items(items)
            return [self._item_to_post(item) for item in items]
        else:
            return self._scrape_homepage(count)

    def _scrape_homepage(self, count: int) -> List[Post]:
        """GeekNews 메인 페이지에서 게시글을 HTML 스크래핑합니다."""
        typer.echo(f"GeekNews에서 상위 {count}개 게시글을 가져옵니다...")

        resp = requests.get(
            GEEKNEWS_URL,
            headers=FEED_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        topic_rows = soup.select(".topic_row")

        posts: List[Post] = []
        for i, row in enumerate(topic_rows[:count]):
            try:
                # 제목 + 링크
                title_el = row.select_one(".topictitle a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                url = title_el.get("href", "")
                if url and not url.startswith("http"):
                    url = f"https://news.hada.io{url}"

                # 요약 텍스트
                desc_el = row.select_one(".topicdesc a")
                description = (
                    desc_el.get_text(strip=True).lstrip("- ") if desc_el else ""
                )

                # 메타 정보 (.topicinfo: 포인트, 작성자, 시간, 댓글)
                topicinfo = row.select_one(".topicinfo")
                author = ""
                timestamp = ""
                likes = 0
                comments = 0

                if topicinfo:
                    # 포인트
                    point_el = topicinfo.select_one("span")
                    if point_el:
                        try:
                            likes = int(point_el.get_text(strip=True))
                        except ValueError:
                            pass

                    # 작성자
                    user_link = topicinfo.select_one('a[href*="/user?"]')
                    if user_link:
                        author = user_link.get_text(strip=True)

                    # 시간: topicinfo의 직접 텍스트에서 추출 → UTC ISO 8601 정규화
                    info_text = topicinfo.get_text(" ", strip=True)

                    rel_match = _REL_KO.search(info_text)
                    if rel_match:
                        timestamp = relative_ko_to_iso(rel_match.group(0)) or ""

                    # 댓글
                    comment_link = topicinfo.select_one('a[href*="go=comments"]')
                    if comment_link:
                        comment_text = comment_link.get_text(strip=True)
                        digits = "".join(c for c in comment_text if c.isdigit())
                        comments = int(digits) if digits else 0

                # content: 제목 + 요약
                content = title if not description else f"{title}\n{description}"

                post = Post(
                    platform="geeknews",
                    author=author or "unknown",
                    content=content,
                    timestamp=timestamp,
                    url=url,
                    likes=likes,
                    comments=comments,
                )
                posts.append(post)
                typer.echo(f"   [{i + 1}/{count}] {title[:60]}")
            except Exception as e:
                typer.echo(f"   [{i + 1}/{count}] 스킵 (에러: {e})")

        return posts

    def _item_to_post(self, item: dict) -> Post:
        extras = {
            key: value
            for key, value in item.items()
            if key
            in (
                "original_url",
                "enrichment_method",
                "enrichment_error",
                "image",
                "description",
                "content_status",
                "geeknews_topic",
            )
            and value
        }
        return Post(
            platform=item.get("platform", self.platform),
            author=item.get("author", ""),
            title=item.get("title", ""),
            content="",
            timestamp=item.get("published", ""),
            url=item.get("url", ""),
            likes=item.get("likes"),
            comments=item.get("comments"),
            summary=item.get("summary", ""),
            content_markdown=item.get("content_markdown"),
            word_count=item.get("word_count"),
            **extras,
        )
