#!/usr/bin/env bash
# install-esphome-dashboard.sh — deploy ESPHome Dashboard on the teacher Pi.
#
# ESPHome Dashboard is the web UI for managing ESPHome-based devices:
#   - Edit YAML configs in-browser
#   - Compile + flash via USB or OTA
#   - Live log streaming
#   - Auto-discover ESP32s on the network via mDNS
#
# Container is named otlab-esphome to distinguish from other ESPHome
# instances. State (compiled binaries, secrets) lives in
# /home/otadmin/teacher/esphome/ — same directory as the YAML configs
# in the repo, so changes flow through.
#
# Reachable at http://otlab-teacher:6052 (login disabled by default for
# lab convenience; enable when running outside the lab).
#
# Idempotent — re-run after YAML edits to restart the container.
#
# Usage:
#   ./scripts/install-esphome-dashboard.sh otadmin@otlab-teacher

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@otlab-teacher}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="/home/otadmin/teacher/esphome"

echo "==> deploying ESPHome Dashboard on $PI_HOST"

# Stage YAML configs onto the Pi
echo "==> rsync teacher/esphome/ -> $REMOTE_DIR"
rsync -a --delete "$REPO_ROOT/teacher/esphome/" "$PI_HOST:/tmp/esphome-stage/"
ssh "$PI_HOST" "
    sudo mkdir -p $REMOTE_DIR
    sudo rsync -a --chown=otadmin:otadmin /tmp/esphome-stage/ $REMOTE_DIR/
    rm -rf /tmp/esphome-stage
"

# Run the dashboard
echo "==> (re)starting otlab-esphome container"
ssh "$PI_HOST" "
    sudo docker rm -f otlab-esphome 2>/dev/null || true
    sudo docker run -d \\
        --name otlab-esphome \\
        --restart=unless-stopped \\
        -p 6052:6052 \\
        -v $REMOTE_DIR:/config \\
        --log-opt max-size=10m --log-opt max-file=3 \\
        esphome/esphome:latest \\
        dashboard /config 2>&1 | tail -3
"

# Sanity check
echo "==> waiting for dashboard to come up..."
ssh "$PI_HOST" '
    for i in $(seq 1 30); do
        if curl -fsS http://localhost:6052/ -o /dev/null 2>/dev/null; then
            echo "    ready"
            break
        fi
        sleep 2
    done
    docker ps --filter name=otlab-esphome --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
'

cat <<EOF

==> done. ESPHome Dashboard live at http://otlab-teacher:6052

What to do next:
  1. Flash each ESP32 via its attached Pi:
       ./scripts/flash-esp32.sh otadmin@otlab-teacher otlab-esp-teacher.yaml
       ./scripts/flash-esp32.sh otadmin@<student> otlab-esp-student-NN.yaml
  2. After first USB flash, ESP32s join WiFi and become discoverable on
     the dashboard. All future updates flow via OTA.
  3. Add sensors by editing the YAML files. The dashboard auto-detects
     changes; click "INSTALL" to OTA the update.

Direct device URLs (after first flash + WiFi join):
  http://otlab-esp-teacher.local
  http://otlab-esp-student-01.local
  http://otlab-esp-student-02.local
EOF
