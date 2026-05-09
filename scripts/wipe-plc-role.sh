#!/usr/bin/env bash
# wipe-plc-role.sh — DESTRUCTIVE. Strip OpenPLC + lab services from a Pi
# so it can be re-provisioned in a different role (e.g. l3-mon-01 → l3-mon-01).
#
# What it removes:
#   - openplc systemd unit + binaries (apt purge if present, or ~/OpenPLC_v3 dir)
#   - otlab-sensor-sim / otlab-dnp3-outstation / sensor-sim / dnp3-outstation
#     systemd units (stop, disable, delete unit files)
#   - /home/otuser/lab/{sensor-sim.py, dnp3-outstation.py, scenarios/, tests/}
#     (the lab dir itself + venv stay — re-used by the new role)
#   - /etc/otlab-bootstrap-info  (gets re-stamped by next bootstrap)
#
# What it leaves alone:
#   - otadmin / otuser accounts + SSH keys
#   - tailscale identity (so the Pi keeps its tailnet membership)
#   - lab venv at /home/otuser/lab/.venv-modern/  (re-used by next role)
#   - dashboard/ if present (next bootstrap may want it; explicit if unwanted)
#   - hostname (rename via hostnamectl separately if needed)
#
# Confirmation:
#   Asks before nuking. Pass --yes to skip the prompt (CI / scripted reuse).
#
# Idempotent — safe to re-run.
#
# Usage:
#   ./scripts/wipe-plc-role.sh otadmin@l3-mon-01.local           # interactive
#   ./scripts/wipe-plc-role.sh otadmin@l3-mon-01.local --yes     # no prompt
#   ./scripts/wipe-plc-role.sh otadmin@softplc-2.local --yes     # legacy alias still works

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@l3-mon-01.local}"
SKIP_PROMPT="${2:-}"

if [ "$SKIP_PROMPT" != "--yes" ]; then
    cat <<EOF
WARNING — destructive operation on $PI_HOST

This will:
  • stop + disable + remove openplc, sensor-sim, dnp3-outstation services
  • delete /home/otuser/lab/{sensor-sim.py, dnp3-outstation.py, scenarios/, tests/}
  • apt purge openplc-runtime (if installed via apt)
  • clear /etc/otlab-bootstrap-info

It will NOT touch:
  • otadmin / otuser accounts
  • tailscale identity
  • lab venv (/home/otuser/lab/.venv-modern/)
  • hostname

Proceed? Type 'yes' to continue.
EOF
    read -r REPLY
    if [ "$REPLY" != "yes" ]; then
        echo "aborted."
        exit 1
    fi
fi

echo "==> wiping PLC role on $PI_HOST"

ssh "$PI_HOST" 'bash -s' <<'REMOTE_EOF'
set -e

# 1. Stop + disable + remove our systemd units
for unit in otlab-sensor-sim sensor-sim otlab-dnp3-outstation dnp3-outstation; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${unit}\.service"; then
        sudo systemctl stop "${unit}.service" 2>/dev/null || true
        sudo systemctl disable "${unit}.service" 2>/dev/null || true
        sudo rm -f "/etc/systemd/system/${unit}.service"
        echo "    removed unit: ${unit}.service"
    fi
done

# 2. OpenPLC — handle both apt-package and source-built installs
if dpkg -l openplc-runtime 2>/dev/null | grep -q '^ii'; then
    sudo systemctl stop openplc 2>/dev/null || true
    sudo apt-get purge -y -qq openplc-runtime 2>/dev/null || true
    echo "    apt-purged openplc-runtime"
elif [ -d /home/otuser/OpenPLC_v3 ] || [ -d ~/OpenPLC_v3 ]; then
    sudo systemctl stop openplc 2>/dev/null || true
    sudo systemctl disable openplc 2>/dev/null || true
    sudo rm -f /etc/systemd/system/openplc.service
    sudo rm -rf /home/otuser/OpenPLC_v3 ~/OpenPLC_v3
    echo "    removed source-built OpenPLC + unit"
fi

sudo systemctl daemon-reload

# 3. Remove lab service files (keep .venv-modern + dashboard/)
sudo rm -f  /home/otuser/lab/sensor-sim.py
sudo rm -f  /home/otuser/lab/dnp3-outstation.py
sudo rm -rf /home/otuser/lab/scenarios
sudo rm -rf /home/otuser/lab/tests
echo "    removed sensor-sim.py, dnp3-outstation.py, scenarios/, tests/"

# 4. Clear bootstrap stamp so next role's bootstrap rewrites it cleanly
sudo rm -f /etc/otlab-bootstrap-info

# 5. Sanity report — what survived
echo
echo "    surviving lab dir contents:"
ls -la /home/otuser/lab/ 2>/dev/null | head -20 || echo "      (lab dir gone)"
echo
echo "    surviving otlab-* / sensor-sim / dnp3 units:"
systemctl list-unit-files 2>/dev/null | grep -E 'otlab-|sensor-sim|dnp3' || echo "      (none)"
REMOTE_EOF

cat <<EOF

==============================================================================
 PLC role stripped from $PI_HOST.

 Next steps depend on the new role:
   l3-mon-01 (monitoring host):
     ./scripts/bootstrap-l3-mon-role.sh $PI_HOST
     ./scripts/install-suricata.sh      $PI_HOST
     ./scripts/install-guacamole.sh     $PI_HOST
     ./scripts/install-dashboard.sh     $PI_HOST --target-host=l3-mon-01

   l1-plc-NN (PLC role, fresh):
     ./scripts/bootstrap-l1-plc-role.sh $PI_HOST l1-plc-02
     ./scripts/install-sensor-sim.sh    $PI_HOST
     ./scripts/install-dnp3.sh          $PI_HOST

 If you also want to rename the host, do it BEFORE the next bootstrap:
   ssh $PI_HOST 'sudo hostnamectl set-hostname l3-mon-01'
==============================================================================
EOF
