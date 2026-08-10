"""
@file feed_config.py
@description 일일 RSS 피드 수집 설정
"""

# Hacker News - hnrss.org (공식보다 풍부한 데이터, 필터링 지원)
# count 기본값이 20이라 하루 창(--days 1)에 30점 넘긴 글의 일부만 들어왔다
# (2026-08-10 실측: 24시간에 65건인데 20건만 수집). 문서상 최대인 100으로 올린다.
HACKERNEWS_FEED_COUNT = 100
HACKERNEWS_MIN_POINTS = 30
HACKERNEWS_RSS = (
    f"https://hnrss.org/newest?points={HACKERNEWS_MIN_POINTS}"
    f"&count={HACKERNEWS_FEED_COUNT}"
)

# Show/Ask HN은 점수 문턱을 두지 않는다. 링크가 아니라 본문이 알맹이인 글이라
# 30점 필터에 걸리면 대부분 사라진다. 크롤러는 이미 Ask/Show 본문 처리를 갖췄다.
#
# 대신 개수는 newest보다 낮게 잡는다. 문턱이 없어 하루치를 다 받으면 저품질까지
# 통째로 들어오고, 그 전부가 원문 추출과 댓글 조회를 돈다 (실측: 셋 다 100으로
# 두면 한 회차 239건, 종전 60건의 4배).
HACKERNEWS_SHOW_ASK_COUNT = 30
HACKERNEWS_FEEDS = {
    "hackernews": HACKERNEWS_RSS,
    "hackernews/show": f"https://hnrss.org/show?count={HACKERNEWS_SHOW_ASK_COUNT}",
    "hackernews/ask": f"https://hnrss.org/ask?count={HACKERNEWS_SHOW_ASK_COUNT}",
}
# 피드별 상한 (창을 다 못 덮었는지 판정할 때 쓴다)
HACKERNEWS_FEED_LIMITS = {
    "hackernews": HACKERNEWS_FEED_COUNT,
    "hackernews/show": HACKERNEWS_SHOW_ASK_COUNT,
    "hackernews/ask": HACKERNEWS_SHOW_ASK_COUNT,
}

# hnrss.org가 죽은 회차용 폴백. 위 세 피드와 같은 범위를 Algolia로 재현한다.
# hnrss는 단일 호스트라 502가 나면 세 피드가 동시에 0건이 되고, 데일리는 고정
# 창으로 돌아 그날 HN이 통째로 유실된다 (2026-08-10 실측).
# tags의 콤마는 AND다. `(story,show_hn)`처럼 괄호로 싸면 OR이 돼 전체 글이 온다.
HACKERNEWS_ALGOLIA_FALLBACK = (
    ("hackernews", "story", f"points>={HACKERNEWS_MIN_POINTS}", HACKERNEWS_FEED_COUNT),
    ("hackernews/show", "story,show_hn", "", HACKERNEWS_SHOW_ASK_COUNT),
    ("hackernews/ask", "story,ask_hn", "", HACKERNEWS_SHOW_ASK_COUNT),
)

# GeekNews (news.hada.io) - Atom 1.0 피드
GEEKNEWS_RSS = "https://news.hada.io/rss/news"

# Product Hunt - RSS 피드
PRODUCTHUNT_RSS = "https://www.producthunt.com/feed"


def youtube_videos_url(canonical_id: str) -> str:
    """채널의 /videos 탭 URL. 핸들(@name)과 채널 ID(UC...)는 경로 형태가 다르다.

    핸들에 /channel/을 붙이면 YouTube가 404를 준다 (데스크톱에서 추가한 핸들
    구독이 전부 빈 목록으로 보이던 원인).
    """
    path = canonical_id if canonical_id.startswith("@") else f"channel/{canonical_id}"
    return f"https://www.youtube.com/{path}/videos"


# YouTube 채널 목록 (handle → channel_id 매핑)
# RSS URL: https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}
YOUTUBE_CHANNELS = {
    # 비즈니스/스타트업
    "비즈니스캔버스 B_ZCF": "UCWgXoKQ4rl7SY9UHuAwxvzQ",
    "EO Korea": "UCQ2DWm5Md16Dc3xRwwhVE7Q",
    "EO Global": "UClWTCPVi-AU9TeCN6FkGARg",
    "a16z": "UC9cn0TuPq4dnbTY-CBsm8XA",
    # AI/마케팅
    "AI Jason": "UCrXSVX9a1mj8l0CMLwKgMVw",
    "Nate Herk": "UC2ojq-nuP8ceeHqiroeKhBA",
    "Alex Hormozi": "UCUyDOdBWhC1MCxEjC46d-zw",
    "Kallaway Marketing": "UCg5WjzrwxRRUUDf7WHKPzsA",
    "Liam Ottley": "UCui4jxDaMb53Gdh-AZUTPAg",
    # 인터뷰/강의
    "Lex Fridman": "UCSHZKyawb77ixDdsGog4iWA",
    "Andrej Karpathy": "UCXUPKJO5MZQN11PqgIvyuvQ",
    "Chester Roh": "UCz-BiVywYdO6iXhjXkw_Kgw",
    # AI 공식
    "Hugging Face": "UCHlNU7kIZhRgSbhHvFoy72w",
    "LangChain": "UCC-lyoTfSrcJzA1ab3APAgw",
    "Anthropic": "UCrDwWp7EBBv4NwvScIpBDOA",
    "OpenAI": "UCXZCJLdBC09xxGZ6gcdrc6A",
}

# Every.to - 칼럼별 RSS 피드
EVERY_TO_FEEDS = {
    "Chain of Thought": "https://every.to/chain-of-thought/feed",
    "Source Code": "https://every.to/source-code/feed",
    "Context Window": "https://every.to/context-window/feed",
    "Napkin Math": "https://every.to/napkin-math/feed",
    "AI & I Podcast": "https://every.to/podcast/feed",
    # Guides 제외: /guides 페이지는 살아있지만 /guides/feed가 HTTP 500이고
    # 대체 피드도 sitemap도 없다 (2026-08-09 확인). 마지막 수집 2026-06-02.
}

# 블로그 구독 (이름 → RSS URL). 개인/기업 기술 블로그 공용.
PERSONAL_BLOGS = {
    "Addy Osmani": "https://addyosmani.com/rss.xml",
    "Phil Schmid": "https://www.philschmid.de/rss",
    "Tidy First (Kent Beck)": "https://tidyfirst.substack.com/feed",
    "PyTorchKR News": "https://discuss.pytorch.kr/c/news/14.rss",
    # /feed/ 도 살아있지만 최근 글 2건을 빠뜨린다. /blog/feed 가 블로그 정본.
    "Kakao Tech": "https://tech.kakao.com/blog/feed",
    # inblog 호스팅. /feed, /rss.xml은 404고 /blog/rss만 유효하다.
    "Kakao Ventures": "https://www.kakao.vc/blog/rss",
    # --- 뉴스레터 (2026-08-10 실측 등록) ---
    # 본문이 피드에 통째로 온다 (Latent Space 27KB, Import AI 20KB).
    "Latent Space": "https://www.latent.space/feed",
    "Import AI": "https://importai.substack.com/feed",
    "Simon Willison": "https://simonwillison.net/atom/everything/",
    # 본문 없이 제목·링크만 온다. enrichment가 원문을 받아 채운다.
    "TLDR AI": "https://tldr.tech/api/rss/ai",
    # --- 한국 기술 블로그 (2026-08-10 실측 등록) ---
    # 요즘IT(yozm.wishket.com)는 뺐다. 피드에 발행일 필드가 아예 없어
    # fetch_feed가 전량 스킵한다 (등록해도 매번 0건).
    "Toss Tech": "https://toss.tech/rss.xml",
    "우아한형제들": "https://techblog.woowahan.com/feed/",
    "NAVER D2": "https://d2.naver.com/d2.atom",
    "당근": "https://medium.com/feed/daangn",
    # --- GitHub 릴리스 (atom content에 릴리스 노트 본문이 들어 있다) ---
    "Claude Code Releases": "https://github.com/anthropics/claude-code/releases.atom",
    "LangChain Releases": "https://github.com/langchain-ai/langchain/releases.atom",
}

# AI 빅테크 블로그/뉴스 (RSS + HTML 스크래핑 혼합)
AI_LABS_SOURCES = [
    {"name": "OpenAI News", "type": "rss", "url": "https://openai.com/news/rss.xml"},
    {
        "name": "Anthropic News",
        "type": "anthropic",
        "url": "https://www.anthropic.com/news",
    },
    {
        "name": "Anthropic Research",
        "type": "anthropic",
        "url": "https://www.anthropic.com/research",
    },
    {
        "name": "Anthropic Engineering",
        "type": "anthropic",
        "url": "https://www.anthropic.com/engineering",
    },
    # Webflow가 rss.xml을 내준다. 인덱스 HTML 파싱보다 정확하고 playwright가 필요 없다.
    {
        "name": "LangChain Blog",
        "type": "rss",
        "url": "https://www.langchain.com/blog/rss.xml",
    },
    # 2026-08-10 실측 등록. 넷 다 200 + 엔트리 있음.
    # Meta AI(ai.meta.com/blog/rss/)는 400, DeepSeek(api.deepseek.com/rss)은 401이라 뺐다.
    {
        "name": "Google DeepMind",
        "type": "rss",
        "url": "https://deepmind.google/blog/rss.xml",
    },
    {
        "name": "Google Research",
        "type": "rss",
        "url": "https://research.google/blog/rss/",
    },
    {
        "name": "Hugging Face Blog",
        "type": "rss",
        "url": "https://huggingface.co/blog/feed.xml",
    },
    {"name": "Mistral AI", "type": "rss", "url": "https://mistral.ai/rss.xml"},
]

# Lobsters - RSS + 게시물별 JSON. JSON의 comments 배열이
# comment_plain/score/depth를 그대로 줘서 Comment에 1:1로 매핑된다.
LOBSTERS_RSS = "https://lobste.rs/rss"
LOBSTERS_ITEM_JSON = "https://lobste.rs/s/{short_id}.json"

# arXiv - Atom API. 카테고리별로 따로 조회해 합친다.
# 크롤러가 합친 뒤 --count로 자르므로 카테고리를 늘려도 수집량은 그대로다.
# 넓어지는 건 커버리지지 볼륨이 아니다. cs.AI 단독이면 cs.CL/cs.LG에만 올라온
# 논문을 통째로 놓친다 (교차 등록이 항상 되어 있지는 않다).
ARXIV_CATEGORIES = ["cs.AI", "cs.CL", "cs.LG", "cs.CV"]
ARXIV_MAX_RESULTS_PER_CATEGORY = 50


def arxiv_api_url(
    category: str,
    max_results: int = ARXIV_MAX_RESULTS_PER_CATEGORY,
    start: int = 0,
) -> str:
    """카테고리별 arXiv Atom API URL.

    http가 아니라 https를 쓴다. export.arxiv.org는 http를 리다이렉트하는데,
    feedparser가 리다이렉트를 따라가며 조용히 빈 피드를 돌려주는 경우가 있다.

    start는 arXiv API가 공식 지원하는 오프셋이다. 한 장이 50건인데 그게 실측
    4시간25분치라(2026-08-10, cs.AI), count를 그보다 크게 잡으면 페이징 없이는
    채울 수가 없다.
    """
    return (
        "https://export.arxiv.org/api/query"
        f"?search_query=cat:{category}"
        "&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={max_results}"
        f"&start={start}"
    )


# 하위 호환: 단일 URL을 참조하던 코드가 남아 있을 수 있다.
ARXIV_API_URL = arxiv_api_url("cs.AI")

# HuggingFace Daily Papers - JSON API
HUGGINGFACE_PAPERS_URL = "https://huggingface.co/api/daily_papers"
