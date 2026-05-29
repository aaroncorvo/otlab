#!/usr/bin/env bash
# flash-esp32.sh — compile an ESPHome YAML and flash it via USB onto an
# ESP32 attached to a Pi.
#
# Uses the official `esphome/esphome` Docker image so we don't need to
# pip-install esphome on every Pi. The container runs on the target Pi
# (where the ESP32 is plugged in via USB) so it can pass through the
# /dev/ttyUSB0 device directly.
#
# Usage:
#   ./scripts/flash-esp32.sh otadmin@<pi> <yaml-name>
# e.g.
#   ./scripts/flash-esp32.sh otadmin@otlab-teacher otlab-esp-teacher.yaml
#
# Env:
#   ESP_DEVICE   /dev/ttyUSB0   override if your ESP32 enumerates differently

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@otlab-teacher}"
YAML="${2:?YAML config name required, e.g. otlab-esp-teacher.yaml}"
ESP_DEVICE="${ESP_DEVICE:-/dev/ttyUSB0}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="/home/otadmin/teacher/esphome"

if [[ ! -f "$REPO_ROOT/teacher/esphome/$YAML" ]]; then
    echo "ERROR: $REPO_ROOT/teacher/esphome/$YAML not found" >&2
    exit 1
fi

echo "==> flashing $YAML to $ESP_DEVICE on $PI_HOST"

# Stage the latest YAML + secrets onto the Pi (in case repo changed)
echo "==> staging esphome configs (in case they changed)"
rsync -a --delete "$REPO_ROOT/teacher/esphome/" "$PI_HOST:/tmp/esphome-flash-stage/"
ssh "$PI_HOST" "
    sudo mkdir -p $REMOTE_DIR
    sudo rsync -a --chown=otadmin:otadmin /tmp/esphome-flash-stage/ $REMOTE_DIR/
    rm -rf /tmp/esphome-flash-stage
"

# Verify the device exists
ssh "$PI_HOST" "[ -c '$ESP_DEVICE' ]" || {
    echo "ERROR: $ESP_DEVICE not present on $PI_HOST. Is the ESP32 plugged in?" >&2
    ssh "$PI_HOST" 'ls -la /dev/ttyUSB* /dev/ttyACM* 2>&1' >&2
    exit 1
}

# Run esphome compile + upload in a one-shot container.
# - --device passes the serial port through
# - --network host so mDNS discovery works for any subsequent OTA
# - PIPESTATUS check so a compile/flash failure aborts the script
#   instead of silently letting the "done" message print
echo "==> compile + USB flash (~3-5 min first time, esphome image pulls + compiles)"
ssh "$PI_HOST" "
    set -o pipefail
    sudo docker run --rm \\
        --device $ESP_DEVICE \\
        -v $REMOTE_DIR:/config \\
        --network host \\
        esphome/esphome:latest \\
        run /config/$YAML --device $ESP_DEVICE --no-logs 2>&1 | tail -40
    exit \${PIPESTATUS[0]}
"

cat <<EOF

==> flash done. ESP32 will reboot, join WiFi, and become reachable in ~10s.

Verify:
  ssh $PI_HOST 'ping -c 3 ${YAML%.yaml}.local'   # mDNS
  curl http://${YAML%.yaml}.local/                # web UI
  curl http://${YAML%.yaml}.local/sensor/uptime   # REST sensor

If WiFi auth fails (wrong SSID/password), the device will broadcast a
fallback AP "${YAML%.yaml}-fallback" — connect a phone to it and
browse to http://192.168.4.1 to fix the config.
EOF
