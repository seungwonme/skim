# Skim Desktop

SwiftUI macOS app for reading the local Skim workspace.

## Scope

- Default workspace database: `data/skim.db` at the repo/workspace root.
- First screen: feed/dashboard for saved posts.
- Source entry: pasted YouTube channel URL or handle saved into `tracked_sources`.
- Preview: embedded YouTube links when possible, external-open fallback otherwise.

Crawlers stay in the Python packages.

## Commands

```bash
swift test --package-path apps/desktop
swift build --package-path apps/desktop
swift run --package-path apps/desktop SkimDesktopSmoke --fixture
scripts/build-app.sh              # install /Applications/Skim.app
scripts/build-app.sh ~/Applications
```
