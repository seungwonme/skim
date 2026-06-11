"""
@file feed_config.py
@description 일일 RSS 피드 수집 설정
"""

# Hacker News - hnrss.org (공식보다 풍부한 데이터, 필터링 지원)
HACKERNEWS_RSS = "https://hnrss.org/newest?points=30"

# GeekNews (news.hada.io) - Atom 1.0 피드
GEEKNEWS_RSS = "https://news.hada.io/rss/news"

# Product Hunt - RSS 피드
PRODUCTHUNT_RSS = "https://www.producthunt.com/feed"

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
    "Guides": "https://every.to/guides/feed",
}

# 개인 블로그 구독 (이름 → RSS URL)
PERSONAL_BLOGS = {
    "Addy Osmani": "https://addyosmani.com/rss.xml",
    "Phil Schmid": "https://www.philschmid.de/rss",
    "Tidy First (Kent Beck)": "https://tidyfirst.substack.com/feed",
}

# AI 빅테크 블로그/뉴스 (RSS + HTML 스크래핑 혼합)
AI_LABS_SOURCES = [
    {"name": "OpenAI News", "type": "rss", "url": "https://openai.com/news/rss.xml"},
    {"name": "Anthropic News", "type": "anthropic", "url": "https://www.anthropic.com/news"},
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
    {"name": "LangChain Blog", "type": "langchain", "url": "https://www.langchain.com/blog"},
]

# arXiv cs.AI - Atom API (최신 50개)
ARXIV_API_URL = "http://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=50"

# HuggingFace Daily Papers - JSON API
HUGGINGFACE_PAPERS_URL = "https://huggingface.co/api/daily_papers"
