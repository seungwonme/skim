# Swift Desktop Feed Dashboard Proof Review

Reviewed on 2026-07-01 against the current `goal/swift-desktop-feed` worktree.

## What Exists

- `apps/swift-desktop` is a SwiftPM macOS package with:
  - `SkimDesktop`: SwiftUI app executable.
  - `SkimDesktopCore`: local data, URL parsing, and preview classification library.
  - `SkimDesktopSmoke`: command-line fixture/workspace smoke executable.
  - `SkimDesktopCoreTests`: focused tests for path resolution, SQLite fixture reads, `tracked_sources` upsert, YouTube channel URL parsing, and embed classification.
- The existing React/Tauri desktop app remains in `apps/desktop`; no migration deletion happened.

## Data Contract

- Default workspace database path resolves to `data/skim.db`, with `SKIM_WORKSPACE_ROOT` respected when set.
- `SkimDatabase.ensureSchema()` creates the same subset used by the current Tauri bridge:
  - `posts` for feed/dashboard content.
  - `tracked_sources` for source subscriptions.
- Fixture tests create SQLite databases at runtime and verify reading posts, reading tracked sources, loading a dashboard snapshot, and upserting a YouTube source through `UNIQUE(platform, canonical_id)`.

## Dashboard Readability

- The first screen is the actual feed/dashboard, not a landing page.
- The layout is intentionally dense:
  - top metrics for Posts, Sources, and currently loaded posts;
  - horizontal tracked source chips;
  - recent content list with platform, source, timestamp, title, and summary/content excerpt;
  - detail pane with title, source metrics, selectable text, and preview/fallback.
- Empty/error states are explicit via `ContentUnavailableView`, so an empty `data/skim.db` does not look broken.

## YouTube Channel URL Flow

- The source entry lives inline in the Tracked Sources section.
- Supported pasted input:
  - `https://www.youtube.com/channel/<channel_id>`
  - `https://www.youtube.com/@handle`
  - `@handle`
  - `https://www.youtube.com/feeds/videos.xml?channel_id=<channel_id>`
- Channel IDs are saved as canonical IDs when present.
- Handles are saved as `@handle` because resolving handles to channel IDs would require a network refresh/import step, which is out of scope.
- The persisted row uses `platform = youtube`, `source_type = channel`, and writes into `tracked_sources`.

## Embed And Fallback

- `ContentPreview.classify(_:)` converts these URLs to `https://www.youtube.com/embed/<video_id>`:
  - YouTube watch URLs;
  - YouTube shorts URLs;
  - YouTube embed URLs;
  - `youtu.be` short URLs.
- SwiftUI detail uses a WebKit-backed preview pane for YouTube embed-capable links.
- Unsupported links render a clear fallback panel with the original URL and an external-open action.

## Known Gaps

- No YouTube login or live subscription-list import.
- No network resolution from `@handle` to channel ID.
- No App Store packaging, code signing, or notarization.
- No crawler rewrite; Python crawlers remain the source of feed data.
- The WebKit preview is compile/test verified through URL classification and build gates, not visually inspected in a running app window during this goal.

## Evidence Map

- Buildable SwiftUI app: `swift build --package-path apps/swift-desktop`.
- Focused tests: `swift test --package-path apps/swift-desktop`.
- Fixture smoke: `swift run --package-path apps/swift-desktop SkimDesktopSmoke --fixture`.
- Required terms are intentionally present here for proof search: `data/skim.db`, `tracked_sources`, YouTube, channel URL, embed, fallback, known gaps.
