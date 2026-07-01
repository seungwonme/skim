# desktop/src

React TypeScript source for the desktop app shell.

## Rules

- Keep Tauri calls inside `lib/api.ts`; UI components consume typed wrappers and shared types from `lib/`.
- Use icons from `react-icons/lu`, matching the existing UI.
- Keep state local and simple unless an existing cross-component flow already requires a shared helper.
- Verify UI code with `pnpm --filter @skim/desktop typecheck` and `pnpm --filter @skim/desktop build`.
