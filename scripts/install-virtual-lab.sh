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

echo '    building otlab/dhcp:latest ...'
docker build -q -t otlab/dhcp:latest \
    -f virtual/dockerfiles/dhcp/Dockerfile virtual/dockerfiles/dhcp | tail -1

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
# 4. Pre-create the zone bridges + bridge-port physical NICs into them.
#
#    ContainerLab's `kind: bridge` attaches to existing host bridges; it
#    doesn't create them. We also need physical NICs bridge-port'd into
#    the right zone so the virtual fabric extends out to real hardware:
#
#      eth0 -> dmz-br0   (DMZ extends to Netgear switch + GL-AR150 WAN)
#      eth1 -> pcn-br0   (USB NIC; physical Pis l1-plc-01, l1-hp-01)
#
#    This is idempotent: a helper script handles "already exists" + "NIC
#    not present" without erroring out, and is invoked both at install
#    time and at boot via systemd.
# ---------------------------------------------------------------------------
echo "==> setting up host bridges (dmz-br0, pcn-br0) + physical-NIC bridge-ports"
ssh "$PI_HOST" 'sudo bash -s' <<'BRIDGE_EOF'
set -e

# Helper — idempotent bridge + bridge-port setup. Lives at /usr/local/sbin
# so the systemd unit can invoke it without inlining shell.
install -m 0755 /dev/stdin /usr/local/sbin/otlab-bridges-up <<'HELPER_EOF'
#!/bin/sh
# otlab-bridges-up — idempotent bridge setup for OTLab.
#   - creates dmz-br0 + pcn-br0 if missing
#   - bridge-ports physical NICs into the right zone
#       eth0 -> dmz-br0  (DMZ extends to physical switch / WAN gateway)
#       eth1 -> pcn-br0  (USB NIC -> physical Pis on lab switch)
#   - safe to run repeatedly + safe if a NIC is absent
set -eu

bring_up_bridge() {
    br="$1"
    if ! ip link show "$br" >/dev/null 2>&1; then
        ip link add "$br" type bridge
        echo "  created bridge $br"
    fi
    ip link set "$br" up
}

attach_port() {
    nic="$1"
    br="$2"
    if ! ip link show "$nic" >/dev/null 2>&1; then
        echo "  skip: $nic not present (will attach on next boot if it appears)"
        return 0
    fi
    # Already attached to the right bridge?
    current_master=$(ip -o link show "$nic" | sed -n 's/.*master \([^ ]*\).*/\1/p')
    if [ "$current_master" = "$br" ]; then
        echo "  $nic already master $br"
    else
        # If attached to a different bridge, detach first.
        if [ -n "${current_master:-}" ]; then
            ip link set "$nic" nomaster
        fi
        ip link set "$nic" master "$br"
        echo "  attached $nic -> $br"
    fi
    ip link set "$nic" up
}

bring_up_bridge dmz-br0
bring_up_bridge pcn-br0
attach_port eth0 dmz-br0
attach_port eth1 pcn-br0
HELPER_EOF

# Run it now to set up the live system.
/usr/local/sbin/otlab-bridges-up

# Persist across reboots via a systemd unit. We use `network-pre.target`
# so this lands before NetworkManager / DHCP try to acquire on eth0/eth1
# directly (we want them to participate as bridge ports, not L3
# endpoints — the bridges are the L3 endpoints in the virtual fabric).
cat >/etc/systemd/system/otlab-bridges.service <<'UNIT_EOF'
[Unit]
Description=OTLab zone bridges (dmz-br0, pcn-br0) + physical NIC bridge-ports
After=network-pre.target
Wants=network-pre.target
Before=NetworkManager.service systemd-networkd.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/otlab-bridges-up

[Install]
WantedBy=multi-user.target
UNIT_EOF
systemctl daemon-reload
systemctl enable otlab-bridges.service >/dev/null 2>&1 || true
BRIDGE_EOF

# ---------------------------------------------------------------------------
# 4.5. Pin NetworkManager away from the lab fabric.
#
# Without this, NM enthusiastically runs DHCP on every clab-created veth
# AND on the bridge-port'd physical NICs (eth0, eth1). The host kernel
# ends up with a 10.20.30.x lease from dhcp-pcn that wrecks the default
# route — internet via the firewall container's no-uplink eth0 = drop.
#
# The lab fabric is managed entirely by /usr/local/sbin/otlab-bridges-up
# + containerlab; NM only manages wlan0 (real internet uplink + tailscale).
#
# Symptom this prevents: `ip route` shows
#   default via 10.20.30.1 dev dhcppcn proto dhcp src 10.20.30.x metric 100
# beating the wlan0 default route, after which docker pulls + tailscale +
# apt all die.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 4.4. Pre-create the shared-state files for inter-container communication.
#
# - /var/lib/otlab/mm-state/        modbus-master writes last.json here;
#                                   dashboard reads it RO
# - /var/lib/otlab/dhcp-{dmz,pcn}.leases   dnsmasq leases files; dashboard
#                                          reads them RO
#
# Docker bind-mounts behave: if the host path doesn't exist, Docker creates
# it as a DIRECTORY — even when the container side is a file. We need
# regular files for the leases (dnsmasq writes via rename) so we touch
# them up front. Idempotent.
# ---------------------------------------------------------------------------
echo "==> pre-creating /var/lib/otlab shared-state files"
ssh "$PI_HOST" 'sudo bash -s' <<'STATE_EOF'
set -e
mkdir -p /var/lib/otlab /var/lib/otlab/mm-state
touch /var/lib/otlab/dhcp-dmz.leases /var/lib/otlab/dhcp-pcn.leases
chmod 0644 /var/lib/otlab/dhcp-dmz.leases /var/lib/otlab/dhcp-pcn.leases
echo "    /var/lib/otlab/ ready"
ls -la /var/lib/otlab/ /var/lib/otlab/mm-state/ 2>&1 | head -8
STATE_EOF

echo "==> pinning NetworkManager away from clab fabric (wlan0-only)"
ssh "$PI_HOST" 'sudo bash -s' <<'NM_EOF'
set -e

cat >/etc/NetworkManager/conf.d/99-otlab-unmanaged.conf <<'CFG_EOF'
# OTLab — keep NetworkManager out of the lab fabric.
#
# The lab fabric (containerlab + Linux bridges + per-zone veths) is
# managed by /usr/local/sbin/otlab-bridges-up + clab itself, NOT by
# NetworkManager. Without this, NM eagerly runs DHCP on every clab-
# created veth -- including dhcp-pcn's bridge port -- and the host
# kernel acquires a 10.20.30.x lease that wrecks the default route.
[keyfile]
unmanaged-devices=interface-name:dmz-br0;interface-name:pcn-br0;interface-name:eth0;interface-name:eth1;interface-name:fw-*;interface-name:dhcp*;interface-name:plc*;interface-name:master;interface-name:sensorsim;interface-name:dnp3;interface-name:dashboard;interface-name:veth*
CFG_EOF

# The pre-bridge `netplan-eth0` connection profile (created by the
# default Pi OS netplan template) has no MAC pin and grabs every
# unmanaged ethernet device that comes up — so NM ends up running DHCP
# on the dhcppcn veth despite our unmanaged rules. Kill it; eth0's job
# is now to be a bridge port, not an L3 endpoint.
nmcli connection delete netplan-eth0 >/dev/null 2>&1 || true

systemctl restart NetworkManager
NM_EOF

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
