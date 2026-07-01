# Swift Desktop Baseline

Historical baseline captured before implementation on 2026-07-01. It describes the pre-removal Tauri desktop surface.

## Existing Desktop Surface

- `apps/desktop` was the React/Vite/Tauri app at capture time.
- `apps/desktop/AGENTS.md` says frontend code calls Tauri through `src/lib/api.ts`, backend commands should bridge existing CLI/scripts, and generated Tauri output must not be edited.
- `apps/desktop/src/App.tsx` currently exposes three tabs: Sources, Credentials, and Explorer.
- `apps/desktop/src/components/SourcesPanel.tsx` already stores tracked sources, including YouTube channels, through the Tauri `upsert_tracked_source` command.
- `apps/desktop/src/components/ExplorerPanel.tsx` searches stored posts from `data/skim.db`, loads results in batches of 25, and shows a selected post detail with external URL opening.

## SQLite Contract To Preserve

The removed Tauri bridge created this app schema. The Swift app must use the same table/column names for the subset it owns:

- `posts`: `id`, `platform`, `source`, `external_id`, `author`, `title`, `content`, `url`, `timestamp`, `likes`, `comments`, `reposts`, `views`, `summary`, `content_markdown`, `word_count`, `extra`, `crawled_at`.
- `tracked_sources`: `id`, `platform`, `source_type`, `display_name`, `canonical_id`, `handle_or_url`, `is_enabled`, `focus_level`, `notes`, `created_at`, `updated_at`.
- `tracked_sources` has `UNIQUE(platform, canonical_id)`, so YouTube channel insertion should use upsert semantics instead of creating duplicates.

## New App Target

- `apps/swift-desktop` does not exist yet.
- The new app should be a consumer of the existing local workspace, not a crawler rewrite.
- The default database path should resolve to the repo workspace `data/skim.db`, with fixture databases used for deterministic tests and smoke runs.

## First-Version Product Shape

- First screen: a dense, readable feed/dashboard for scanning recent saved content.
- Source input: paste YouTube channel URLs or handles, normalize locally when possible, and save to `tracked_sources`.
- Detail preview: embed YouTube watch/short/embed links when possible; unsupported links should remain easy to open externally.
