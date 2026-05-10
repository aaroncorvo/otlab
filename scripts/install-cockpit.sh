#!/usr/bin/env bash
# install-cockpit.sh — deploy Cockpit (Red Hat's web admin UI) on l3-mon-01.
#
# Cockpit is the back-of-house Linux admin surface — services, networking,
# storage, journal, terminal, package manager. Useful when SSH isn't
# convenient. Doesn't know about ContainerLab specifically (no upstream
# Cockpit plugin for clab exists today; srl-labs ships clab-ui [v.0.x,
# 1 star] and clab-api-server but not a Cockpit module). For Docker
# container management see scripts/install-portainer.sh; for packet
# capture see scripts/install-edgeshark.sh.
#
# Reachable at:
#   https://l3-mon-01:9090/
#   https://100.77.255.56:9090/  (tailscale)
#
# Login: any local Linux user (otadmin recommended). Sets otadmin's PAM
# password to the lab convention 'P@ssw0rd!' if not already set.
#
# Idempotent — re-running just refreshes apt + restarts the socket.
#
# Usage:
#   ./scripts/install-cockpit.sh otadmin@l3-mon-01.local

set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@l3-mon-01.local}"

echo "==> deploying Cockpit on $PI_HOST"

# ---------------------------------------------------------------------------
# 1. apt: cockpit base + commonly useful plugins
# ---------------------------------------------------------------------------
echo "==> apt install cockpit + plugins (~3 min first run)"
ssh "$PI_HOST" '
    set -e
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        cockpit cockpit-bridge cockpit-system cockpit-networkmanager \
        cockpit-storaged cockpit-packagekit
'

# ---------------------------------------------------------------------------
# 2. Enable + start cockpit
# ---------------------------------------------------------------------------
echo "==> enabling cockpit.socket"
ssh "$PI_HOST" '
    sudo systemctl enable --now cockpit.socket
    sudo systemctl status cockpit.socket --no-pager 2>&1 | head -8
'

# ---------------------------------------------------------------------------
# 3. Set otadmin's PAM password (lab convention) so Cockpit login works
# ---------------------------------------------------------------------------
echo "==> ensuring otadmin has the lab-convention PAM password"
ssh "$PI_HOST" '
    if id otadmin >/dev/null 2>&1; then
        echo "otadmin:P@ssw0rd!" | sudo chpasswd
        echo "    otadmin password set (lab convention; rotate per DEF CON event)"
    fi
'

# ---------------------------------------------------------------------------
# 4. Stamp bootstrap-info
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

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
HOST_BARE="${PI_HOST##*@}"
cat <<EOF

==============================================================================
 Cockpit deployed.

 URL:    https://${HOST_BARE}:9090/
         https://l3-mon-01:9090/      (when mDNS catches up)
         https://100.77.255.56:9090/  (tailscale)

 Login:  otadmin / P@ssw0rd!  (lab convention — rotate per DEF CON event)

 Tabs you'll find in the left nav:
   - Overview, System, Logs, Networking, Storage, Services, Terminal
   - Software updates (cockpit-packagekit)

 First TLS visit will warn about the self-signed cert (Cockpit generates
 it on first start). Accept the cert; subsequent visits are clean.

 ContainerLab management:
   No first-class Cockpit plugin exists today. Two paths:
     1. Use the Terminal tab in Cockpit + run 'sudo containerlab inspect/
        deploy/destroy' as you would on the CLI.
     2. Use Portainer (scripts/install-portainer.sh) for container-level
        docker management — clab containers are regular Docker, so they
        all show up.

 Logs:
   sudo journalctl -u cockpit -n 50
==============================================================================
EOF
