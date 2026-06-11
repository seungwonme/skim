"""Shared dataclasses for research/* (Phase 1 + Phase 2)."""

from dataclasses import dataclass, field


@dataclass
class SearchStats:
    """`search_posts` 반환 측정 지표.

    Phase 2 `refresh.py` 가 동일 모듈에서 import 하여 응답 stats 에 통합한다.
    """

    rows_scanned: int = 0
    rows_returned: int = 0
    latency_ms: int = 0
    short_tokens: list[str] = field(default_factory=list)
