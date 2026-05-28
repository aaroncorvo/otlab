#!/usr/bin/env bash
# install-virtual-lab.sh — bootstrap the containerlab-based virtual fabric
# on l3-mon-01. Idempotent end-to-end.
#
# What it does:
#   1. rsyncs virtual/, plc/, dashboard/ trees onto the Pi
#   2. Installs containerlab (official one-liner installer; pinned version)
#   3. Builds 7 OTLab Docker images:
#        sensor-sim, dnp3-outstation, modbus-master,
#        firewall (with DNS forwarder + state exporter),
#        dhcp (one image, two containers per-zone),
#        dashboard, openplc
#   4. Sets up host-side bridges + physical-NIC attach config:
#        /usr/local/sbin/otlab-bridges-up   (idempotent helper)
#        /etc/systemd/system/otlab-bridges.service  (boot-time apply)
#        /etc/otlab/bridge-attach.conf      (per-NIC attach decisions)
#   5. Pre-creates host-shared state dirs/files at /var/lib/otlab/
#      (mm-state/, fw-state/, dhcp-*.{leases,reservations,log})
#   6. Configures NetworkManager to ignore the lab fabric (otherwise
#      NM auto-DHCPs the host on every clab veth and steals the
#      default route)
#   7. containerlab destroy + deploy the topology (clean state)
#   8. Verifies bridges + container connectivity + firewall policy
#   9. Stamps /etc/otlab-bootstrap-info
#
# Usage:
#   ./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
#
# Pre-reqs:
#   - bootstrap-users.sh has run (otadmin + otuser exist)
#   - bootstrap-pi.sh has run (Docker installed, lab venv created)
#   - bootstrap-l3-mon-role.sh has run (l3-mon-01 deps + tailscale)
#   - Pi 5 16GB strongly recommended (8GB tight; ~7 GB working set)
#
# After install: see docs/setup-from-scratch.md for the full lab build
# (including the physical Pis + companion admin UIs + Suricata).

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
ssh "$PI_HOST" "sudo -u ${RUNTIME_USER} mkdir -p ${LAB_DIR}/virtual ${LAB_DIR}/plc/scenarios ${LAB_DIR}/scripts"
rsync -a --delete virtual/  "${PI_HOST}:/tmp/otlab-virtual-stage/"
rsync -a --delete plc/      "${PI_HOST}:/tmp/otlab-plc-stage/"
rsync -a --delete dashboard/ "${PI_HOST}:/tmp/otlab-dashboard-stage/"
# Just the topology render script (other scripts/ live on the laptop and
# run via SSH; this one needs to execute on the Pi before clab deploy)
rsync -a scripts/render-topology.sh "${PI_HOST}:/tmp/otlab-render-topology.sh"

ssh "$PI_HOST" "
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/otlab-virtual-stage/   ${LAB_DIR}/virtual/
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/otlab-plc-stage/       ${LAB_DIR}/plc/
    sudo rsync -a --delete --chown=${RUNTIME_USER}:${RUNTIME_USER} \
        /tmp/otlab-dashboard-stage/ ${LAB_DIR}/dashboard/
    sudo install -m 0755 -o ${RUNTIME_USER} -g ${RUNTIME_USER} \
        /tmp/otlab-render-topology.sh ${LAB_DIR}/scripts/render-topology.sh
    rm -rf /tmp/otlab-virtual-stage /tmp/otlab-plc-stage /tmp/otlab-dashboard-stage /tmp/otlab-render-topology.sh
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

echo '    building otlab/modbus-master:latest ...'
docker build -q -t otlab/modbus-master:latest \
    -f virtual/dockerfiles/modbus-master/Dockerfile . | tail -1

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
#   - bridge-ports physical NICs into the right zone, gated on
#     /etc/otlab/bridge-attach.conf so we don't accidentally
#     extend the virtual fabric onto a shared lab switch with no
#     VLAN isolation (which would leak DHCP from dhcp-pcn / dhcp-dmz
#     to unrelated devices on the wire).
#
# Per-NIC attach gating:
#   /etc/otlab/bridge-attach.conf, one line per NIC:
#       eth0=dmz-br0     # attach eth0 to dmz-br0 (extend DMZ to physical wire)
#       eth1=pcn-br0     # attach eth1 to pcn-br0 (USB NIC -> physical Pis)
#   To keep a NIC OUT of any bridge, comment the line:
#       # eth1=pcn-br0   # disabled until lab switch has VLAN isolation
#
# The default config (laid down on first install) is "everything on" to
# match the original V2.x setup. Edit it on the host to match your
# physical-network reality.
set -eu

CONF=/etc/otlab/bridge-attach.conf

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
        echo "  skip: $nic not present"
        return 0
    fi
    current_master=$(ip -o link show "$nic" | sed -n 's/.*master \([^ ]*\).*/\1/p')
    if [ "$current_master" = "$br" ]; then
        echo "  $nic already master $br"
    else
        if [ -n "${current_master:-}" ]; then
            ip link set "$nic" nomaster
        fi
        ip link set "$nic" master "$br"
        echo "  attached $nic -> $br"
    fi
    ip link set "$nic" up
}

detach_port() {
    nic="$1"
    if ! ip link show "$nic" >/dev/null 2>&1; then
        return 0
    fi
    current_master=$(ip -o link show "$nic" | sed -n 's/.*master \([^ ]*\).*/\1/p')
    if [ -n "${current_master:-}" ]; then
        ip link set "$nic" nomaster
        echo "  detached $nic from $current_master (per config)"
    fi
}

bring_up_bridge dmz-br0
bring_up_bridge pcn-br0

# Read the per-NIC attach config. Lines look like `eth0=dmz-br0` (attach)
# or `# eth0=dmz-br0` (comment = leave detached).
if [ ! -f "$CONF" ]; then
    echo "  no $CONF — skipping physical-NIC attach (virtual-only mode)"
    exit 0
fi

# Build the set of NICs that SHOULD be attached so we can detach any
# leftover bridge ports that the operator commented out.
wanted_nics=""
while IFS='=' read -r nic br; do
    case "$nic" in
        ''|'#'*) continue ;;
    esac
    nic=$(echo "$nic" | tr -d '[:space:]')
    br=$(echo "$br"   | sed 's/[#].*$//' | tr -d '[:space:]')
    [ -z "$br" ] && continue
    attach_port "$nic" "$br"
    wanted_nics="$wanted_nics $nic"
done <"$CONF"

# Detach any physical NIC that's currently bridge-port'd but no longer
# in the config (operator commented it out and re-ran us).
for nic in eth0 eth1 eth2 eth3; do
    case " $wanted_nics " in
        *" $nic "*) ;;  # in wanted list, leave alone
        *)
            current_master=$(ip -o link show "$nic" 2>/dev/null | sed -n 's/.*master \([^ ]*\).*/\1/p')
            case "$current_master" in
                dmz-br0|pcn-br0) detach_port "$nic" ;;
            esac
            ;;
    esac
done
HELPER_EOF

# Detect classroom mode (student.env exists with ROLE=student). In
# classroom mode, eth0 = mgmt link to the classroom segment and MUST
# stay as an L3 endpoint — bridging it into dmz-br0 would kill the
# Pi's connectivity to the teacher panel + Loki SIEM and orphan the
# Pi from the classroom subnet entirely.
CLASSROOM_MODE=no
if [ -f /etc/otlab/student.env ] && \
   grep -q '^ROLE=student' /etc/otlab/student.env 2>/dev/null; then
    CLASSROOM_MODE=yes
fi

# Lay down the default attach config if missing. Idempotent — never
# overwrites an operator's edits.
mkdir -p /etc/otlab
if [ ! -f /etc/otlab/bridge-attach.conf ]; then
    if [ "$CLASSROOM_MODE" = "yes" ]; then
        cat >/etc/otlab/bridge-attach.conf <<'CFG_EOF'
# OTLab — physical NIC attach config (CLASSROOM MODE)
#
# Auto-generated by install-virtual-lab.sh because /etc/otlab/student.env
# indicates this Pi is a classroom student. NO physical NIC is bridged
# into any fabric bridge — the student's eth0 stays as the L3 mgmt
# interface on the classroom segment so the teacher panel + Loki can
# reach it. The internal fabric (dmz-br0, pcn-br0) is purely virtual.
#
# To extend the PCN onto a physical wire later (e.g. when wiring a
# real PLC into the student's pcn-br0 via the otlab-otext port on a
# Cruiser Keel), uncomment one of the lines below. Don't enable for
# eth0 in classroom mode — that breaks classroom connectivity.

# eth1=pcn-br0    # uncomment when wiring real OT gear into otlab-otext
CFG_EOF
        chmod 0644 /etc/otlab/bridge-attach.conf
        echo "    wrote /etc/otlab/bridge-attach.conf for CLASSROOM mode (no physical NIC bridged)"
    else
        cat >/etc/otlab/bridge-attach.conf <<'CFG_EOF'
# OTLab — physical NIC attach config for /usr/local/sbin/otlab-bridges-up
# (SINGLE-PI mode — the Pi IS the lab, eth0 extends the DMZ to physical wire)
#
# One line per NIC: <interface-name>=<bridge-name>
#   - Uncomment to attach the NIC as a bridge port (extends the virtual
#     fabric onto the physical wire — for physical Pis, hardware PLCs,
#     real operator devices, etc.)
#   - Comment out to keep the NIC out of any bridge (virtual-only zone).
#
# WARNING: bridge-port'ing a NIC into a zone with a DHCP server
# (dhcp-dmz or dhcp-pcn) means the DHCP server WILL serve leases to
# every device on that physical segment. If your lab switch has no
# VLAN isolation, only attach NICs whose physical segment you control.

# DMZ extends to the GL-AR150 / Netgear lab switch (operator laptops,
# WAN gateway). Safe to attach in single-Pi mode: this is the operator side.
eth0=dmz-br0

# PCN extends via USB NIC to the lab switch where physical Pis live.
# Disabled by default — only enable when:
#   (a) the lab switch has a VLAN that isolates pcn-br0 traffic from
#       unrelated devices, OR
#   (b) you've confirmed every device on the segment is supposed to
#       receive a lease from dhcp-pcn (.200-.250).
# eth1=pcn-br0
CFG_EOF
        chmod 0644 /etc/otlab/bridge-attach.conf
        echo "    wrote default /etc/otlab/bridge-attach.conf (single-Pi mode: DMZ on, PCN off)"
    fi
fi

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
mkdir -p /var/lib/otlab /var/lib/otlab/mm-state /var/lib/otlab/fw-state /var/lib/otlab/ssh
chmod 0700 /var/lib/otlab/ssh
# DHCP servers expect file binds (not dir binds) for atomic-rename
# semantics. Create empty files so Docker doesn't auto-create the path
# as a directory.
for f in dhcp-dmz.leases dhcp-pcn.leases \
         dhcp-dmz.reservations dhcp-pcn.reservations \
         dhcp-dmz.log dhcp-pcn.log; do
    touch "/var/lib/otlab/$f"
    chmod 0644 "/var/lib/otlab/$f"
done

# Generate a stable SSH keypair for the dashboard container. Bind-mounted
# at /root/.ssh inside the container; pubkey gets authorized on each
# physical Pi (next step) so the dashboard's health probe + remote
# reboot + pcap capture features work.
if [ ! -f /var/lib/otlab/ssh/id_ed25519 ]; then
    ssh-keygen -t ed25519 -N '' -C 'otlab-dashboard' -f /var/lib/otlab/ssh/id_ed25519 >/dev/null
    echo "    generated dashboard SSH keypair"
fi
# known_hosts and config get created on first use; pre-touch so they
# exist with the right perms (NOT 0600 because the bind-mount maps
# them as the same files inside the container's /root/.ssh).
touch /var/lib/otlab/ssh/known_hosts
chmod 0600 /var/lib/otlab/ssh/id_ed25519
chmod 0644 /var/lib/otlab/ssh/id_ed25519.pub /var/lib/otlab/ssh/known_hosts

echo "    /var/lib/otlab/ ready"
ls -la /var/lib/otlab/ 2>&1 | head -15
STATE_EOF

# Pull the pubkey to the operator's laptop so we can copy it to physical
# Pis below.
DASHBOARD_PUBKEY=$(ssh "$PI_HOST" 'sudo cat /var/lib/otlab/ssh/id_ed25519.pub')

# ---------------------------------------------------------------------------
# 4.6. Authorize the dashboard's pubkey on physical Pis (optional).
#
# Driven by env: PHYSICAL_PIS="otadmin@l1-plc-01.local otadmin@l1-hp-01.local"
# (space-separated user@host list). If unset, we skip — the operator can
# manually `ssh-copy-id -i /var/lib/otlab/ssh/id_ed25519.pub <user>@<pi>`
# later, or run this script with the env set.
#
# The dashboard SSHes as the SSH_USER env (default 'otadmin'), so the
# pubkey must be authorized for that user on each Pi.
# ---------------------------------------------------------------------------
if [ -n "${PHYSICAL_PIS:-}" ]; then
    echo "==> authorizing dashboard pubkey on physical Pis: $PHYSICAL_PIS"
    for pi in $PHYSICAL_PIS; do
        echo "    -> $pi"
        # ssh-copy-id-style: append if not already present.
        # Uses the operator's existing SSH agent for auth.
        ssh "$pi" "
            mkdir -p ~/.ssh && chmod 0700 ~/.ssh
            touch ~/.ssh/authorized_keys && chmod 0600 ~/.ssh/authorized_keys
            grep -qF '$DASHBOARD_PUBKEY' ~/.ssh/authorized_keys 2>/dev/null \
                || echo '$DASHBOARD_PUBKEY' >> ~/.ssh/authorized_keys
        " || echo "    (couldn't reach $pi — authorize manually later)"
    done
else
    echo "==> skipping physical-Pi pubkey authorization (PHYSICAL_PIS env unset)"
    echo "    To enable system-health probes for physical Pis, run this on your laptop:"
    echo "        echo '$DASHBOARD_PUBKEY' | ssh otadmin@l1-plc-01.local 'cat >> ~/.ssh/authorized_keys'"
    echo "        echo '$DASHBOARD_PUBKEY' | ssh otadmin@l1-hp-01.local  'cat >> ~/.ssh/authorized_keys'"
fi

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

# Render topology yaml from .tmpl using /etc/otlab/student.env values
# (or single-Pi defaults if student.env doesn't exist). This is what
# wires per-student subnets into clab. Single-Pi mode is unchanged —
# default substitutions produce the historical 192.168.75 / 10.20.30
# addressing.
echo "    rendering topology yaml from template ..."
bash "$LAB_DIR/scripts/render-topology.sh"

# Idempotent cleanup — clab's `destroy --cleanup` fails when the
# topology YAML drifted from the running clab-state metadata (common
# when re-running install-virtual-lab.sh after a YAML change). So we
# do a belt-and-suspenders cleanup at the docker + ip-link layers
# before deploying:
#   1. clab destroy first (handles the happy path; ignored on failure)
#   2. docker rm -f any leftover clab-otlab-* containers
#   3. ip link delete any leftover veths attached to dmz-br0 or pcn-br0
#      that aren't the physical NICs (eth0, eth1)
echo "    cleaning up any prior deployment ..."
containerlab destroy -t topologies/otlab.clab.yaml --cleanup 2>/dev/null || true

leftover_containers=$(docker ps -aq --filter 'name=clab-otlab-' || true)
if [ -n "$leftover_containers" ]; then
    docker rm -f $leftover_containers >/dev/null 2>&1 || true
fi

# Drop any leftover veths attached to the zone bridges. The endpoint
# names from the topology YAML (fw-dmz, fw-pcn, dashboard, dhcpdmz,
# dhcppcn, master, plc1, plc2, sensorsim, dnp3) are stable so we can
# enumerate. Skip eth0 / eth1 — those are physical bridge ports.
for veth in fw-dmz fw-pcn dashboard dhcpdmz dhcppcn master plc1 plc2 sensorsim dnp3; do
    ip link delete "$veth" 2>/dev/null || true
done

# Also wipe clab's state dir if present (forces clab to re-create cleanly).
rm -rf clab-otlab 2>/dev/null || true

echo "    deploying ..."
containerlab deploy -t topologies/otlab.clab.yaml
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

 Topology (9 containers):
   dmz-br0  192.168.75.0/24   (L3.5 — Industrial DMZ)
     .1   fw-dmz-pcn       firewall + DNS forwarder (dnsmasq)
     .2   dhcp-dmz         DHCP server (.150-.199 scope)
     .40  dashboard        https://${HOST_BARE}:8000/   (otlab / P@ssw0rd!)

   pcn-br0  10.20.30.0/24    (L1/L2 — Process Control Network)
     .1   fw-dmz-pcn       firewall + DNS forwarder
     .2   dhcp-pcn         DHCP server (.200-.250 scope; reservations for .47/.48/.50/.51/.52)
     .43  modbus-master    polls sensor-sim @10Hz; writes state for dashboard
     .60  plc-1-virt       OpenPLC #1                  http://${HOST_BARE}:8081/
     .61  plc-2-virt       OpenPLC #2                  http://${HOST_BARE}:8082/
     .70  sensor-sim       Modbus outstation :5020 + ctrl :5021
     .71  dnp3-outstation  DNP3 outstation :20000

   Physical (when eth1 is enabled in /etc/otlab/bridge-attach.conf):
     .47  l1-plc-01        physical Pi 5 + OpenPLC + Phase 2 hardware
     .48  l1-hp-01         physical Pi 3 B+ Conpot host
     .50-.52  Conpot personas (siemens, schneider, rockwell)

 Dashboard tabs:
   Overview · Architecture · IDS · Firewall · DHCP · Live Data · Teaching

 Manage:
   sudo containerlab inspect -t ${LAB_DIR}/virtual/topologies/otlab.clab.yaml
   sudo containerlab destroy -t ${LAB_DIR}/virtual/topologies/otlab.clab.yaml --cleanup
   sudo containerlab deploy  -t ${LAB_DIR}/virtual/topologies/otlab.clab.yaml --reconfigure

 Bridge / physical-NIC config:
   /etc/otlab/bridge-attach.conf       (per-NIC attach decisions)
   sudo /usr/local/sbin/otlab-bridges-up   (re-apply after edits)

 Container shells:
   sudo docker exec -it clab-otlab-fw-dmz-pcn bash
   sudo docker exec -it clab-otlab-modbus-master bash
   sudo docker exec -it clab-otlab-sensor-sim bash

 Live state (host-shared volumes — read directly for debugging):
   /var/lib/otlab/mm-state/last.json           modbus-master tick state
   /var/lib/otlab/fw-state/iptables.json       firewall rules + counters
   /var/lib/otlab/fw-state/conntrack.txt       active flows
   /var/lib/otlab/fw-state/dnsmasq-fw.log      DNS query log
   /var/lib/otlab/dhcp-{dmz,pcn}.leases        DHCP leases per zone
   /var/lib/otlab/dhcp-{dmz,pcn}.reservations  rendered DHCP_HOSTS

 Next steps:
   - Companion admin UIs: install-cockpit.sh, install-portainer.sh,
     install-edgeshark.sh
   - Suricata IDS: install-suricata.sh (host-mode sniffer on pcn-br0)
   - V2.z (next): Authentik + Ignition + Guacamole on the DMZ
   - V3: CODESYS Control SL + Web HMI
   See docs/virtualization.md for the full roadmap.
==============================================================================
EOF
