#!/usr/bin/env bash
# install-qwiic.sh — deploy the Qwiic physical-I/O control service to the
# teacher Pi. Relay (0x18) + TMP117 (0x48) + motor driver (0x5d) on I2C
# bus 1; control page + REST API on :8090.
#
# Usage:
#   ./teacher/qwiic/install-qwiic.sh otadmin@10.20.30.27
#
# Reachable at: http://<pi>:8090/   (login otlab / P@ssw0rd!)
set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@10.20.30.27}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> staging Qwiic files to $PI_HOST"
rsync -a "$HERE/otlab-qwiic.py" "$HERE/otlab-qwiic.service" \
    "$PI_HOST:/tmp/otlab-qwiic-stage/"

echo "==> installing on $PI_HOST (needs sudo)"
ssh "$PI_HOST" 'sudo bash -s' <<'REMOTE'
set -e
# deps: flask + smbus2. Prefer apt (Pi images have no pip3 by default),
# fall back to pip with --break-system-packages if the apt pkg is absent.
python3 -c "import flask"  2>/dev/null || apt-get install -y -qq python3-flask 2>&1 | tail -2 || \
    pip3 install --break-system-packages flask 2>&1 | tail -2
python3 -c "import smbus2" 2>/dev/null || apt-get install -y -qq python3-smbus2 2>&1 | tail -2 || \
    pip3 install --break-system-packages smbus2 2>&1 | tail -2

install -d /opt/otlab
install -m 0755 /tmp/otlab-qwiic-stage/otlab-qwiic.py /opt/otlab/otlab-qwiic.py
install -m 0644 /tmp/otlab-qwiic-stage/otlab-qwiic.service \
    /etc/systemd/system/otlab-qwiic.service
systemctl daemon-reload
systemctl enable --now otlab-qwiic.service
rm -rf /tmp/otlab-qwiic-stage

echo
systemctl --no-pager --lines=5 status otlab-qwiic.service 2>&1 | head -10
REMOTE

echo
echo "==> done. Control page: http://${PI_HOST#*@}:8090/  (otlab / P@ssw0rd!)"
echo "    logs: ssh $PI_HOST 'journalctl -u otlab-qwiic -f'"
