# Source Backlog

<p align="center"><b>English</b> | <a href="TODO.ko.md">한국어</a></p>

Candidate sources and promotion checklist for Skim. Keep implementation plans under `docs/plans/` and AI working rules in `AGENTS.md`.

## Already Covered

- Communities: Hacker News, GeekNews, Product Hunt
- Social/API: Threads, X, LinkedIn, Reddit
- Articles: Every.to, personal blogs in `PERSONAL_BLOGS`
- Video: YouTube channels in `YOUTUBE_CHANNELS`
- Papers: Hugging Face Daily Papers, arXiv cs.AI
- AI labs: OpenAI, Anthropic, LangChain

## Candidate Accounts

### LinkedIn

- https://www.linkedin.com/in/kjh941213/
- https://www.linkedin.com/in/gb-jeong/

### YouTube

- https://www.youtube.com/@B_ZCF
- https://www.youtube.com/@eo_korea
- https://www.youtube.com/@eoglobal
- https://www.youtube.com/@a16z
- https://www.youtube.com/@AIJasonZ
- https://www.youtube.com/@nateherk
- https://www.youtube.com/@AlexHormozi
- https://www.youtube.com/@kallawaymarketing
- https://www.youtube.com/@LiamOttley
- https://www.youtube.com/@lexfridman/videos
- https://www.youtube.com/@AndrejKarpathy
- https://www.youtube.com/@chester_roh
- https://www.youtube.com/@HuggingFace
- https://www.youtube.com/@LangChain
- https://www.youtube.com/@anthropic-ai
- https://www.youtube.com/@OpenAI

## Candidate Sources

- Google AI blogs and research updates

## Retired Sources

- `every.to/Guides` — `/guides/feed` returns HTTP 500 with no alternate feed or sitemap (checked 2026-08-09). The `/guides` page itself is alive, so it can return as a `scrape` source if it becomes worth a custom index parser. Last collected 2026-06-02.

## Promotion Checklist

- Run `uv run skim source probe <url>` first. It reports the feed URL, backfill depth, and the observed extraction tier (`rss` / `rss+enrich` / `rss+render` / `scrape`), so the decision below is made on measurements rather than guesses.
- Prefer the highest tier the probe reports. Drop to `scrape` only when the source is worth a hand-written index parser.
- Add static feed/source config in `packages/skim-core/src/skim_core/feed_config.py` when possible.
- Add or update a crawler in `packages/skim-core/src/skim_core/crawlers/` only when config is not enough.
- Register new platforms in `packages/skim-core/src/skim_core/crawlers/__init__.py`.
- Update README supported platforms and add one focused regression or smoke check.
