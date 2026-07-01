<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-17 | Updated: 2026-04-17 -->

# skim-core

## Purpose
skim 파이프라인의 라이브러리 레이어. 크롤러 Protocol과 구현체, SQLite 스토리지, enrichment(defuddle/yt-dlp), feed 설정, 경로 헬퍼를 제공한다. `skim_cli`/데스크탑 앱/테스트/`scripts/`가 모두 이 패키지를 소비한다.

## Key Files

| File | Description |
|------|-------------|
| `pyproject.toml` | `skim-core` 패키지 선언 (uv workspace 멤버) |
| `src/skim_core/` | 실제 Python 소스 트리 (see `src/skim_core/AGENTS.md`) |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `src/` | 표준 src-layout 컨테이너 |

## For AI Agents

### Working In This Directory
- 외부에서는 반드시 `from skim_core...` 절대 import를 사용. 패키지 내부는 상대 import 허용
- 공개 API가 바뀌면 `skim_cli.cli`, 데스크탑 백엔드, `tests/`의 호출부를 함께 확인
- 신규 모듈은 `src/skim_core/` 아래 위치해야 uv 빌드 대상에 포함됨

### Testing Requirements
- `uv run pytest tests -v` (레포 루트 `tests/`가 이 패키지를 대상으로 회귀 커버)
- 린트: 루트 `uv run black`/`isort`/`flake8`/`pylint`

### Common Patterns
- 네트워크/파일 I/O는 `async` 유지. CLI 레이어에서 `asyncio.run` 호출
- 경로는 `skim_core.paths.workspace_root()`를 통해 해석하여 `SKIM_WORKSPACE_ROOT` override를 자동 존중

## Dependencies

### Internal
- 소비자: `packages/skim-cli/`, `apps/desktop/src-tauri/`, `tests/`, `scripts/`

### External
- `httpx`, `feedparser`, `playwright`, `yt-dlp`, `pydantic`, `sqlite3`(std)

<!-- MANUAL: -->
