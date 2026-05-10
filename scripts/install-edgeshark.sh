#!/usr/bin/env bash
# install-edgeshark.sh — deploy EdgeShark on l3-mon-01.
#
# EdgeShark (Siemens, OSS): web UI for live packet capture on container
# interfaces. Click a container → click an interface → live tcpdump in
# the browser, optionally feed into Wireshark via the Cshargextcap helper.
#
# For the OTLab this is the curriculum-front-of-house tool: students
# watch real Modbus / DNP3 packets traverse the firewall conduit in
# real time. Pairs naturally with the Suricata IDS we'll add in V2.
#
# https://github.com/siemens/edgeshark
#
# Reachable at:
#   http://l3-mon-01:5001/
#   http://100.77.255.56:5001/  (tailscale)
#
# Idempotent — re-running pulls the latest images + restarts.
#
# Usage:
#   ./scripts/install-edgeshark.sh otadmin@l3-mon-01.local

set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@l3-mon-01.local}"
RUNTIME_USER=otuser
EDGESHARK_DIR="/home/${RUNTIME_USER}/edgeshark"

echo "==> deploying EdgeShark on $PI_HOST"

# ---------------------------------------------------------------------------
# 0. Ensure docker compose v2 is installed (Debian's docker.io ships
#    compose as a Suggests, not a Depends — bootstrap-l3-mon-role.sh
#    used --no-install-recommends, so it may be missing)
# ---------------------------------------------------------------------------
echo "==> ensuring docker-compose plugin is installed"
ssh "$PI_HOST" '
    if ! docker compose version >/dev/null 2>&1; then
        sudo apt-get install -y -qq docker-compose 2>&1 | tail -3
    fi
    docker compose version | head -1
'

# ---------------------------------------------------------------------------
# 1. Fetch Siemens' official compose file + stage on the Pi
#    (use the upstream compose verbatim so we automatically inherit any
#    image-name / cap / entrypoint changes the project makes)
# ---------------------------------------------------------------------------
echo "==> staging Siemens' official EdgeShark compose"
COMPOSE_URL="https://github.com/siemens/edgeshark/raw/main/deployments/wget/docker-compose.yaml"
ssh "$PI_HOST" "
    sudo -u ${RUNTIME_USER} mkdir -p ${EDGESHARK_DIR}
    sudo -u ${RUNTIME_USER} curl -fsSL -o ${EDGESHARK_DIR}/compose.yaml '${COMPOSE_URL}'
    sudo -u ${RUNTIME_USER} head -3 ${EDGESHARK_DIR}/compose.yaml
"

# ---------------------------------------------------------------------------
# 2. Bring the stack up — via 'sudo bash -s' so root does the cd into
#    the otuser-owned dir (otadmin can't traverse mode 700 /home/otuser)
# ---------------------------------------------------------------------------
echo "==> docker compose up -d (will pull ~200MB of images on first run)"
ssh "$PI_HOST" "sudo EDGESHARK_DIR=${EDGESHARK_DIR} bash -s" <<'COMPOSE_EOF'
set -e
cd "$EDGESHARK_DIR"
docker compose up -d 2>&1 | tail -15
COMPOSE_EOF

# ---------------------------------------------------------------------------
# 3. Wait + verify
# ---------------------------------------------------------------------------
echo "==> waiting ~10s for services to come up"
sleep 10
ssh "$PI_HOST" '
    echo "  containers (compose names them edgeshark-{service}-N):"
    sudo docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "edgeshark|NAMES" || sudo docker ps --format "table {{.Names}}\t{{.Status}}" | head -3
    echo
    echo "  GET http://localhost:5001/ :"
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5001/ || echo 000)
    echo "    HTTP $code"
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
 EdgeShark deployed.

 URL:   http://${HOST_BARE}:5001/
        http://100.77.255.56:5001/  (tailscale)

 What you can do:
   - Browse the topology — every netns + every container interface visible
   - Click an interface → live packet stream in browser
   - Click "Wireshark" button on a stream → opens the live capture in
     your local Wireshark via the Cshargextcap helper
     (https://github.com/siemens/cshargextcap)
   - Filter by container / by Linux network namespace
   - Watch the firewall container straddling dmz-br0 + pcn-br0 — see
     the Modbus packets traverse the conduit in real time

 Suggested first probe:
   1. Open EdgeShark, find clab-otlab-fw-dmz-pcn → eth2 (the PCN side)
   2. Click "Live" on eth2
   3. From the OTLab dashboard, run a Modbus write
   4. Watch the FC5/FC6 frame appear in the EdgeShark stream

 Logs:
   ssh ${PI_HOST} 'sudo docker logs edgeshark-ui'
   ssh ${PI_HOST} 'sudo docker logs edgeshark-gostwire'
   ssh ${PI_HOST} 'sudo docker logs edgeshark-packetflix'

 Stop / restart:
   cd ${EDGESHARK_DIR} && sudo docker compose down
   cd ${EDGESHARK_DIR} && sudo docker compose up -d
==============================================================================
EOF
