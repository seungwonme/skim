# Skim Swift Desktop

SwiftUI macOS app for reading the local Skim workspace.

## Scope

- Default workspace database: `data/skim.db` at the repo/workspace root.
- First screen: feed/dashboard for saved posts.
- Source entry: pasted YouTube channel URL or handle saved into `tracked_sources`.
- Preview: embedded YouTube links when possible, external-open fallback otherwise.

The existing React/Tauri app remains under `apps/desktop`. Crawlers stay in the Python packages.

## Commands

```bash
swift test --package-path apps/swift-desktop
swift build --package-path apps/swift-desktop
swift run --package-path apps/swift-desktop SkimDesktopSmoke --fixture
```
