#!/usr/bin/env bash
# install-virtual-lab.sh — bootstrap the containerlab-based virtual fabric
# on l3-mon-01. Idempotent.
#
# What it does:
#   1. Installs containerlab (apt repo from srl-labs)
#   2. Builds the OTLab Docker images (sensor-sim, dnp3-outstation,
#      firewall, dashboard, openplc)
#   3. Pulls third-party images we use (none in V1; V2 will add Ignition,
#      Authentik, Guacamole, Suricata)
#   4. Deploys the topology
#   5. Verifies inter-zone connectivity + firewall policy
#   6. Stamps /etc/otlab-bootstrap-info
#
# Usage:
#   ./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
#
# Pre-reqs:
#   - bootstrap-pi.sh has run (Docker installed)
#   - bootstrap-l3-mon-role.sh has run (l3-mon-01 ready as monitoring host)
#   - Pi 5 16GB recommended (8GB works for V1; V2 needs 16GB)

set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@l3-mon-01.local}"
RUNTIME_USER=otuser
LAB_DIR="/home/${RUNTIME_USER}/lab"
VIRT_DIR="${LAB_DIR}/virtual"

echo "==> deploying virtual lab to $PI_HOST"

# ---------------------------------------------------------------------------
# 1. Stage repo's virtual/ tree onto the Pi
# ---------------------------------------------------------------------------
echo "==> rsyncing virtual/ + plc/ onto $PI_HOST (build context for Dockerfiles)"
ssh "$PI_HOST" "sudo -u ${RUNTIME_USER} mkdir -p ${LAB_DIR}/virtual ${LAB_DIR}/plc/scenarios"
rsync -a --delete virtual/  "${PI_HOST}:/tmp/otlab-virtual-stage/"
rsync -a --delete plc/      "${PI_HOST}:/tmp/otlab-plc-stage/"
rsync -a --delete dashboard/ "${PI_HOST}:/tmp/otlab-dashboard-stage/"

ssh "$PI_HOST" "
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/otlab-virtual-stage/   ${LAB_DIR}/virtual/
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/otlab-plc-stage/       ${LAB_DIR}/plc/
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/otlab-dashboard-stage/ ${LAB_DIR}/dashboard/
    rm -rf /tmp/otlab-virtual-stage /tmp/otlab-plc-stage /tmp/otlab-dashboard-stage
"

# ---------------------------------------------------------------------------
# 2. Install containerlab (idempotent — apt-pin'd version)
# ---------------------------------------------------------------------------
echo "==> ensuring containerlab is installed"
ssh "$PI_HOST" '
    set -e
    if ! command -v containerlab >/dev/null; then
        # Official one-liner from the project (verified TLS, GPG-signed apt repo)
        bash -c "$(curl -fsSL https://get.containerlab.dev)" -- -v 0.59.0
    fi
    containerlab version | head -3
'

# ---------------------------------------------------------------------------
# 3. Build OTLab Docker images (ARM64 native — slow first time, cached after)
#    Run as root via 'sudo bash -s' since otadmin can't traverse /home/otuser
#    (mode 700) but root can. All docker builds happen as root.
# ---------------------------------------------------------------------------
echo "==> building OTLab Docker images (this is the slow part — ~20-30 min first run)"
ssh "$PI_HOST" "sudo LAB_DIR=${LAB_DIR} bash -s" <<'BUILD_EOF'
set -e
cd "$LAB_DIR"

echo '    building otlab/sensor-sim:latest ...'
docker build -q -t otlab/sensor-sim:latest \
    -f virtual/dockerfiles/sensor-sim/Dockerfile . | tail -1

echo '    building otlab/dnp3-outstation:latest ...'
docker build -q -t otlab/dnp3-outstation:latest \
    -f virtual/dockerfiles/dnp3-outstation/Dockerfile . | tail -1

echo '    building otlab/firewall:latest ...'
docker build -q -t otlab/firewall:latest \
    -f virtual/dockerfiles/firewall/Dockerfile virtual/dockerfiles/firewall | tail -1

echo '    building otlab/dashboard:latest ...'
docker build -q -t otlab/dashboard:latest \
    -f virtual/dockerfiles/dashboard/Dockerfile . | tail -1

echo '    building otlab/openplc:latest (~15-20 min — matiec compile, only on first run; cached after) ...'
docker build -q -t otlab/openplc:latest \
    -f virtual/dockerfiles/openplc/Dockerfile . | tail -1

echo '    image inventory:'
docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}' | grep '^otlab/'
BUILD_EOF

# ---------------------------------------------------------------------------
# 4. Pre-create the zone bridges — containerlab's `kind: bridge` attaches
#    to existing host bridges; it doesn't create them. This is idempotent
#    (ip link add fails harmlessly if the bridge already exists, the `|| true`
#    swallows it).
# ---------------------------------------------------------------------------
echo "==> pre-creating dmz-br0 + pcn-br0 host bridges"
ssh "$PI_HOST" 'sudo bash -s' <<'BRIDGE_EOF'
set -e
for br in dmz-br0 pcn-br0; do
    if ! ip link show "$br" >/dev/null 2>&1; then
        ip link add "$br" type bridge
        ip link set "$br" up
        echo "    created $br"
    else
        echo "    $br already exists"
    fi
done
# Persist across reboots via a systemd unit (idempotent)
cat >/etc/systemd/system/otlab-bridges.service <<'UNIT_EOF'
[Unit]
Description=OTLab zone bridges (dmz-br0, pcn-br0) for ContainerLab
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/ip link add dmz-br0 type bridge
ExecStart=/sbin/ip link add pcn-br0 type bridge
ExecStart=/sbin/ip link set dmz-br0 up
ExecStart=/sbin/ip link set pcn-br0 up
ExecStartPost=-/bin/true
SuccessExitStatus=0 1 2

[Install]
WantedBy=multi-user.target
UNIT_EOF
systemctl daemon-reload
systemctl enable otlab-bridges.service >/dev/null 2>&1 || true
BRIDGE_EOF

# ---------------------------------------------------------------------------
# 5. Deploy the topology
# ---------------------------------------------------------------------------
echo "==> deploying containerlab topology"
ssh "$PI_HOST" "sudo LAB_DIR=${LAB_DIR} bash -s" <<'DEPLOY_EOF'
set -e
cd "$LAB_DIR/virtual"
# Destroy any existing deployment first (idempotent)
containerlab destroy -t topologies/otlab.clab.yaml --cleanup 2>/dev/null || true
containerlab deploy  -t topologies/otlab.clab.yaml
DEPLOY_EOF

# ---------------------------------------------------------------------------
# 5. Verify
# ---------------------------------------------------------------------------
echo "==> verifying topology + connectivity"
sleep 5
ssh "$PI_HOST" "
    set -e
    echo
    echo '── containers ──'
    sudo containerlab inspect -t ${LAB_DIR}/virtual/topologies/otlab.clab.yaml \
        --format table 2>&1 | head -25

    echo
    echo '── bridges ──'
    ip -br link show type bridge | grep -E 'br0|clab' || true

    echo
    echo '── firewall ruleset (top of FORWARD chain) ──'
    sudo docker exec clab-otlab-fw-dmz-pcn iptables -nvL FORWARD | head -10

    echo
    echo '── reachability test: dashboard from DMZ ──'
    sudo docker exec clab-otlab-fw-dmz-pcn ping -c1 -W2 192.168.75.40 \
        | tail -3 || echo '    (dashboard not pingable — may still be initializing)'

    echo
    echo '── reachability test: sensor-sim from PCN ──'
    sudo docker exec clab-otlab-fw-dmz-pcn ping -c1 -W2 10.20.30.70 \
        | tail -3 || echo '    (sensor-sim not pingable)'
"

# ---------------------------------------------------------------------------
# 6. Stamp /etc/otlab-bootstrap-info
# ---------------------------------------------------------------------------
COMMIT="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$(basename "$0")"
ssh "$PI_HOST" "
sudo tee /etc/otlab-bootstrap-info >/dev/null <<EOF
ts=$TS
commit=$COMMIT
script=$SCRIPT
EOF
sudo chmod 644 /etc/otlab-bootstrap-info
"

# ---------------------------------------------------------------------------
# 7. Summary
# ---------------------------------------------------------------------------
HOST_BARE="${PI_HOST##*@}"
cat <<EOF

==============================================================================
 OTLab virtual fabric deployed.

 Topology:
   dmz-br0  192.168.75.0/24   (L3.5 — Industrial DMZ)
     .1   firewall
     .40  dashboard       https://${HOST_BARE}:8000/

   pcn-br0  10.20.30.0/24    (L1/L2 — Process Control Network)
     .1   firewall
     .60  plc-1-virt       (OpenPLC #1 — master)    http://${HOST_BARE}:8081/
     .61  plc-2-virt       (OpenPLC #2 — outstation) http://${HOST_BARE}:8082/
     .70  sensor-sim       (Modbus :5020 + ctrl :5021)
     .71  dnp3-outstation  (DNP3 :20000)

 Manage:
   sudo containerlab inspect -t ${LAB_DIR}/virtual/topologies/otlab.clab.yaml
   sudo containerlab destroy -t ${LAB_DIR}/virtual/topologies/otlab.clab.yaml
   sudo containerlab deploy  -t ${LAB_DIR}/virtual/topologies/otlab.clab.yaml --reconfigure

 Container shells:
   sudo docker exec -it clab-otlab-fw-dmz-pcn bash
   sudo docker exec -it clab-otlab-sensor-sim bash
   sudo docker exec -it clab-otlab-plc-1-virt bash

 Firewall stats:
   sudo docker exec clab-otlab-fw-dmz-pcn iptables -nvL FORWARD --line-numbers

 Logs:
   sudo docker logs clab-otlab-dashboard
   sudo docker logs clab-otlab-fw-dmz-pcn

 Next steps:
   - V2: add Authentik + Ignition + Guacamole + Suricata to the topology,
         bring physical Pis (l1-plc-01, l1-hp-01) into pcn-br0 via macvlan
         on a USB NIC (eth1).
   - V3: add CODESYS Control SL + CODESYS Web HMI.
   See docs/virtualization.md for the full roadmap.
==============================================================================
EOF
