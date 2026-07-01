# tests

Python regression tests for CLI/core behavior.

## Rules

- Use `tmp_path` and `SKIM_WORKSPACE_ROOT` to avoid touching repo-local `data/`.
- Mock network and subprocess boundaries unless a command is explicitly a smoke test.
- Add focused tests for behavior-changing refactors; avoid broad fixture architecture for one bug.
- Run targeted tests first, then `uv run pytest tests -q` before claiming Python coverage.
