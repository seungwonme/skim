# Goal Plan Instructions

Long-running agent work that continues until a verifiable condition holds.
Start the loop with `/goal @GOAL.md`.

## Goal

Create a SwiftUI macOS desktop app under apps/swift-desktop that reads data/skim.db and provides a feed/dashboard with channel-URL YouTube source entry and embeddable content previews.

## Proof

Run from the goal worktree root:

```bash
set -euo pipefail
swift test --package-path apps/swift-desktop
swift build --package-path apps/swift-desktop
swift run --package-path apps/swift-desktop SkimDesktopSmoke --fixture
test -f docs/proof/swift-desktop-feed-dashboard.md
rg -n "data/skim.db|tracked_sources|YouTube|channel URL|embed|fallback|known gaps" \
  apps/swift-desktop/README.md docs/proof/swift-desktop-feed-dashboard.md
```

The fixture smoke must use the same posts/tracked_sources schema shape as `data/skim.db`. If a local `data/skim.db` exists in the worktree, also run the smoke against it and report that result as current-at-proof-time evidence.

## Acceptance Criteria

1. `apps/swift-desktop` contains a buildable SwiftUI macOS app package with focused tests and a small smoke executable; the existing `apps/desktop` Tauri app remains available.
2. The Swift app defaults to the workspace `data/skim.db` path, reads posts and tracked sources from SQLite, and presents a scannable feed/dashboard for recent content.
3. The app supports YouTube channel subscription by pasted channel URL/handle input, normalizes what can be normalized locally, and persists the result to `tracked_sources` without requiring login.
4. Content detail supports embedded preview for easy links, at minimum YouTube watch/short/embed URLs, and uses a clear external-open fallback for unsupported URLs.
5. `docs/proof/swift-desktop-feed-dashboard.md` records a semantic review of readability, dashboard information hierarchy, embed behavior, exclusions, and known gaps with concrete examples.

## Context

- Repo is `skim`, a local-first information curation pipeline. `README.md` says the current desktop app is React + Vite + Tauri and shares the local workspace with the CLI.
- Existing desktop code lives under `apps/desktop`. Read `apps/desktop/AGENTS.md` before touching nearby code. Existing frontend calls Tauri through `apps/desktop/src/lib/api.ts`.
- Current source management UI is in `apps/desktop/src/components/SourcesPanel.tsx`; current stored-post browsing UI is in `apps/desktop/src/components/ExplorerPanel.tsx`.
- Current desktop bridge/schema lives in `apps/desktop/src-tauri/src/lib.rs`; it defines `posts`, `tracked_sources`, `platform_credentials`, and search commands.
- Python crawler/core remains under `packages/skim-core` and `packages/skim-cli`. `packages/skim-core/src/skim_core/feed_config.py` currently stores YouTube channel display-name to channel-id mappings.
- `data/`, `refs/`, and `worktrees/` are local/runtime material unless explicitly named.

## Scope

- Add a new SwiftUI macOS app under `apps/swift-desktop`.
- Add the minimal Swift data layer needed to read `posts` and `tracked_sources` from SQLite and write YouTube tracked-source entries.
- Add a feed/dashboard first screen optimized for scanning content, selecting an item, and previewing embeddable links.
- Add YouTube channel URL paste flow for `youtube.com/channel/...`, `youtube.com/@handle`, `@handle`, and RSS URL inputs, with deterministic local normalization where possible.
- Add focused tests, fixture/smoke checks, and proof documentation.

## Out of Scope

- Rewriting Python crawlers or enrichment in Rust/Swift.
- Migrating the Rust Tauri bridge or deleting the existing `apps/desktop` app.
- YouTube login, importing the user's live YouTube subscription list, or browser automation.
- App Store packaging, code signing, notarization, iOS, or cross-platform UI.
- New hosted backend, cloud sync, account system, or remote publishing.

## Constraints

- Prefer SwiftPM, SwiftUI, SQLite3, WebKit, Foundation, and AppKit/native APIs already available on macOS. Do not add third-party Swift dependencies unless a row documents why stdlib/native APIs are insufficient.
- Keep existing Python CLI/crawler behavior intact. Do not edit crawler behavior unless a compile/test blocker proves the app cannot work without a narrow schema compatibility fix.
- Do not delete or replace `apps/desktop`; the Swift app is added beside it.
- Target edits should stay under `apps/swift-desktop/`, `docs/proof/`, and goal files unless a small root metadata change is required and explained in `progress.tsv`.
- No destructive filesystem actions outside staged commits. Do not use `git reset --hard`, force-push, or delete user runtime data.
- Proof should avoid live network. URL parser/embed tests use static URLs; any live local `data/skim.db` smoke is current-at-proof-time evidence only.

## Input Stability

`data/skim.db` is mutable and may be absent from this worktree. Deterministic tests must build a fixture SQLite database during test/smoke execution. If a real `data/skim.db` exists, report its smoke result as current-at-proof-time only.

## Target Change Tracking

This is a repo worktree at `worktrees/goal-swift-desktop-feed` on branch `goal/swift-desktop-feed`. Each loop step commits code/proof changes plus `progress.tsv` on that branch.

## Bounds

None with supervision. If the Swift toolchain/Xcode command line tools are unavailable, record the exact blocker and stop. If one row exceeds about 90 minutes without passing its local check, mark it blocked or discard it with notes before choosing the next smallest row.

## How Progress Is Tracked

- `progress.tsv` is the plan and progress table; the task breakdown lives in its rows. Edit it ONLY through the helper so rows never break on tab matching:
  `python3 /Users/seungwonan/.agents/skills/shared/goal-plan/scripts/goal_log.py <add|start|done|block|drop|set|show> ...` (flags: `<cmd> --help`).
- This plan lives in this goal workspace; in worktree mode the workspace is on its own goal branch, and in dedicated-repo mode the repo itself is the ledger. Each loop step ends in a commit, so the commit history is the durable record. `goal_log.py done` stamps the current `HEAD` as the row checkpoint; the commit you make immediately after records the row update. The checkpoint plus git history is your resume point: on resume, recover state with `pwd`, `goal_log.py show`, `git log`, then re-run the Proof.
- Long runs can lose conversational context. What lets the loop survive is externalized state: `progress.tsv` and git hold the plan and checkpoints, and the Proof output stays in the transcript, so you resume cleanly with `goal_log.py show` + `git log`.

## Loop Protocol

Run as an autonomous loop until the Goal holds.

1. `goal_log.py show` to see where you are.
2. Take the next row (or `goal_log.py add --task ...`), then `goal_log.py start <id>`. Pick the row most likely to advance the Goal or unblock the rest, not just the next in line.
3. Do the work within Scope and Constraints, then run the Proof.
4. `goal_log.py done <id> --decision keep|discard|crash --artifact "<proof>"` (keep if it advanced the goal; discard if it did not but did no harm; crash if it caused a regression). Be skeptical of your own success -- if the Proof passed quietly, rerun it before marking done. Every done row needs artifact or notes evidence; for discard/crash, put WHY it failed and what to try instead in notes so a later session does not repeat the dead end. Use `goal_log.py drop <id> --notes "<reason>"` only for rows that are no longer needed.
5. Commit the changed files plus `progress.tsv`: `git add <files> progress.tsv && git commit -m "<keep|discard>: <what you did>"`. If target code changes live outside this repo, also commit the target repo or commit patch/snapshot artifacts here, according to Target Change Tracking.
6. Surface the Proof in your reply as evidence, not a claim: paste the actual command output (pass/fail, line numbers, exact errors), then name the checkpoint, what you verified this step, what remains, and whether you are blocked. The Proof must verify every Acceptance Criteria item. The `/goal` evaluator reads the conversation, not the files.
7. To undo a change that made things worse, revert with git, sparingly. Do not `git reset` away failed attempts you have already committed -- the commit log is the full record, including discards.

Do not ask "should I continue?" once the loop starts. Stop only when the Goal holds, when you hit a Bound, or when blocked by missing access, destructive risk, or an explicit user choice -- when blocked, report what specific input or access would unblock you. Budget or turn exhaustion is not completion.

## Completion

Complete only when the Goal holds and is shown with the Proof output in the conversation or logs, and required rows in `progress.tsv` are `done` or intentionally `dropped`.
