# Source Backlog

<p align="center"><a href="TODO.md">English</a> | <b>한국어</b></p>

Skim에 넣을 source 후보와 promotion checklist입니다. 구현 계획은 `docs/plans/` 아래에 두고, AI 작업 규칙은 `AGENTS.md`에 둡니다.

## 이미 포함됨

- Communities: Hacker News, GeekNews, Product Hunt
- Social/API: Threads, X, LinkedIn, Reddit
- Articles: Every.to, `PERSONAL_BLOGS`의 personal blogs
- Video: `YOUTUBE_CHANNELS`의 YouTube channels
- Papers: Hugging Face Daily Papers, arXiv cs.AI
- AI labs: OpenAI, Anthropic, LangChain

## 후보 계정

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

## 후보 소스

- Google AI blogs and research updates

## Promotion Checklist

- 가능하면 `packages/skim-core/src/skim_core/feed_config.py`에 static feed/source config만 추가한다.
- config로 부족할 때만 `packages/skim-core/src/skim_core/crawlers/`에 crawler를 추가하거나 수정한다.
- 새 platform은 `packages/skim-core/src/skim_core/crawlers/__init__.py`에 등록한다.
- README supported platforms를 갱신하고 focused regression 또는 smoke check를 하나 추가한다.
