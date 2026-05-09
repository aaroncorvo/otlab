#!/usr/bin/env bash
# test-arp-discovery.sh — passive + active discovery of every device on
# the lab segment 10.20.30.0/24. Compares results against the dashboard's
# expected inventory.
#
# What an attacker does: enumerate the segment to find targets.
# What defenders look for: unexpected devices appearing in ARP tables.
# What this lab teaches: how lightweight an "attack" can be — just ping +
# `ip neigh`, no exploitation needed, fully passive after the ARP probe.

set -e
SEGMENT="10.20.30"

echo "=== passive: existing ARP cache (whatever this host has talked to) ==="
ip neigh show dev eth0 2>&1 | grep "$SEGMENT" | sort -t. -k4 -n

echo
echo "=== active: ping sweep + ARP harvest ==="
for i in $(seq 1 254); do
  (ping -c1 -W1 ${SEGMENT}.${i} >/dev/null 2>&1 &)
done
wait
sleep 1

echo "post-sweep inventory:"
ip neigh show dev eth0 2>&1 | grep "$SEGMENT" | grep -v FAILED | sort -t. -k4 -n | sed 's/^/  /'

echo
echo "=== expected lab inventory ==="
declare -A EXPECTED=(
    ["10.20.30.1"]="TP-Link gateway"
    ["10.20.30.47"]="l1-plc-01 (Pi 5)"
    ["10.20.30.48"]="l1-hp-01 (Pi 3 B+)"
    ["10.20.30.49"]="l3-mon-01 (Pi 5 + NVMe)"
    ["10.20.30.50"]="Conpot Siemens persona"
    ["10.20.30.51"]="Conpot Schneider persona"
    ["10.20.30.52"]="Conpot Rockwell persona"
)
for ip in "${!EXPECTED[@]}"; do
    if ip neigh show dev eth0 | grep -q "^${ip} "; then
        printf "  ✓ %-15s — %s\n" "$ip" "${EXPECTED[$ip]}"
    else
        printf "  ✗ %-15s — %s   [MISSING from ARP]\n" "$ip" "${EXPECTED[$ip]}"
    fi
done

echo
echo "=== unknown / extra IPs (potential other DHCP clients) ==="
for line in $(ip neigh show dev eth0 | grep "$SEGMENT" | grep -v FAILED | awk '{print $1}'); do
    if [ -z "${EXPECTED[$line]:-}" ]; then
        printf "  ? %-15s\n" "$line"
    fi
done

echo
echo "Detection lesson: any 'unknown / extra' device on this segment"
echo "is a candidate intruder. Real plant SOCs feed ARP tables into"
echo "asset-inventory tools (Claroty, Dragos) and alert on first-sighting."
