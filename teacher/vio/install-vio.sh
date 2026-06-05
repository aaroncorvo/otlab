#!/usr/bin/env bash
# install-vio.sh — deploy the virtual I/O backend (simulated turbine) to a
# Pi. Lets the ladder PLC run with no physical Qwiic hardware. Installs the
# service but does NOT start it (so it won't disturb a running otlab-qwiic);
# use switch-io.sh to select virtual vs physical mode.
#
# Usage:
#   ./teacher/vio/install-vio.sh otadmin@10.20.30.49
#   ./teacher/vio/switch-io.sh   otadmin@10.20.30.49 virtual
set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@10.20.30.49}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> staging virtual-I/O files to $PI_HOST"
ssh "$PI_HOST" 'mkdir -p /tmp/otlab-vio-stage'
rsync -a "$HERE/otlab_vio.py" "$HERE/otlab-vio.service" \
    "$PI_HOST:/tmp/otlab-vio-stage/"

echo "==> installing on $PI_HOST"
ssh "$PI_HOST" 'sudo bash -s' <<'REMOTE'
set -e
python3 -c "import flask" 2>/dev/null || apt-get install -y -qq python3-flask 2>&1 | tail -2
install -d /opt/otlab
install -m 0755 /tmp/otlab-vio-stage/otlab_vio.py /opt/otlab/otlab_vio.py
install -m 0644 /tmp/otlab-vio-stage/otlab-vio.service /etc/systemd/system/otlab-vio.service
systemctl daemon-reload
rm -rf /tmp/otlab-vio-stage
echo "installed (not started). otlab-qwiic still owns :8090 unless you switch."
REMOTE

echo
echo "==> done. To run the simulated turbine instead of physical hardware:"
echo "     ./teacher/vio/switch-io.sh ${PI_HOST} virtual"
echo "    To switch back when the Qwiic kit arrives:"
echo "     ./teacher/vio/switch-io.sh ${PI_HOST} physical"
