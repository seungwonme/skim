# Source Backlog

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

## Promotion Checklist

- Add static feed/source config in `packages/skim-core/src/skim_core/feed_config.py` when possible.
- Add or update a crawler in `packages/skim-core/src/skim_core/crawlers/` only when config is not enough.
- Register new platforms in `packages/skim-core/src/skim_core/crawlers/__init__.py`.
- Update README supported platforms and add one focused regression or smoke check.
