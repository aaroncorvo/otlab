#!/usr/bin/env bash
# bootstrap-l3-mon-role.sh — provision a fresh Pi to be the OTLab L3 ops host.
# Companion to bootstrap-pi.sh (which provisions the L1 PLC role).
#
# Sets up:
#   - apt deps for Docker (Guacamole), Suricata, dashboard runtime
#   - lab Python venv at /home/otuser/lab/.venv-modern (matches l3-mon-01)
#   - cloud-init disable + wifi powersave + adm group (lab canonical posture)
#   - dashboard pre-reqs: nothing here yet (install-dashboard.sh handles them)
#   - tailscale (so the host joins the tailnet from first boot)
#   - bootstrap-info stamp
#
# Pre-req: bootstrap-users.sh has run (otadmin + otuser exist).
#
# Usage:
#   ./scripts/bootstrap-l3-mon-role.sh PI_HOST
#
# Args:
#   PI_HOST   user@host, e.g. otadmin@OPSHOST.local
#
# Run-time: ~10 minutes (mostly apt + Docker install).

set -euo pipefail

PI_HOST="${1:?PI_HOST required, e.g. otadmin@OPSHOST.local}"
RUNTIME_USER=otuser
RUNTIME_DIR="/home/${RUNTIME_USER}/lab"

echo "==> bootstrapping OTLab l3-mon-01 at $PI_HOST"

# Sanity check
ssh -o BatchMode=yes "$PI_HOST" 'sudo -n true 2>/dev/null' || {
    echo "ERROR: $PI_HOST needs passwordless sudo. Run bootstrap-users.sh first."
    exit 1
}

# ---------------------------------------------------------------------------
# 1. apt: base packages + Docker + Suricata + dashboard pre-reqs
# ---------------------------------------------------------------------------
echo "==> apt update + install (~5-8 min)"
ssh "$PI_HOST" '
    set -e
    sudo apt-get update -qq
    # Pre-seed wireshark-common debconf so the postinst creates the wireshark
    # group in non-interactive mode (same gotcha bootstrap-pi.sh handles).
    echo "wireshark-common wireshark-common/install-setuid boolean true" | \
        sudo debconf-set-selections
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        git vim htop tmux tcpdump wireshark-common net-tools \
        python3-pip python3-venv curl jq sqlite3 snmp \
        build-essential nmap iptables-persistent
    # Suricata — needs special handling on Bookworm where the package was
    # pulled from stable due to a security maintenance gap. Bookworm-backports
    # has it; Trixie has it in main. Tries main first, falls back to backports
    # on Bookworm only.
    if ! dpkg -l suricata 2>/dev/null | grep -q "^ii "; then
        if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq suricata 2>/dev/null; then
            echo "    suricata installed from main"
        elif [ "$(lsb_release -cs 2>/dev/null || awk -F= "/VERSION_CODENAME/ {print \$2}" /etc/os-release)" = "bookworm" ]; then
            echo "    suricata not in bookworm main — enabling bookworm-backports"
            echo "deb http://deb.debian.org/debian bookworm-backports main contrib non-free non-free-firmware" \
                | sudo tee /etc/apt/sources.list.d/bookworm-backports.list >/dev/null
            sudo apt-get update -qq
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -t bookworm-backports suricata
            echo "    suricata installed from bookworm-backports"
        else
            echo "    WARN: suricata install failed and no fallback available — continuing without it"
        fi
    fi
    # Docker engine (for Guacamole containerized deploy)
    if ! command -v docker >/dev/null; then
        sudo apt-get install -y -qq docker.io
    fi
    # Belt-and-suspenders: ensure wireshark group exists
    getent group wireshark >/dev/null || sudo groupadd --system wireshark
'

# ---------------------------------------------------------------------------
# 2. Disable cloud-init + wifi powersave (canonical posture)
# ---------------------------------------------------------------------------
echo "==> disabling cloud-init"
ssh "$PI_HOST" '
    if [ -d /etc/cloud ] && command -v cloud-init >/dev/null 2>&1; then
        sudo touch /etc/cloud/cloud-init.disabled
        for svc in cloud-init cloud-init-local cloud-config cloud-final; do
            sudo systemctl mask --quiet "$svc" 2>/dev/null || true
        done
    fi
'

echo "==> disabling wifi powersave (Pi 3 B+ mgmt-network reachability fix; harmless on Pi 5)"
ssh "$PI_HOST" '
    if ! command -v nmcli >/dev/null 2>&1; then exit 0; fi
    set -e
    for c in $(nmcli -t -f NAME,TYPE connection show | awk -F: "/:802-11-wireless\$/ {print \$1}"); do
        cur=$(nmcli -t -f 802-11-wireless.powersave connection show "$c" 2>/dev/null | cut -d: -f2 | xargs)
        if [ "$cur" != "disable" ]; then
            sudo nmcli connection modify "$c" wifi.powersave 2
        fi
    done
    sudo nmcli device reapply wlan0 2>/dev/null || true
'

# ---------------------------------------------------------------------------
# 3. Group memberships — adm (journalctl), docker, wireshark
# ---------------------------------------------------------------------------
echo "==> adding otadmin + otuser to adm/docker/wireshark groups"
ssh "$PI_HOST" '
    for u in otadmin otuser; do
        if id "$u" >/dev/null 2>&1; then
            sudo usermod -aG adm,docker,wireshark "$u"
        fi
    done
'

# ---------------------------------------------------------------------------
# 4. Lab Python venv (matches l3-mon-01's)
# ---------------------------------------------------------------------------
echo "==> creating ${RUNTIME_DIR}/.venv-modern with pymodbus + Flask + Flask-HTTPAuth + paho-mqtt"
ssh "$PI_HOST" "
    set -e
    sudo -u ${RUNTIME_USER} mkdir -p ${RUNTIME_DIR}
    if [ ! -d ${RUNTIME_DIR}/.venv-modern ]; then
        sudo -u ${RUNTIME_USER} python3 -m venv ${RUNTIME_DIR}/.venv-modern
    fi
    sudo -u ${RUNTIME_USER} ${RUNTIME_DIR}/.venv-modern/bin/pip install --quiet --upgrade pip
    sudo -u ${RUNTIME_USER} ${RUNTIME_DIR}/.venv-modern/bin/pip install --quiet \
        'pymodbus>=3' flask flask-httpauth paho-mqtt pyserial requests
"

# ---------------------------------------------------------------------------
# 5. Tailscale (matches the other 3 Pis)
# ---------------------------------------------------------------------------
echo "==> installing tailscale (will need manual auth — see post-deploy notes)"
ssh "$PI_HOST" '
    if ! command -v tailscale >/dev/null; then
        curl -fsSL https://tailscale.com/install.sh | sudo sh
        sudo systemctl enable --now tailscaled
    fi
'

# ---------------------------------------------------------------------------
# 6. Pre-create directories the dashboard + Suricata + Guacamole will need
# ---------------------------------------------------------------------------
echo "==> creating runtime directories"
ssh "$PI_HOST" "
    sudo -u ${RUNTIME_USER} mkdir -p ${RUNTIME_DIR}/dashboard ${RUNTIME_DIR}/dashboard/captures ${RUNTIME_DIR}/dashboard/.ssh-cm
    sudo -u ${RUNTIME_USER} chmod 750 ${RUNTIME_DIR}/dashboard/captures
    sudo -u ${RUNTIME_USER} chmod 700 ${RUNTIME_DIR}/dashboard/.ssh-cm
    sudo mkdir -p /var/log/suricata /var/lib/suricata
    sudo mkdir -p ${RUNTIME_DIR}/guacamole
"

# ---------------------------------------------------------------------------
# 7. Stamp bootstrap-info
# ---------------------------------------------------------------------------
COMMIT="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$(basename "$0")"
ssh "$PI_HOST" "
sudo tee /etc/otlab-bootstrap-info >/dev/null <<EOF
ts=$TS
commit=$COMMIT
script=$SCRIPT
role=l3-mon-01
EOF
sudo chmod 644 /etc/otlab-bootstrap-info
"

echo
echo "==> bootstrap complete on $PI_HOST"
echo
echo "Next:"
echo "  1. Authenticate tailscale:"
echo "       ssh $PI_HOST 'sudo tailscale up'"
echo "     Visit the printed URL, approve, then advertise routes:"
echo "       ssh $PI_HOST 'sudo tailscale set --advertise-routes=10.20.30.0/24,10.20.40.0/24'"
echo "     Approve in https://login.tailscale.com/admin/machines"
echo
echo "  2. Deploy services:"
echo "       ./scripts/install-suricata.sh  $PI_HOST"
echo "       ./scripts/install-guacamole.sh $PI_HOST"
echo "       ./scripts/install-dashboard.sh $PI_HOST --target-host=l3-mon-01"
echo
echo "  3. Apply L1-firewall policies on the PLC hosts (see docs/architecture-evolution.md)"
