#!/usr/bin/env bash
# capture-screenshots.sh — drive Google Chrome on macOS to capture the
# dashboard's seven tabs as PNG files for the GitHub README.
#
# How it works:
#   - Opens Chrome at the dashboard URL
#   - You log in once + accept the self-signed cert
#   - Script cycles through tabs by setting the URL hash; the dashboard's
#     app.js listens for hashchange events and switches tabs accordingly
#   - macOS `screencapture` grabs each rendered tab as PNG
#
# macOS permission note:
#   screencapture -l <window-id> needs Terminal to have Screen Recording
#   permission (System Settings → Privacy & Security → Screen Recording).
#   This script defaults to -R <bounds> (region-based capture) which uses
#   the window's geometry instead — usually works without that prompt.
#   If you want the cleaner per-window capture, grant Screen Recording
#   permission to Terminal/iTerm and pass --window as an arg.
#
# Usage:
#   ./scripts/capture-screenshots.sh
#   ./scripts/capture-screenshots.sh https://192.168.120.19:8000
#   ./scripts/capture-screenshots.sh --window                # use -l <wid>
#
# Output: reference/screenshots/{overview,architecture,ids,firewall,dhcp,live-data,teaching}.png

set -euo pipefail

USE_WINDOW=0
URL="https://l3-mon-01.local:8000"

for arg in "$@"; do
    case "$arg" in
        --window) USE_WINDOW=1 ;;
        http*)    URL="$arg" ;;
        *)        echo "Unknown arg: $arg" >&2; exit 1 ;;
    esac
done

OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/reference/screenshots"
mkdir -p "$OUT_DIR"

TABS=(overview architecture ids firewall dhcp live-data teaching)

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This script is macOS-only. On Linux/Windows, capture manually." >&2
    exit 1
fi

echo "==> opening Chrome at $URL"
open -a "Google Chrome" --new --args --new-window "$URL"
echo
echo "    Log in as otlab / P@ssw0rd!"
echo "    Accept the self-signed cert warning (Advanced → Proceed)"
echo "    Wait until the Overview tab is fully rendered (cards have data,"
echo "    synoptic is animated). Keep Chrome FRONTMOST during capture."
echo
read -r -p "    Then press ENTER here to start the capture: "

# Make Chrome the active app and pull window 1's bounds. AppleScript's
# `bounds` returns `{left, top, right, bottom}` — convert to screencapture's
# `-R x,y,w,h` format.
osascript -e 'tell application "Google Chrome" to activate'
sleep 1

read -r WL WT WR WB < <(osascript -e '
    tell application "Google Chrome"
        set b to bounds of window 1
        return (item 1 of b) & " " & (item 2 of b) & " " & (item 3 of b) & " " & (item 4 of b)
    end tell' | tr ',' ' ')

WW=$((WR - WL))
WH=$((WB - WT))
echo "    Chrome window bounds: ${WW}x${WH} at +${WL}+${WT}"

if [[ $USE_WINDOW -eq 1 ]]; then
    WID=$(osascript -e 'tell application "Google Chrome" to id of window 1')
    echo "    Using --window mode (Chrome window id $WID)"
fi

for tab in "${TABS[@]}"; do
    echo "==> switching to $tab"
    # Change the URL hash. The dashboard's app.js hashchange listener
    # switches tabs internally.
    osascript -e "tell application \"Google Chrome\" to set URL of active tab of window 1 to \"$URL/#tab=$tab\"" >/dev/null

    # Wait for the tab to render + data to refresh (IDS/Firewall/DHCP poll
    # every 5s and re-fetch on tab switch).
    sleep 6

    out="$OUT_DIR/$tab.png"
    if [[ $USE_WINDOW -eq 1 ]]; then
        screencapture -l "$WID" -t png -x "$out" 2>&1 \
            || { echo "    -l capture failed — falling back to -R for this tab"; \
                 screencapture -R "${WL},${WT},${WW},${WH}" -t png -x "$out"; }
    else
        screencapture -R "${WL},${WT},${WW},${WH}" -t png -x "$out"
    fi

    if [[ -s "$out" ]]; then
        echo "    saved $out ($(du -h "$out" | cut -f1))"
    else
        echo "    WARNING: $out is empty — make sure Chrome is the frontmost window"
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
