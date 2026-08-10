# Source Backlog

<p align="center"><b>English</b> | <a href="TODO.ko.md">한국어</a></p>

Candidate sources and promotion checklist for Skim. Keep implementation plans under `docs/plans/` and AI working rules in `AGENTS.md`.

## Already Covered

- Communities: Hacker News (newest + Show + Ask), Lobsters, GeekNews, Product Hunt
- Social/API: Threads, X, LinkedIn, Reddit
- Social/public: Bluesky (`BLUESKY_ACCOUNTS`, no login required)
- Articles: Every.to, blogs and newsletters in `PERSONAL_BLOGS`
- Video: YouTube channels in `YOUTUBE_CHANNELS`
- Papers: Hugging Face Daily Papers, arXiv (cs.AI, cs.CL, cs.LG, cs.CV)
- AI labs: OpenAI, Anthropic, LangChain, Google DeepMind, Google Research, Hugging Face, Mistral

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

## Rejected Sources

Probed and failed, so nobody re-checks them by hand (2026-08-10):

- Meta AI `https://ai.meta.com/blog/rss/` — HTTP 400.
- DeepSeek `https://api.deepseek.com/rss` — HTTP 401.
- 요즘IT `https://yozm.wishket.com/magazine/feed/` — HTTP 200 with 30 entries, but no
  date field on any entry. `fetch_feed` drops undated entries, so registering it collects
  nothing while looking healthy. Needs a custom parser that reads dates off the article page.

## Underfilled Sources

Registered and working, but the daily `--days 1` window collects nothing from them.

- `bluesky` — the crawler is fine (verified against production 2026-08-10: HTTP 200,
  20 entries), but `BLUESKY_ACCOUNTS` holds one account, `bsky.app`, whose posting
  interval runs days to a month. The newest post was 4 days old at check time, so a
  daily run yields 0 almost every day. A wider lookback does not fix this — it would
  just re-collect the same posts. The fix is a list of accounts that actually post
  daily, which is a curation decision. Until then `doctor` will keep flagging bluesky
  as a 0-count regression.

## Retired Sources

- `every.to/Guides` — `/guides/feed` returns HTTP 500 with no alternate feed or sitemap (checked 2026-08-09). The `/guides` page itself is alive, so it can return as a `scrape` source if it becomes worth a custom index parser. Last collected 2026-06-02.

## Promotion Checklist

- Run `uv run skim source probe <url>` first. It reports the feed URL, backfill depth, and the observed extraction tier (`rss` / `rss+enrich` / `rss+render` / `scrape`), so the decision below is made on measurements rather than guesses.
- Prefer the highest tier the probe reports. Drop to `scrape` only when the source is worth a hand-written index parser.
- Add static feed/source config in `packages/skim-core/src/skim_core/feed_config.py` when possible.
- Add or update a crawler in `packages/skim-core/src/skim_core/crawlers/` only when config is not enough.
- Register new platforms in `packages/skim-core/src/skim_core/crawlers/__init__.py`.
- Update README supported platforms and add one focused regression or smoke check.
