#!/bin/sh
# start-firewall.sh — apply the OTLab DMZ↔PCN firewall policy.
#
# Runs inside the firewall container on every start. Idempotent: flushes
# the chains first, then re-applies the canonical policy.
#
# Architecture:
#
#   dmz-br0 (192.168.75.0/24, L3.5)  ──┐
#                                       ├── this container ── eth0 (uplink)
#   pcn-br0 (10.20.30.0/24,    L1/L2)  ──┘
#
# Policy (Purdue-aligned conduit):
#   - DMZ → PCN: allow read protocols (Modbus, DNP3, HTTP, SSH, ICMP)
#                  per-port allowlist; writes are protocol-not-port so
#                  Suricata catches them, IDS-not-IPS for now (V2 will
#                  add deep-protocol blocking via Suricata in IPS mode)
#   - PCN → DMZ: ESTABLISHED/RELATED only (responses)
#   - PCN → WAN: NAT via uplink (so PLCs can reach internet for apt etc.)
#   - DMZ → WAN: NAT via uplink
#   - WAN → anything: DROP except ESTABLISHED
#
# Tunable via env (set in topology.clab.yaml):
#   PCN_NET, DMZ_NET    — subnets
#   PCN_IF, DMZ_IF      — interface names inside the container
#   UPLINK_IF           — uplink interface (containerlab mgmt by default)

set -eu

# Defaults — env can override
: "${PCN_NET:=10.20.30.0/24}"
: "${DMZ_NET:=192.168.75.0/24}"
: "${PCN_IF:=eth2}"
: "${DMZ_IF:=eth1}"
: "${UPLINK_IF:=eth0}"

echo "==> applying OTLab firewall policy"
echo "    DMZ: $DMZ_NET on $DMZ_IF"
echo "    PCN: $PCN_NET on $PCN_IF"
echo "    UPLINK: $UPLINK_IF"

# Enable IP forwarding (also set in topology.clab.yaml exec)
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# Flush + reset chains (idempotent re-apply)
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X
iptables -t mangle -F
iptables -t mangle -X

# Default policies
iptables -P INPUT   DROP
iptables -P FORWARD DROP
iptables -P OUTPUT  ACCEPT

# ─────────────────────────────────────────────────────────────────────
# INPUT — traffic destined to the firewall itself
# ─────────────────────────────────────────────────────────────────────
iptables -A INPUT -i lo                                                          -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED                     -j ACCEPT
iptables -A INPUT -p icmp --icmp-type echo-request                               -j ACCEPT
# SSH for diagnostics from DMZ (operator-side)
iptables -A INPUT -i "$DMZ_IF" -s "$DMZ_NET" -p tcp --dport 22                   -j ACCEPT

# DNS — the firewall runs dnsmasq as a forwarder bound to its bridge-side
# IPs (192.168.75.1 + 10.20.30.1). Internal DHCP servers advertise this
# container as the resolver, so every internal DNS query lands here.
iptables -A INPUT -i "$DMZ_IF" -s "$DMZ_NET" -p udp --dport 53                   -j ACCEPT
iptables -A INPUT -i "$DMZ_IF" -s "$DMZ_NET" -p tcp --dport 53                   -j ACCEPT
iptables -A INPUT -i "$PCN_IF" -s "$PCN_NET" -p udp --dport 53                   -j ACCEPT
iptables -A INPUT -i "$PCN_IF" -s "$PCN_NET" -p tcp --dport 53                   -j ACCEPT

# ─────────────────────────────────────────────────────────────────────
# FORWARD — the policy that matters
# ─────────────────────────────────────────────────────────────────────
# Established connections — bidirectional
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED                   -j ACCEPT

# DMZ → PCN: allow operations-zone reach into PLC zone for read protocols
# Note: protocol-aware "block writes only" requires Suricata IPS or a real
# OT firewall. For V1, we allow port-level access; V2 layers Suricata.
iptables -A FORWARD -i "$DMZ_IF" -o "$PCN_IF" -s "$DMZ_NET" -d "$PCN_NET" \
    -p tcp -m multiport --dports 22,80,502,5020,8080,20000                       -j ACCEPT
iptables -A FORWARD -i "$DMZ_IF" -o "$PCN_IF" -s "$DMZ_NET" -d "$PCN_NET" \
    -p icmp                                                                      -j ACCEPT

# PCN → DMZ: only responses (handled by ESTABLISHED above). Initiated
# connections from PCN to DMZ are blocked by default — PLCs should never
# spontaneously call into operations.

# PCN → WAN: allow outbound (for apt-get update, NTP, container pulls)
iptables -A FORWARD -i "$PCN_IF" -o "$UPLINK_IF" -s "$PCN_NET"                   -j ACCEPT

# DMZ → WAN: allow outbound
iptables -A FORWARD -i "$DMZ_IF" -o "$UPLINK_IF" -s "$DMZ_NET"                   -j ACCEPT

# Log + drop everything else (the meaningful artifact for forensics)
iptables -A FORWARD -m limit --limit 5/min -j LOG --log-prefix "OTLAB-FW-DROP: " --log-level 6
iptables -A FORWARD                                                              -j DROP

# ─────────────────────────────────────────────────────────────────────
# NAT — masquerade outbound on the uplink so PLC/DMZ can reach internet
# ─────────────────────────────────────────────────────────────────────
iptables -t nat -A POSTROUTING -o "$UPLINK_IF" -s "$PCN_NET" -j MASQUERADE
iptables -t nat -A POSTROUTING -o "$UPLINK_IF" -s "$DMZ_NET" -j MASQUERADE

# SNAT for DMZ → PCN traffic. Physical Pis bridged into pcn-br0 via
# eth1 (USB NIC) have their default route on wlan0 (so they retain
# tailscale + apt access independently of the lab fabric). That means
# they don't know how to reply to packets sourced from 192.168.75.0/24
# — there's no route back to the DMZ on the Pi side.
#
# Fix: rewrite src as 10.20.30.1 (the firewall) on the way out the PCN
# interface. Physical Pis reply to .1 (which IS on their local subnet),
# the firewall's conntrack table translates the reply back to the
# original DMZ client. Result: every DMZ-side service can reach every
# physical Pi without needing per-Pi static routes.
#
# Side effect: virtual PCN containers ALSO see DMZ traffic as src=.1,
# but that's a no-op on the reply path since they already use .1 as
# their default gateway.
#
# Forensic note: Suricata sniffs `pcn-br0` AFTER SNAT (POSTROUTING
# happens before the packet hits the bridge fabric), so DMZ-originated
# attacks appear in alerts as src=10.20.30.1. The IDS rules use
# `!10.20.30.43` (anything-but-the-master) for the negation, so .1
# still triggers the rule correctly — but the original DMZ client IP
# is lost from Suricata's view. The dashboard's audit log still
# records the original auth'd user, so cross-correlation is possible
# downstream.
iptables -t nat -A POSTROUTING -o "$PCN_IF" -s "$DMZ_NET" -d "$PCN_NET" \
    -j SNAT --to-source 10.20.30.1

echo "==> firewall policy applied"
iptables -nvL FORWARD --line-numbers | head -20

# ─────────────────────────────────────────────────────────────────────
# DNS forwarder — dnsmasq bound to the firewall's bridge-facing IPs.
# Internal DHCP servers advertise this container as the resolver, so
# every internal DNS query lands here. Useful as a teaching artifact
# ("DNS exfil detection at the firewall").
#
# Only start if not already running. This makes start-firewall.sh
# idempotent across re-execs (e.g. when clab re-runs the exec hook on
# `containerlab deploy --reconfigure`).
# ─────────────────────────────────────────────────────────────────────
: "${DNS_UPSTREAM_1:=1.1.1.1}"
: "${DNS_UPSTREAM_2:=8.8.8.8}"

if ! pgrep -x dnsmasq >/dev/null 2>&1; then
    echo "==> starting dnsmasq DNS forwarder on 192.168.75.1 + 10.20.30.1"
    dnsmasq --no-hosts --no-resolv \
        --listen-address=192.168.75.1 --listen-address=10.20.30.1 \
        --bind-interfaces \
        --server="$DNS_UPSTREAM_1" --server="$DNS_UPSTREAM_2" \
        --log-queries --log-facility=/var/log/dnsmasq-fw.log \
        --cache-size=200
else
    echo "==> dnsmasq already running, skipping"
fi
