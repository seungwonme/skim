"""
@file __init__.py
@description 크롤러 레지스트리

모든 크롤러를 플랫폼 이름으로 조회할 수 있는 REGISTRY를 제공합니다.
"""

from .api.linkedin import LinkedInAPICrawler
from .api.reddit import RedditAPICrawler
from .api.threads import ThreadsAPICrawler
from .api.x import XAPICrawler
from .base import Crawler
from .feed.ailabs import AILabsCrawler
from .feed.arxiv import ArxivCrawler
from .feed.blogs import BlogsCrawler
from .feed.everyto import EveryToCrawler
from .feed.geeknews import GeekNewsCrawler
from .feed.hackernews import HackerNewsCrawler
from .feed.huggingface import HuggingFaceCrawler
from .feed.producthunt import ProductHuntCrawler
from .feed.youtube import YouTubeCrawler

REGISTRY: dict[str, type] = {
    "threads": ThreadsAPICrawler,
    "linkedin": LinkedInAPICrawler,
    "x": XAPICrawler,
    "reddit": RedditAPICrawler,
    "hackernews": HackerNewsCrawler,
    "geeknews": GeekNewsCrawler,
    "youtube": YouTubeCrawler,
    "producthunt": ProductHuntCrawler,
    "arxiv": ArxivCrawler,
    "huggingface": HuggingFaceCrawler,
    "everyto": EveryToCrawler,
    "blogs": BlogsCrawler,
    "ailabs": AILabsCrawler,
}

__all__ = ["Crawler", "REGISTRY"]
