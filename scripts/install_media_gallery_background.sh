#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/Media Gallery"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/com.jm.media-gallery-indexer.plist"
NEW_LABEL="com.jm.media-gallery-indexer"
OLD_LABEL="com.jm.screenshot-gallery-sync"

mkdir -p "$SUPPORT_DIR" "$SUPPORT_DIR/cache/thumbs" "$LAUNCH_AGENTS_DIR"

cp "$PROJECT_DIR/scripts/sync_gallery_data.py" "$SUPPORT_DIR/sync_gallery_data.py"
if [ -f "$PROJECT_DIR/gallery-data.json" ] && [ ! -f "$SUPPORT_DIR/gallery-data.json" ]; then
  cp "$PROJECT_DIR/gallery-data.json" "$SUPPORT_DIR/gallery-data.json"
fi

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${NEW_LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>${SUPPORT_DIR}/sync_gallery_data.py</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${SUPPORT_DIR}</string>

  <key>WatchPaths</key>
  <array>
    <string>${HOME}/Desktop</string>
    <string>${HOME}/Pictures</string>
    <string>${HOME}/Downloads</string>
    <string>${HOME}/Movies</string>
    <string>/Volumes</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>StartOnMount</key>
  <true/>

  <key>StandardOutPath</key>
  <string>${SUPPORT_DIR}/indexer.log</string>

  <key>StandardErrorPath</key>
  <string>${SUPPORT_DIR}/indexer-error.log</string>
</dict>
</plist>
PLIST

/usr/bin/python3 "$SUPPORT_DIR/sync_gallery_data.py"

launchctl bootout "gui/$(id -u)/$OLD_LABEL" >/dev/null 2>&1 || true
rm -f "$LAUNCH_AGENTS_DIR/com.jm.screenshot-gallery-sync.plist"

launchctl bootout "gui/$(id -u)/$NEW_LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/$NEW_LABEL"

echo "Installed media gallery background indexer."
