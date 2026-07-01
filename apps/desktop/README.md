# Skim Desktop

macOS-first desktop app for Skim, built with Tauri, React, and TypeScript.
It reads the same local SQLite database and session files used by the CLI.

## Commands

```bash
pnpm install
pnpm desktop:dev
pnpm desktop:build
```

## Current Scope

- tracked source management
- macOS Keychain credential storage
- session status inspection and browser login trigger
- `data/skim.db` browsing
