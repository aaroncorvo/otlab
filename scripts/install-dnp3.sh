#!/usr/bin/env bash
# install-dnp3.sh — push the DNP3 outstation + systemd unit to an L1 PLC
# (currently l1-plc-01; future l1-plc-02 once backfilled).
# Companion to install-sensor-sim.sh. Listens on TCP/20000 (DNP3 standard).
#
# Idempotent — re-run after edits.
#
# Usage:
#   ./scripts/install-dnp3.sh                                # default otadmin@RASPLC01.local
#   ./scripts/install-dnp3.sh otadmin@100.77.255.56          # via tailscale

set -euo pipefail
PI_HOST="${1:-otadmin@RASPLC01.local}"

SCRIPT_SRC="plc/dnp3-outstation.py"
SERVICE_SRC="plc/dnp3-outstation.service"
RUNTIME_USER="otuser"
RUNTIME_DIR="/home/${RUNTIME_USER}/lab"

echo "==> staging DNP3 outstation on $PI_HOST"
scp "$SCRIPT_SRC"  "$PI_HOST:/tmp/dnp3-outstation.py"
scp "$SERVICE_SRC" "$PI_HOST:/tmp/dnp3-outstation.service"

ssh "$PI_HOST" "
    sudo install -m 0755 -o ${RUNTIME_USER} -g ${RUNTIME_USER} \
        /tmp/dnp3-outstation.py ${RUNTIME_DIR}/dnp3-outstation.py
    sudo install -m 0644 /tmp/dnp3-outstation.service /etc/systemd/system/dnp3-outstation.service
    rm /tmp/dnp3-outstation.py /tmp/dnp3-outstation.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now dnp3-outstation
    sudo systemctl restart dnp3-outstation
"

sleep 2
echo "==> status"
ssh "$PI_HOST" 'sudo systemctl status dnp3-outstation --no-pager 2>&1 | head -10'
echo
echo "==> recent journal"
ssh "$PI_HOST" 'sudo journalctl -u dnp3-outstation -n 5 --no-pager'

echo
echo "Done. Probe from any lab host:"
echo "  nc -vz 10.20.30.47 20000      # TCP reachability"
echo "  nmap -p 20000 -sV 10.20.30.47 # service-version detection"
echo "  python3 plc/tests/test-dnp3-scan.py"
