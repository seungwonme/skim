"""
@file geeknews.py
@description GeekNews 크롤러 (HTML scraping + RSS)
"""

import re
import time
from typing import Any, List, Optional

import requests
import typer
from bs4 import BeautifulSoup

from ...comments import Comment, append_comment_section, render_comment_section
from ...enrichment import enrich_with_content
from ...feed_config import GEEKNEWS_RSS
from ...feed_utils import FEED_HEADERS, fetch_feed
from ...models import Post
from ...timestamp import _REL_KO, relative_ko_to_iso

GEEKNEWS_URL = "https://news.hada.io/"
_TOPIC_ID = re.compile(r"topic\?id=(\d+)")
MAX_COMMENTS = 15

# news.hada.io는 토픽 페이지를 (IP, UA) 단위로 보고 요청이 몰리면 막는다. 다른
# 크롤러(reddit, lobsters)는 이미 초당 1요청으로 걸어두는데 여기만 없어서, 회차마다
# 50건을 간격 없이 몰아치고 있었다.
TOPIC_REQUEST_INTERVAL_SECONDS = 1.0

# 막힌 뒤에도 계속 두드리면 차단이 갱신돼 풀리지 않는다. 2026-08-29~09-22에 댓글과
# 지표가 3주간 통째로 빈 게 그 상태다: 매 회차 50건을 전부 403으로 맞으면서 매일
# 차단을 새로 걸었다. 반대로 쓰지 않으면 풀린다 (8-09에 막힌 UA가 9-22에 정상 응답).
# 그래서 막히면 그 회차의 남은 요청을 포기한다. 게시글 저장은 영향받지 않는다.
MAX_CONSECUTIVE_BLOCKS = 3

# 목록 한 장이 20건. RSS가 주는 50건을 세 장이면 덮는다. 필요한 id를 다 찾으면
# 남은 장은 받지 않으므로 이건 상한일 뿐이다.
METRICS_INDEX_MAX_PAGES = 3

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
# 봇 차단이므로 풀지 않고, 차단으로 인식해 물러난다.
_CHALLENGE_MARKER = "browser-check-turnstile"


def _is_blocked(resp: requests.Response) -> bool:
    """차단 응답인지 본다. 403, 200+"Forbidden", 200+브라우저 확인 세 가지다."""
    if resp.status_code == 403:
        return True
    if resp.status_code != 200:
        return False
    return resp.text.strip().startswith("Forbidden") or _CHALLENGE_MARKER in resp.text


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


def fetch_metrics_index(
    topic_ids: Optional[List[str]] = None, max_pages: int = METRICS_INDEX_MAX_PAGES
) -> dict:
    """`/newest` 목록에서 `{topic_id: {"likes":, "comments":}}`를 모은다.

    목록에는 토픽 페이지와 같은 마크업(`#tp{id}`, `data-topic-comment-count`)이 그대로
    있고 브라우저 확인도 걸리지 않는다. 글마다 토픽 페이지를 열던 때는 같은 값을
    받으려고 회차당 50건을 요청했고, 그 물량이 차단을 불렀다.
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
                    index[topic_id] = _row_metrics(soup, topic_id)
        except Exception as e:  # pylint: disable=broad-except
            typer.echo(f"   [!] GeekNews 목록 {page}쪽 수집 실패: {e}")
            break
        # 필요한 글을 다 찾았으면 남은 장은 받지 않는다.
        if wanted and wanted <= index.keys():
            break
        time.sleep(TOPIC_REQUEST_INTERVAL_SECONDS)
    return index


def fetch_geeknews_metrics(topic_id: str) -> Optional[dict]:
    """토픽 페이지에서 포인트, 댓글 수, 댓글 본문을 가져온다.

    RSS는 지표를 싣지 않아 `--days` 경로로 저장한 행은 likes/comments가 비어 있었다.
    홈페이지 스크래핑 경로만 지표를 채우던 비대칭을 없앤다.
    같은 응답에 댓글 본문도 들어 있으므로 `comment_section`으로 함께 돌려준다.
    """
    global _last_topic_request, _consecutive_blocks  # pylint: disable=global-statement
    if _consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS:
        return None

    waited = time.monotonic() - _last_topic_request
    if waited < TOPIC_REQUEST_INTERVAL_SECONDS:
        time.sleep(TOPIC_REQUEST_INTERVAL_SECONDS - waited)
    _last_topic_request = time.monotonic()

    # 파싱까지 try 안에 둔다. HTTP만 감싸면 news.hada.io의 마크업이 바뀔 때
    # 셀렉터 예외가 crawl 루프로 올라가 그 회차 GeekNews가 통째로 저장 0건이 된다.
    # 지표까지 함께 잃지만 HTTP 실패 경로와 동작이 같고, 플랫폼 전량 유실보다 낫다.
    try:
        resp = requests.get(
            f"{GEEKNEWS_URL}topic?id={topic_id}",
            headers=FEED_HEADERS,
            timeout=10,
        )
        if _is_blocked(resp):
            _consecutive_blocks += 1
            if _consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS:
                typer.echo(
                    "   [!] GeekNews가 요청을 막았습니다. 이 회차의 남은 지표·댓글 "
                    "수집을 건너뜁니다 (계속 두드리면 차단이 길어집니다)."
                )
            return None
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # 포인트는 `<span id='tp{id}'>3</span>P` 형태로만 노출된다.
        point_el = soup.select_one(f"#tp{topic_id}")
        likes = _digits_to_int(point_el.get_text(strip=True)) if point_el else None

        # 댓글 수는 링크 텍스트("댓글 3개")보다 data 속성이 안정적이다.
        comment_el = soup.select_one("[data-topic-comment-count]")
        comments = (
            _digits_to_int(comment_el.get("data-topic-comment-count"))
            if comment_el
            else None
        )

        comment_section = _parse_comment_section(soup)
    except Exception as e:  # pylint: disable=broad-except
        typer.echo(f"   [!] GeekNews 지표 수집 실패(id={topic_id}): {e}")
        return None

    _consecutive_blocks = 0
    if likes is None and comments is None and comment_section is None:
        return None
    return {"likes": likes, "comments": comments, "comment_section": comment_section}


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
                enrich_with_content(items)
                # 지표는 목록에서 20건씩 받는다. 예전에는 글마다 토픽 페이지를 열어
                # 회차당 50건을 요청했고, 그 물량이 차단을 불렀다.
                index = fetch_metrics_index(
                    [topic_id_from_url(i.get("url", "")) for i in items]
                )
                for item in items:
                    metrics = index.get(topic_id_from_url(item.get("url", "")))
                    if metrics:
                        item["likes"] = metrics["likes"]
                        item["comments"] = metrics["comments"]

                # 댓글 본문만 토픽 페이지를 봐야 한다. 댓글이 없는 글까지 열면 얻는 것
                # 없이 요청이 두 배가 된다. 신선한 UA도 33건쯤에서 브라우저 확인이
                # 걸려(2026-09-22 실측) 회차 예산이 댓글 있는 글 수보다 적을 수 있으므로,
                # 토론이 많은 글부터 받아 모자랄 때 덜 아쉬운 쪽을 잃게 한다.
                for item in sorted(
                    (i for i in items if i.get("comments")),
                    key=lambda i: i["comments"],
                    reverse=True,
                ):
                    detail = fetch_geeknews_metrics(
                        topic_id_from_url(item.get("url", ""))
                    )
                    item["content_markdown"] = append_comment_section(
                        item.get("content_markdown"),
                        detail.get("comment_section") if detail else None,
                    )
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
