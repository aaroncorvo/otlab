#!/usr/bin/env bash
# install-sensor-sim.sh — push sensor-sim.py + the systemd unit to softplc-2
# and enable the service. Run from your laptop with the OTLab repo root as cwd.
#
# Service runs as otuser (lab's non-privileged user, created by
# scripts/bootstrap-users.sh). The script SCPs as the SSH user (otadmin),
# then sudo-installs to /home/otuser/lab/ with the right ownership.
#
# Idempotent — re-running just refreshes the file and restarts the service.

set -euo pipefail

PI_HOST="${1:-otadmin@RASPLC02.local}"   # softplc-2 via mDNS by default; pass user@host to override

SCRIPT_SRC="plc/sensor-sim.py"
SERVICE_SRC="plc/sensor-sim.service"
RUNTIME_USER="otuser"
RUNTIME_DIR="/home/${RUNTIME_USER}/lab"

echo "==> staging $SCRIPT_SRC + $SERVICE_SRC on $PI_HOST"
scp "$SCRIPT_SRC"  "$PI_HOST:/tmp/sensor-sim.py"
scp "$SERVICE_SRC" "$PI_HOST:/tmp/sensor-sim.service"

echo "==> installing sensor-sim.py to ${RUNTIME_DIR}/ (owned by ${RUNTIME_USER})"
ssh "$PI_HOST" "
    sudo -u ${RUNTIME_USER} mkdir -p ${RUNTIME_DIR}
    sudo install -m 0755 -o ${RUNTIME_USER} -g ${RUNTIME_USER} \
        /tmp/sensor-sim.py ${RUNTIME_DIR}/sensor-sim.py
    rm /tmp/sensor-sim.py
"

echo "==> installing systemd unit at /etc/systemd/system/sensor-sim.service"
ssh "$PI_HOST" '
    sudo install -m 0644 /tmp/sensor-sim.service /etc/systemd/system/sensor-sim.service
    rm /tmp/sensor-sim.service
'

echo "==> enabling + (re)starting service"
ssh "$PI_HOST" 'sudo systemctl daemon-reload && sudo systemctl enable --now sensor-sim && sudo systemctl restart sensor-sim'

sleep 2
echo "==> status"
ssh "$PI_HOST" 'systemctl status sensor-sim --no-pager | head -15'

echo
echo "==> recent journal"
ssh "$PI_HOST" 'journalctl -u sensor-sim -n 5 --no-pager'

echo
echo "Done. Probe from another lab host:"
echo "  source ~/lab/.venv-modern/bin/activate    # (otuser's venv)"
echo '  python3 -c "from pymodbus.client import ModbusTcpClient; c=ModbusTcpClient(\"10.20.30.49\",port=5020); c.connect(); print(c.read_holding_registers(0,4,device_id=0).registers); c.close()"'
