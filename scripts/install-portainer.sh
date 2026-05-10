#!/usr/bin/env bash
# install-portainer.sh — deploy Portainer CE on l3-mon-01.
#
# Portainer = full-featured web UI for Docker (containers, images, networks,
# volumes, stacks, exec/logs/inspect). The right tool for managing the
# ContainerLab containers (which are regular Docker containers underneath).
#
# This is admin-back-of-house — operators who want to click through
# containers, view logs, exec a shell, restart things, see resource
# usage. Doesn't know about the ContainerLab YAML specifically (no
# topology view), but everything ContainerLab spawns shows up.
#
# Reachable at:
#   https://l3-mon-01:9443/
#   https://100.77.255.56:9443/  (tailscale)
#
# First-time setup: visit the URL, create an admin account in the first
# 5 minutes (Portainer security feature — it locks itself if you don't).
#
# Idempotent — re-running just pulls latest image + restarts the container.
#
# Usage:
#   ./scripts/install-portainer.sh otadmin@l3-mon-01.local

set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@l3-mon-01.local}"

echo "==> deploying Portainer CE on $PI_HOST"

# ---------------------------------------------------------------------------
# 1. Create persistent volume + run container
# ---------------------------------------------------------------------------
echo "==> creating portainer_data volume + starting Portainer"
ssh "$PI_HOST" '
    set -e
    # Persistent volume for Portainer state (admin account, settings)
    sudo docker volume create portainer_data >/dev/null 2>&1 || true

    # Stop + remove any existing container (idempotent re-run)
    sudo docker rm -f portainer 2>/dev/null || true

    # Pull + run latest community edition for ARM64
    sudo docker run -d \
        --name portainer \
        --restart=unless-stopped \
        -p 9443:9443 \
        -p 8000:8000 \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v portainer_data:/data \
        portainer/portainer-ce:latest 2>&1 | tail -3

    # The dashboard already binds to host:8000 — Portainer also wants 8000
    # for its tunnel feature (Edge Compute). We map it anyway; the conflict
    # only matters if both bind 0.0.0.0:8000. Dashboard runs in clab netns
    # with hostport 8000 → container 8000, so the host port 8000 is taken
    # by the dashboard. Portainer 8000 is just for Edge agents (optional).
'

# ---------------------------------------------------------------------------
# Note: port 8000 conflict with the dashboard. Detect + warn.
# ---------------------------------------------------------------------------
echo "==> checking for port 8000 conflict (dashboard already binds it)"
ssh "$PI_HOST" '
    sleep 4
    if sudo docker ps --filter name=portainer --format "{{.Status}}" | grep -q "Restarting"; then
        echo "    Portainer restart-loop detected — likely the :8000 conflict with the dashboard."
        echo "    Recreating without the :8000 port mapping (Edge feature unused)."
        sudo docker rm -f portainer
        sudo docker run -d \
            --name portainer \
            --restart=unless-stopped \
            -p 9443:9443 \
            -v /var/run/docker.sock:/var/run/docker.sock \
            -v portainer_data:/data \
            portainer/portainer-ce:latest 2>&1 | tail -3
    fi
'

# ---------------------------------------------------------------------------
# 2. Wait + verify
# ---------------------------------------------------------------------------
echo "==> waiting 10s for startup"
sleep 10
ssh "$PI_HOST" '
    echo "  container:"
    sudo docker ps --filter "name=portainer" --format "    {{.Names}}\t{{.Status}}"
    echo
    echo "  GET / (HTTP 200 = healthy, will redirect to login):"
    code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 https://localhost:9443/ || echo 000)
    echo "    HTTP $code"
'

# ---------------------------------------------------------------------------
# 3. Stamp bootstrap-info
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
# 4. Summary
# ---------------------------------------------------------------------------
HOST_BARE="${PI_HOST##*@}"
cat <<EOF

==============================================================================
 Portainer CE deployed.

 URL:   https://${HOST_BARE}:9443/
        https://100.77.255.56:9443/  (tailscale)

 IMPORTANT — first visit (within 5 min, or Portainer locks itself):
   1. Open https://100.77.255.56:9443/ in a browser (accept self-signed
      cert warning)
   2. Create the initial admin account (any username + 12+ char password
      — the lab convention 'P@ssw0rd!' is too short, so pick something
      like 'OTLabAdmin2026!')
   3. Choose "Get Started" → use the local Docker environment

 Once in, you'll see all the ContainerLab containers (named clab-otlab-*)
 in the Containers list. Click any container to:
   - View live logs
   - Exec a shell inside it
   - Inspect resource usage
   - Stop / restart / remove

 Useful for ContainerLab workflows:
   - Watch live logs of clab-otlab-fw-dmz-pcn during attack walkthroughs
   - Exec into clab-otlab-sensor-sim to poke at scenarios manually
   - Restart a single container without 'clab redeploy'

 Note: Portainer doesn't know about the topology YAML — for that, use
 'sudo containerlab inspect' from a terminal (or Cockpit's Terminal tab).

 Logs:
   ssh ${PI_HOST} 'sudo docker logs portainer'
==============================================================================
EOF
