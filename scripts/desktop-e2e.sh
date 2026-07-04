#!/bin/sh
# Desktop e2e smoke: 실제 바이너리를 fixture 워크스페이스로 부팅시켜 검증한다.
# 1) SkimDesktopSmoke가 빈 fixture에 스키마를 만들고 0건을 읽는지
# 2) sqlite3로 시드한 포스트 1건을 같은 바이너리가 읽어내는지
# 3) SkimDesktop 앱이 fixture 워크스페이스로 부팅해 죽지 않는지
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$(swift build --package-path "$ROOT/apps/desktop" --show-bin-path)"
swift build --package-path "$ROOT/apps/desktop"

FIXTURE="$(mktemp -d)"
APP_BUNDLE_DIR="$(mktemp -d)"
APP_PID=""
cleanup() {
    [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null || true
    [ -n "$FIXTURE" ] && /bin/rm -rf -- "$FIXTURE"
    [ -n "$APP_BUNDLE_DIR" ] && /bin/rm -rf -- "$APP_BUNDLE_DIR"
}
trap cleanup EXIT

export SKIM_WORKSPACE_ROOT="$FIXTURE"
mkdir -p "$FIXTURE/data"

echo "== smoke: empty fixture =="
"$BIN/SkimDesktopSmoke" | tee /dev/stderr | grep -q "recent_posts=0"

echo "== seed one post =="
sqlite3 "$FIXTURE/data/skim.db" "INSERT INTO posts (platform, external_id, author, title, content, content_markdown, url, timestamp)
VALUES ('hackernews', 'e2e-1', 'e2e', 'E2E fixture post', 'body', '# E2E fixture post', 'https://example.com', datetime('now'));"

echo "== smoke: seeded fixture =="
"$BIN/SkimDesktopSmoke" | tee /dev/stderr | grep -q "recent_posts=1"

echo "== app bundle install smoke =="
"$ROOT/scripts/build-app.sh" "$APP_BUNDLE_DIR"
test -x "$APP_BUNDLE_DIR/Skim.app/Contents/MacOS/Skim"
test -f "$APP_BUNDLE_DIR/Skim.app/Contents/Resources/SkimIcon.icns"
plutil -extract CFBundleIdentifier raw "$APP_BUNDLE_DIR/Skim.app/Contents/Info.plist" | grep -q "dev.aidenahn.skim"
codesign --verify "$APP_BUNDLE_DIR/Skim.app"

echo "== app boot =="
"$BIN/SkimDesktop" &
APP_PID=$!
sleep 5
kill -0 "$APP_PID" || { echo "FAIL: SkimDesktop exited within 5s"; exit 1; }
kill "$APP_PID"
wait "$APP_PID" 2>/dev/null || true
APP_PID=""

echo "OK: desktop e2e passed"
