#!/usr/bin/env bash
# install-sensor-sim.sh — push sensor-sim.py + the systemd unit to softplc-2
# and enable the service. Run from your laptop with the OTLab repo root as cwd.
#
# Idempotent — re-running just refreshes files and restarts the service.

set -euo pipefail

PI_HOST="${1:-otadmin@RASPLC02.local}"   # softplc-2 mgmt IP by default
SCRIPT_SRC="plc/sensor-sim.py"
SERVICE_SRC="plc/sensor-sim.service"

echo "==> deploying $SCRIPT_SRC to $PI_HOST:~/lab/"
ssh "$PI_HOST" 'mkdir -p ~/lab'
scp "$SCRIPT_SRC" "$PI_HOST:~/lab/sensor-sim.py"

echo "==> deploying $SERVICE_SRC to $PI_HOST:/etc/systemd/system/ (sudo)"
scp "$SERVICE_SRC" "$PI_HOST:/tmp/sensor-sim.service"
ssh "$PI_HOST" 'sudo install -m 0644 /tmp/sensor-sim.service /etc/systemd/system/sensor-sim.service && rm /tmp/sensor-sim.service'

echo "==> enabling + (re)starting service"
ssh "$PI_HOST" 'sudo systemctl daemon-reload && sudo systemctl enable --now sensor-sim && sudo systemctl restart sensor-sim'

sleep 2
echo "==> status"
ssh "$PI_HOST" 'systemctl status sensor-sim --no-pager | head -15'

echo
echo "==> tailing journal (Ctrl-C to stop)"
ssh "$PI_HOST" 'journalctl -u sensor-sim -n 5 --no-pager'

echo
echo "Done. Probe from another lab host:"
echo "  source ~/lab/.venv-modern/bin/activate"
echo "  python3 -c 'from pymodbus.client import ModbusTcpClient; c=ModbusTcpClient(\"10.20.30.49\",port=5020); c.connect(); print(c.read_holding_registers(0,4,device_id=0).registers); c.close()'"
