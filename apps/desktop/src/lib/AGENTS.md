# desktop/src/lib

Typed frontend bridge to Tauri commands.

## Rules

- `api.ts` is a thin wrapper over `invoke`; keep command names aligned with `src-tauri/src/lib.rs`.
- `types.ts` is the shared frontend contract for backend payloads.
- When a backend command shape changes, update `api.ts`, `types.ts`, the Rust command, and the relevant component together.
- Do not put UI state or formatting logic here.
