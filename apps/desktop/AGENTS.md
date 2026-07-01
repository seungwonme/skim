# desktop

React/Vite frontend plus Tauri v2 backend for the local Skim desktop app.

## Commands

- Dev: `pnpm desktop:dev`
- Build: `pnpm desktop:build`
- Frontend typecheck: `pnpm --filter @skim/desktop typecheck`
- Desktop backend tests: `cargo test -p desktop`

## Rules

- Keep frontend/backend versions aligned with the repo release version when touching release metadata.
- Frontend code calls Tauri through `src/lib/api.ts`; components should not import `@tauri-apps/api` directly.
- Backend commands in `src-tauri/src/lib.rs` should bridge existing CLI/scripts instead of reimplementing Python behavior.
- Do not manually edit `src-tauri/gen/` or build artifacts.
