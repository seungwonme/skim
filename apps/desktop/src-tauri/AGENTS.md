# desktop/src-tauri

Tauri v2 Rust backend for the desktop app.

## Rules

- The root Cargo workspace owns Rust builds; run `cargo test -p desktop` from the repo root.
- Add commands in `src/lib.rs`, register them in `invoke_handler!`, then expose typed frontend wrappers in `apps/desktop/src/lib/`.
- Keep long-running CLI work in subprocesses. Do not port crawler logic into Rust.
- Do not manually edit generated Tauri files under `gen/`.
