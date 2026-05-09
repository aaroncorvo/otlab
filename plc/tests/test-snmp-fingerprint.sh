#!/usr/bin/env bash
# test-snmp-fingerprint.sh — snmpwalk vendor enterprise OIDs against each
# Conpot persona, prove they answer with vendor-correct identifiers.
#
# What you're doing: ICS reconnaissance via SNMP — students learn how
# attackers fingerprint networked devices to choose the right exploit.
# What honeypots do: respond with vendor-coherent fakes to deceive +
# alert defenders.
#
# Requires: snmp tools (apt install snmp). Run from any host with
# lab-segment access (or via tailscale subnet route).

set -e

PERSONAS=(
    "Siemens   10.20.30.50  1.3.6.1.4.1.4196   PS4-CPU01"
    "Schneider 10.20.30.51  1.3.6.1.4.1.3833   HVAC-M340"
    "Rockwell  10.20.30.52  1.3.6.1.4.1.5188   CHEM-LGX01"
)

for entry in "${PERSONAS[@]}"; do
    set -- $entry
    name=$1; ip=$2; oid=$3; expected=$4
    echo "=== ${name} @ ${ip}  (vendor OID ${oid}, expected sysName=${expected}) ==="
    echo "  sysDescr (1.3.6.1.2.1.1.1.0):"
    snmpwalk -v2c -c public -t 2 -r 1 "$ip" 1.3.6.1.2.1.1.1.0 2>&1 | sed 's/^/    /' | head -3
    echo "  sysName  (1.3.6.1.2.1.1.5.0):"
    snmpwalk -v2c -c public -t 2 -r 1 "$ip" 1.3.6.1.2.1.1.5.0 2>&1 | sed 's/^/    /' | head -3
    echo "  vendor enterprise (${oid}):"
    snmpwalk -v2c -c public -t 2 -r 1 "$ip" "$oid" 2>&1 | sed 's/^/    /' | head -5
    echo
done

echo "Detection lesson:"
echo "  Every snmpwalk above produced a Conpot log entry with your IP."
echo "  Open the dashboard's Honeypot Fabric panel — your IP appears in"
echo "  the top-attackers list within ~8 seconds. SOC alert pipeline:"
echo "  Conpot log → fluentd → SIEM → ticket → block."
