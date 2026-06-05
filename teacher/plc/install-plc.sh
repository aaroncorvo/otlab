#!/usr/bin/env bash
# install-plc.sh — deploy the ladder PLC engine to a Pi running the
# otlab-qwiic I/O service. Control UI + REST API on :8091.
#
# Usage:
#   ./teacher/plc/install-plc.sh otadmin@10.20.30.27
set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@10.20.30.27}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> staging PLC files to $PI_HOST"
rsync -a "$HERE/otlab_plc.py" "$HERE/otlab-plc.service" \
    "$PI_HOST:/tmp/otlab-plc-stage/"

echo "==> installing on $PI_HOST"
ssh "$PI_HOST" 'sudo bash -s' <<'REMOTE'
set -e
python3 -c "import flask" 2>/dev/null || \
    apt-get install -y -qq python3-flask 2>&1 | tail -2
install -d /opt/otlab /var/lib/otlab/plc
install -m 0755 /tmp/otlab-plc-stage/otlab_plc.py /opt/otlab/otlab_plc.py
install -m 0644 /tmp/otlab-plc-stage/otlab-plc.service \
    /etc/systemd/system/otlab-plc.service
systemctl daemon-reload
systemctl enable --now otlab-plc.service
rm -rf /tmp/otlab-plc-stage
sleep 2
systemctl --no-pager --lines=4 status otlab-plc.service 2>&1 | head -8
REMOTE

echo
echo "==> done. PLC UI: http://${PI_HOST#*@}:8091/  (otlab / P@ssw0rd!)"
echo "    Click Run to start the scan loop. The default demo spins the"
echo "    turbine at 28°C, full at 31°C, trips the relay at 33°C."
