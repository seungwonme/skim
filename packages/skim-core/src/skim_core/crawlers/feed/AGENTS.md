# skim_core/crawlers/feed

Feed-based crawlers for RSS, Atom, JSON feeds, and source-specific HTML indexes.

## Rules

- Supported feed crawlers: `hackernews`, `geeknews`, `youtube`, `producthunt`, `arxiv`, `huggingface`, `everyto`, `blogs`, `ailabs`.
- Keep source lists in `feed_config.py` unless a crawler already has a more specific local config.
- Use `since` for time-windowed fetches; honor `count` only as an optional cap.
- Avoid network work in tests by mocking HTTP/feed responses.
