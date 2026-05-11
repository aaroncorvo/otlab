#!/usr/bin/env bash
# capture-screenshots.sh — drive Google Chrome on macOS to capture the
# dashboard's seven tabs as PNG files for the GitHub README.
#
# Requires:
#   - macOS
#   - Google Chrome installed
#   - You'll get prompted ONCE for the basic-auth creds; check "remember"
#     and the script handles the rest by clicking each tab in sequence
#
# Usage:
#   ./scripts/capture-screenshots.sh
#   ./scripts/capture-screenshots.sh https://l3-mon-01.local:8000
#
# Output: reference/screenshots/{overview,architecture,ids,firewall,dhcp,live-data,teaching}.png

set -euo pipefail

URL="${1:-https://l3-mon-01.local:8000}"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/reference/screenshots"
mkdir -p "$OUT_DIR"

TABS=(overview architecture ids firewall dhcp live-data teaching)

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This script is macOS-only. On Linux/Windows, capture screenshots manually." >&2
    exit 1
fi

echo "==> launching Chrome with the dashboard"
open -a "Google Chrome" --new --args --new-window "$URL"
echo "    A Chrome window should open. Log in as otlab / P@ssw0rd! when prompted."
echo "    Wait until the Overview tab is fully rendered, then press ENTER here."
read -r

for tab in "${TABS[@]}"; do
    echo "==> capturing $tab"
    osascript <<APPLESCRIPT
tell application "Google Chrome"
    activate
    set URL of active tab of window 1 to "$URL/#$tab"
end tell
APPLESCRIPT
    # Switch to the tab in JS so the data refreshes
    osascript <<APPLESCRIPT
tell application "Google Chrome"
    execute active tab of window 1 javascript "document.querySelector('.tab-btn[data-tab=\\\"$tab\\\"]')?.click()"
end tell
APPLESCRIPT
    # Wait for tab to render + data to refresh
    sleep 6
    # screencapture -l <window-id> -t png <output>
    WID=$(osascript -e 'tell app "Google Chrome" to return id of window 1')
    screencapture -l "$WID" -t png -x "$OUT_DIR/$tab.png"
    echo "    saved $OUT_DIR/$tab.png"
done

echo
echo "==> done. Captures in $OUT_DIR/"
ls -la "$OUT_DIR/"
