<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-17 | Updated: 2026-04-17 -->

# skim-cli

## Purpose
Typer 기반 사용자 CLI 패키지. `uv run skim ...` 진입점을 제공하며, `skim_core` 라이브러리를 얇게 감싸 crawl/login/platforms 같은 명령을 노출한다. 데스크탑 앱의 백엔드도 이 CLI를 subprocess로 호출한다.

## Key Files

| File | Description |
|------|-------------|
| `pyproject.toml` | `skim-cli` 패키지 정의 (`[project.scripts] skim = "skim_cli.cli:app"`) |
| `src/skim_cli/` | CLI 구현 (see `src/skim_cli/AGENTS.md`) |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `src/` | 표준 src-layout 컨테이너 |

## For AI Agents

### Working In This Directory
- 비즈니스 로직은 `skim_core`에 두고 이 패키지는 argument parsing/presentation만 담당
- 새 subcommand 추가 시 Typer app에 등록 후 `skim platforms` 출력/README 사용법 동시 업데이트
- 프린트/진행 표시는 `skim_core.print`를 통해 일관된 Rich 스타일을 사용

### Testing Requirements
- `skim crawl ...` 기반 회귀는 `tests/test_main_crawl_persistence.py`에서 lib 수준으로 고정
- 엔드투엔드 확인은 `uv run skim platforms` 스모크 실행이 가장 싸다

## Dependencies

### Internal
- `packages/skim-core/` — 모든 실제 동작이 여기 있음

### External
- `typer`, `rich`

<!-- MANUAL: -->
