#!/usr/bin/env bash
# install-modbus.sh — deploy the Qwiic -> Modbus TCP bridge to a Pi that is
# already running the otlab-qwiic I/O service. Serves Modbus/TCP on :502.
#
# Usage:
#   ./teacher/modbus/install-modbus.sh otadmin@10.20.30.27
set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@10.20.30.27}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> staging Modbus bridge files to $PI_HOST"
ssh "$PI_HOST" 'mkdir -p /tmp/otlab-modbus-stage'
rsync -a "$HERE/otlab_modbus_io.py" "$HERE/otlab-modbus-io.service" \
    "$PI_HOST:/tmp/otlab-modbus-stage/"

echo "==> installing on $PI_HOST"
ssh "$PI_HOST" 'sudo bash -s' <<'REMOTE'
set -e
python3 -c "import pymodbus" 2>/dev/null || \
    apt-get install -y -qq python3-pymodbus 2>&1 | tail -2
install -d /opt/otlab
install -m 0755 /tmp/otlab-modbus-stage/otlab_modbus_io.py /opt/otlab/otlab_modbus_io.py
install -m 0644 /tmp/otlab-modbus-stage/otlab-modbus-io.service \
    /etc/systemd/system/otlab-modbus-io.service
systemctl daemon-reload
systemctl enable --now otlab-modbus-io.service
rm -rf /tmp/otlab-modbus-stage
sleep 2
systemctl --no-pager --lines=5 status otlab-modbus-io.service 2>&1 | head -9
REMOTE

echo
echo "==> done. Modbus TCP slave on ${PI_HOST#*@}:502"
echo "    Read holding registers 0..6 for live state (temp x10, relay, motors)."
echo "    Write HR10 (relay 0/1), HR11/HR12 (motor A/B -100..100) to actuate."
