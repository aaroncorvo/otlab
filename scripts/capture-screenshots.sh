#!/usr/bin/env bash
# capture-screenshots.sh — drive Google Chrome on macOS to capture the
# dashboard's seven tabs as PNG files for the GitHub README.
#
# How it works:
#   - Opens Chrome at https://l3-mon-01.local:8000
#   - You log in once (otlab / P@ssw0rd!) + accept the self-signed cert
#   - Script cycles through tabs by setting the URL hash; the dashboard's
#     app.js listens for hashchange events and switches tabs accordingly
#     (no AppleScript JS injection — that route is brittle)
#   - macOS `screencapture -l <window-id>` grabs each rendered tab as PNG
#
# Usage:
#   ./scripts/capture-screenshots.sh
#   ./scripts/capture-screenshots.sh https://l3-mon-01.local:8000
#   ./scripts/capture-screenshots.sh https://192.168.120.19:8000   # if mDNS doesn't resolve
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

echo "==> opening Chrome at $URL"
open -a "Google Chrome" --new --args --new-window "$URL"
echo
echo "    Log in as otlab / P@ssw0rd!"
echo "    Accept the self-signed cert warning (Advanced → Proceed)"
echo "    Wait until the Overview tab is fully rendered (cards have data,"
echo "    synoptic is animated)."
echo
read -r -p "    Then press ENTER here to start the capture: "

for tab in "${TABS[@]}"; do
    echo "==> switching to $tab"
    # Set the URL hash. The dashboard's app.js listens for hashchange and
    # switches tabs accordingly. No JS injection needed.
    osascript -e "tell application \"Google Chrome\" to activate" \
              -e "tell application \"Google Chrome\" to set URL of active tab of window 1 to \"$URL/#tab=$tab\""

    # Wait for the tab to render + its data fetch to complete.
    # IDS / Firewall / DHCP poll every 5s with their own initial fetch
    # on tab-switch, so 6s is enough headroom.
    sleep 6

    # Get the front Chrome window's ID, then capture just that window
    # (so we don't accidentally include menu bar or other windows).
    WID=$(osascript -e 'tell application "Google Chrome" to id of window 1')

    out="$OUT_DIR/$tab.png"
    screencapture -l "$WID" -t png -x "$out"
    if [[ -s "$out" ]]; then
        echo "    saved $out ($(du -h "$out" | cut -f1))"
    else
        echo "    WARNING: $out is empty — Chrome may not have been frontmost"
    fi
done

echo
echo "==> done. Captures:"
ls -lh "$OUT_DIR/"*.png 2>&1

cat <<EOF

Next: record the demo GIF per reference/screenshots/README.md, then:
    git add reference/screenshots/
    git commit -m "screenshots: capture dashboard tabs + demo GIF"
    git push
EOF
