#!/usr/bin/env bash
# configure-4port-pi.sh — name the 4 Ethernet ports on a Cruiser Keel
# (or any 4-port carrier) Pi systematically and persist the role map.
#
# Why: predictable network names. Linux's default `eth0`/`eth1` order
# can shift between boots depending on PCIe enumeration. We pin them
# by MAC address using systemd .link files so they always show up as:
#
#   otlab-mgmt    Pi mgmt / classroom segment   (VLAN 10)
#   otlab-otext   OT lab segment extension      (VLAN 200, future)
#   otlab-mirror  SPAN / mirror destination     (future, out-of-band IDS)
#   otlab-spare   Reserved                      (future)
#
# Idempotent. Run after bootstrap-pi.sh, before install-virtual-lab.sh.
# otlab-install.sh calls this automatically on student Pis with 4+ NICs.
#
# Usage:
#   ./scripts/configure-4port-pi.sh otadmin@otlab-student-05.local
#
# Flags:
#   --dry-run   Print what would be renamed, don't change anything
#   --reset     Remove the .link files (restore default eth0..3 names)
#
# After this runs (requires reboot to take effect):
#   ip link show          # shows otlab-mgmt, otlab-otext, otlab-mirror, otlab-spare
#   /etc/otlab/ports.conf # the persisted role→MAC map

set -euo pipefail

PI_HOST="${1:?Usage: $0 otadmin@<pi>.local}"
DRY_RUN="no"
RESET="no"

shift
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN="yes"; shift ;;
        --reset)   RESET="yes"; shift ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mXX \033[0m %s\n' "$*" >&2; exit 1; }

# ── reset mode ────────────────────────────────────────────────────────
if [[ "$RESET" == "yes" ]]; then
    say "removing OTLab port-naming .link files on $PI_HOST"
    if [[ "$DRY_RUN" == "yes" ]]; then
        echo "    [dry-run] would remove /etc/systemd/network/10-otlab-*.link"
        echo "    [dry-run] would remove /etc/otlab/ports.conf"
    else
        ssh "$PI_HOST" '
            sudo rm -f /etc/systemd/network/10-otlab-*.link
            sudo rm -f /etc/otlab/ports.conf
            echo "    removed. Reboot to revert to default eth0..3 names."
        '
    fi
    exit 0
fi

# ── discover NICs ─────────────────────────────────────────────────────
say "discovering Ethernet NICs on $PI_HOST"
NIC_DATA="$(ssh "$PI_HOST" '
    for iface in $(ls /sys/class/net/ | grep -vE "^(lo|docker|br-|veth|vlan|wlan)"); do
        if [ -f "/sys/class/net/$iface/address" ]; then
            mac=$(cat /sys/class/net/$iface/address)
            # Skip docker bridges and clab veths (they have synthetic MACs)
            driver=$(readlink "/sys/class/net/$iface/device/driver" 2>/dev/null | xargs -n1 basename || echo "unknown")
            speed=$(cat "/sys/class/net/$iface/speed" 2>/dev/null || echo "unknown")
            echo "$iface $mac $driver $speed"
        fi
    done
')"

if [[ -z "$NIC_DATA" ]]; then
    die "no Ethernet NICs detected on $PI_HOST"
fi

echo "    NIC inventory:"
echo "$NIC_DATA" | awk '{printf "      %-12s  %s  driver=%s  speed=%s\n", $1, $2, $3, $4}'

NIC_COUNT=$(echo "$NIC_DATA" | wc -l | tr -d ' ')
if [[ "$NIC_COUNT" -lt 4 ]]; then
    warn "expected 4 NICs (Cruiser Keel) but found $NIC_COUNT"
    warn "this script is a no-op on Pis with <4 NICs — they only need eth0 = mgmt"
    exit 0
fi
echo "    found $NIC_COUNT NICs — proceeding with 4-port naming"

# ── build role → MAC map ──────────────────────────────────────────────
# Convention: order by current eth name (eth0 first), assign roles in
# fixed sequence. eth0 = mgmt (Pi's onboard NIC always enumerates first
# on the Cruiser Keel). Other 3 are the PCIe-attached NICs.
ROLES=("otlab-mgmt" "otlab-otext" "otlab-mirror" "otlab-spare")
i=0

declare -a ROLE_MAC=()
declare -a ROLE_OLDNAME=()
while read -r iface mac _driver _speed; do
    [[ "$i" -ge 4 ]] && break
    ROLE_MAC[i]="$mac"
    ROLE_OLDNAME[i]="$iface"
    i=$((i + 1))
done <<<"$NIC_DATA"

say "role assignment"
for n in 0 1 2 3; do
    printf "    %-13s %s   (was %s)\n" "${ROLES[$n]}" "${ROLE_MAC[$n]}" "${ROLE_OLDNAME[$n]}"
done

# ── generate systemd .link files ──────────────────────────────────────
say "generating systemd .link files"
LINK_FILES="$(mktemp -d)"
PORTS_CONF="$(mktemp)"

cat >"$PORTS_CONF" <<EOF
# /etc/otlab/ports.conf — written by scripts/configure-4port-pi.sh
# Maps logical OTLab port roles to MAC addresses for this Pi.
# Used by otlab-install.sh + render-topology.sh.
OTLAB_MGMT_MAC=${ROLE_MAC[0]}
OTLAB_OTEXT_MAC=${ROLE_MAC[1]}
OTLAB_MIRROR_MAC=${ROLE_MAC[2]}
OTLAB_SPARE_MAC=${ROLE_MAC[3]}
EOF

for n in 0 1 2 3; do
    role="${ROLES[$n]}"
    mac="${ROLE_MAC[$n]}"
    cat >"$LINK_FILES/10-${role}.link" <<EOF
# /etc/systemd/network/10-${role}.link
# Pin role "${role}" to MAC ${mac} (written by configure-4port-pi.sh)
[Match]
MACAddress=${mac}

[Link]
Name=${role}
EOF
done

if [[ "$DRY_RUN" == "yes" ]]; then
    say "[dry-run] would deploy these files:"
    for f in "$LINK_FILES"/*.link; do
        echo ""
        echo "    --- $f ---"
        cat "$f" | sed 's/^/      /'
    done
    echo ""
    echo "    --- /etc/otlab/ports.conf ---"
    cat "$PORTS_CONF" | sed 's/^/      /'
    rm -rf "$LINK_FILES" "$PORTS_CONF"
    exit 0
fi

# ── deploy to Pi ──────────────────────────────────────────────────────
say "deploying .link files + /etc/otlab/ports.conf to $PI_HOST"
ssh "$PI_HOST" 'sudo mkdir -p /etc/systemd/network /etc/otlab'

for f in "$LINK_FILES"/*.link; do
    base="$(basename "$f")"
    scp -q "$f" "$PI_HOST:/tmp/$base"
    ssh "$PI_HOST" "sudo install -m 0644 -o root -g root /tmp/$base /etc/systemd/network/$base && rm /tmp/$base"
done

scp -q "$PORTS_CONF" "$PI_HOST:/tmp/ports.conf"
ssh "$PI_HOST" 'sudo install -m 0644 -o root -g root /tmp/ports.conf /etc/otlab/ports.conf && rm /tmp/ports.conf'

rm -rf "$LINK_FILES" "$PORTS_CONF"

# ── trigger udev re-process (avoids reboot for most cases) ────────────
say "applying via systemd-udev"
ssh "$PI_HOST" '
    sudo udevadm control --reload
    # Note: rename happens on next link-down/up cycle. To force it now:
    #   sudo ip link set <oldname> down
    #   sudo udevadm trigger --action=add --subsystem-match=net
    # But that drops the SSH session for whichever NIC carries it. So
    # we recommend a reboot for safety.
    echo "    .link files installed. REBOOT the Pi to apply: sudo reboot"
'

echo
say "done"
echo "    After reboot, verify with: ssh $PI_HOST 'ip link show | grep otlab-'"
echo "    Expected names: otlab-mgmt, otlab-otext, otlab-mirror, otlab-spare"
echo "    Persistent role map: /etc/otlab/ports.conf"
