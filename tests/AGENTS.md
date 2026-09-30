# tests

Python regression tests for CLI/core behavior.

## Rules

- Never touch the workspace `data/`; in the main checkout it holds the production DB and crawl state.
  `conftest.py` points the default DB (`skim_core.db.DB_PATH`) and the GeekNews budget file at
  `tmp_path` for every test, and fails any test that opens, writes, or creates a path under `data/`.
  For other `data/` paths, pass a path argument or monkeypatch the module constant.
- Mock network and subprocess boundaries unless a command is explicitly a smoke test.
- Add focused tests for behavior-changing refactors; avoid broad fixture architecture for one bug.
- Run targeted tests first, then `uv run pytest tests -q` before claiming Python coverage.
