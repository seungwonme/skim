#!/bin/bash
# Build and install Skim.app from the SwiftPM desktop target.
# Usage: scripts/build-app.sh [install directory, default /Applications]
set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="Skim"
PRODUCT_NAME="SkimDesktop"
BUNDLE_ID="dev.aidenahn.skim"
DEST="${1:-/Applications}"
PACKAGE_PATH="apps/desktop"
ICON_PNG="$PACKAGE_PATH/Sources/SkimDesktopApp/Resources/SkimIcon.png"

echo ">> release build"
swift build --package-path "$PACKAGE_PATH" -c release
BIN_DIR="$(swift build --package-path "$PACKAGE_PATH" -c release --show-bin-path)"
BIN="$BIN_DIR/$PRODUCT_NAME"

APP="$DEST/$APP_NAME.app"
mkdir -p "$DEST"

osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1 || true
for _ in {1..20}; do
    pgrep -x "$APP_NAME" >/dev/null 2>&1 || break
    sleep 0.2
done

if [[ "$APP" == *"/$APP_NAME.app" && -d "$APP" ]]; then
    /bin/rm -rf -- "$APP"
fi
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$APP_NAME"
cp "$ICON_PNG" "$APP/Contents/Resources/SkimIcon.png"

ICON_TMP="$(mktemp -d "${TMPDIR:-/tmp}/skim-icon.XXXXXX")"
trap 'rm -rf "$ICON_TMP"' EXIT
ICONSET="$ICON_TMP/SkimIcon.iconset"
mkdir -p "$ICONSET"

make_icon() {
    sips -z "$1" "$1" "$ICON_PNG" --out "$ICONSET/$2" >/dev/null
}

make_icon 16 icon_16x16.png
make_icon 32 icon_16x16@2x.png
make_icon 32 icon_32x32.png
make_icon 64 icon_32x32@2x.png
make_icon 128 icon_128x128.png
make_icon 256 icon_128x128@2x.png
make_icon 256 icon_256x256.png
make_icon 512 icon_256x256@2x.png
make_icon 512 icon_512x512.png
cp "$ICON_PNG" "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/SkimIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>Skim</string>
    <key>CFBundleIconFile</key><string>SkimIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null

IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
    | awk -F'"' '/Apple Development/{print $2; exit}')
if [[ -n "${IDENTITY}" ]]; then
    echo ">> sign: $IDENTITY"
    codesign --force --sign "$IDENTITY" "$APP"
else
    echo ">> sign: ad-hoc"
    codesign --force --sign - "$APP"
fi
codesign --verify "$APP"

echo "installed: $APP"
