# desktop/src-tauri/src

Rust command implementations for the desktop backend.

## Rules

- Keep workspace paths derived from `SKIM_WORKSPACE_ROOT` or the existing `workspace_root()` helper.
- If database schema expectations change, update `APP_SCHEMA` and add or adjust `cargo test -p desktop` coverage.
- Keep command errors actionable; return `Result<_, String>` messages that name the failed operation.
- Avoid destructive file operations unless they are already represented by an explicit command and test.
