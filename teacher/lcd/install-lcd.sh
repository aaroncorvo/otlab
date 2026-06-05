#!/usr/bin/env bash
# install-lcd.sh — deploy the SerLCD status display service to the
# teacher Pi (Cruiser carrier + CM5, Qwiic chain on /dev/i2c-2).
#
# What it does:
#   1. Ensures I2C is enabled (dtparam=i2c_arm=on + i2c-dev module)
#   2. Installs python3 smbus2
#   3. Copies otlab-lcd.py to /opt/otlab/
#   4. Installs + enables otlab-lcd.service
#
# Usage:
#   ./teacher/lcd/install-lcd.sh otadmin@10.20.30.27
#
# The LCD shows the hostname + lab IP, then the Tailscale IP, rotating
# every 4 seconds. Edit the Environment= lines in otlab-lcd.service to
# change bus/address/interface/cadence.
set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@10.20.30.27}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> staging LCD files to $PI_HOST"
rsync -a "$HERE/otlab-lcd.py" "$HERE/otlab-lcd.service" \
    "$PI_HOST:/tmp/otlab-lcd-stage/"

echo "==> installing on $PI_HOST (needs sudo)"
ssh "$PI_HOST" 'sudo bash -s' <<'REMOTE'
set -e

# 1. I2C enable (idempotent)
CFG=/boot/firmware/config.txt
if ! grep -qE "^dtparam=i2c_arm=on" "$CFG"; then
    echo "dtparam=i2c_arm=on" >> "$CFG"
    echo "  added dtparam=i2c_arm=on (reboot needed if /dev/i2c-* is absent)"
fi
modprobe i2c-dev 2>/dev/null || true
echo "i2c-dev" > /etc/modules-load.d/i2c-dev.conf

# 2. smbus2
python3 -c "import smbus2" 2>/dev/null || \
    pip3 install --break-system-packages smbus2 2>&1 | tail -2

# 3. install the driver
install -d /opt/otlab
install -m 0755 /tmp/otlab-lcd-stage/otlab-lcd.py /opt/otlab/otlab-lcd.py

# 4. install + enable the service
install -m 0644 /tmp/otlab-lcd-stage/otlab-lcd.service \
    /etc/systemd/system/otlab-lcd.service
systemctl daemon-reload
systemctl enable --now otlab-lcd.service
rm -rf /tmp/otlab-lcd-stage

echo
echo "==> service status:"
systemctl --no-pager --lines=5 status otlab-lcd.service 2>&1 | head -12
REMOTE

echo
echo "==> done. LCD should be cycling hostname/IP <-> Tailscale."
echo "    logs:    ssh $PI_HOST 'journalctl -u otlab-lcd -f'"
echo "    restart: ssh $PI_HOST 'sudo systemctl restart otlab-lcd'"
