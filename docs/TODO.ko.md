# Source Backlog

<p align="center"><a href="TODO.md">English</a> | <b>한국어</b></p>

Skim에 넣을 source 후보와 promotion checklist입니다. 구현 계획은 `docs/plans/` 아래에 두고, AI 작업 규칙은 `AGENTS.md`에 둡니다.

## 이미 포함됨

- Communities: Hacker News (newest + Show + Ask), Lobsters, GeekNews, Product Hunt
- Social/API: Threads, X, LinkedIn, Reddit
- Articles: Every.to, `PERSONAL_BLOGS`의 블로그와 뉴스레터
- Video: `YOUTUBE_CHANNELS`의 YouTube channels
- Papers: Hugging Face Daily Papers, arXiv (cs.AI, cs.CL, cs.LG, cs.CV)
- AI labs: OpenAI, Anthropic, LangChain, Google DeepMind, Google Research, Hugging Face, Mistral

## 등록하지 않은 소스

실측해서 떨어진 것들. 손으로 다시 확인하지 않도록 남긴다 (2026-08-10):

- Meta AI `https://ai.meta.com/blog/rss/` — HTTP 400.
- DeepSeek `https://api.deepseek.com/rss` — HTTP 401.
- 요즘IT `https://yozm.wishket.com/magazine/feed/` — 200에 엔트리 30건이 오는데
  발행일 필드가 하나도 없다. `fetch_feed`가 날짜 없는 엔트리를 버리므로 등록해도
  매번 0건인데 겉보기엔 멀쩡하다. 글 페이지에서 날짜를 읽는 전용 파서가 필요하다.

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

## 제외된 소스

- `bluesky` - 2026-08-10에 추가했다가 같은 날 제거했다. 크롤러는 동작했지만
  (프로덕션 실측 HTTP 200, 엔트리 20개) `BLUESKY_ACCOUNTS`에 게시 빈도가 낮은 계정
  하나뿐이라 데일리 `--days 1` 창에서 매번 0건이었다. 실제 실행 조건에서 산출을
  확인하지 않고 넣었고, 요청받은 플랫폼도 아니었다. 되살리려면 매일 올라오는 계정
  목록이 필요한데 그건 큐레이션 결정이다.
- `every.to/Guides` - `/guides/feed`가 HTTP 500이고 대체 피드도 sitemap도 없다 (2026-08-09 확인). `/guides` 페이지 자체는 살아 있어서, 전용 인덱스 파서를 만들 값어치가 생기면 `scrape` 소스로 복귀할 수 있다. 마지막 수집 2026-06-02.

## Promotion Checklist

- 먼저 `uv run skim source probe <url>`를 돌린다. 피드 URL, 백필 깊이, 관측된 추출 등급(`rss` / `rss+enrich` / `rss+render` / `scrape`)을 보고하므로 아래 판단을 추측이 아닌 실측으로 한다.
- probe가 보고한 가장 높은 등급을 우선한다. 손으로 인덱스 파서를 만들 값어치가 있을 때만 `scrape`로 내려간다.
- 가능하면 `packages/skim-core/src/skim_core/feed_config.py`에 static feed/source config만 추가한다.
- config로 부족할 때만 `packages/skim-core/src/skim_core/crawlers/`에 crawler를 추가하거나 수정한다.
- 새 platform은 `packages/skim-core/src/skim_core/crawlers/__init__.py`에 등록한다.
- README supported platforms를 갱신하고 focused regression 또는 smoke check를 하나 추가한다.
