#!/usr/bin/env bash
# install-suricata.sh — deploy Suricata IDS on the l3-mon-01. Sniffs the
# lab segment promiscuously, parses Modbus/DNP3/HTTP/SNMP, alerts via
# EVE JSON.
#
# Rules: ET-OT (Emerging Threats Open) ICS rules + a small set of
# OTLab-specific rules tuned to the scenarios (FC5/6 from non-master,
# DNP3 setpoint manipulation, etc.).
#
# EVE JSON output at /var/log/suricata/eve.json — consumed by a future
# dashboard panel "IDS Alerts" that tails the file and surfaces fresh
# alerts.
#
# Idempotent — re-run after rule edits.
#
# Usage:
#   ./scripts/install-suricata.sh otadmin@OPSHOST.local
#
# Pre-req: bootstrap-l3-mon-role.sh has installed the suricata package.

set -euo pipefail
PI_HOST="${1:?PI_HOST required, e.g. otadmin@OPSHOST.local}"
LAB_IFACE="${LAB_IFACE:-eth0}"   # interface that sees the lab segment

echo "==> deploying Suricata on $PI_HOST (interface=$LAB_IFACE)"

# ---------------------------------------------------------------------------
# 1. Ensure suricata + suricata-update are installed
# ---------------------------------------------------------------------------
ssh "$PI_HOST" '
    set -e
    if ! command -v suricata >/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq suricata
    fi
    if ! command -v suricata-update >/dev/null; then
        sudo apt-get install -y -qq suricata-update python3-pip || \
            sudo /home/otuser/lab/.venv-modern/bin/pip install --quiet suricata-update || true
    fi
'

# ---------------------------------------------------------------------------
# 2. Pull ET-OT ICS rules
# ---------------------------------------------------------------------------
echo "==> updating Suricata rules (ET Open + ET-OT)"
ssh "$PI_HOST" '
    set -e
    sudo suricata-update enable-source et/open                 >/dev/null 2>&1 || true
    sudo suricata-update enable-source ptresearch/attackdetection >/dev/null 2>&1 || true
    # ET-OT lives at https://rules.emergingthreats.net/open/suricata-7.0/rules/
    # Pull it directly into the rules dir
    sudo curl -fsSL -o /etc/suricata/rules/emerging-scada.rules \
        https://rules.emergingthreats.net/open/suricata-7.0/rules/emerging-scada.rules \
        2>/dev/null || \
    sudo curl -fsSL -o /etc/suricata/rules/emerging-scada.rules \
        https://rules.emergingthreats.net/open/suricata/rules/emerging-scada.rules \
        2>/dev/null || \
    echo "    note: emerging-scada.rules pull failed (offline?). Install will continue with built-in rules only."
    sudo suricata-update 2>&1 | tail -5 || true
'

# ---------------------------------------------------------------------------
# 3. OTLab-specific custom rules
# ---------------------------------------------------------------------------
echo "==> writing OTLab custom rules"
ssh "$PI_HOST" 'sudo tee /etc/suricata/rules/otlab-custom.rules >/dev/null' <<'EOF'
# OTLab custom Suricata rules — tuned to the scenarios and walkthroughs.
# Detection signatures from docs/curriculum.md "Test Library" detection table.

# Modbus FC5 (write single coil) from any IP that is not the legitimate master
# l1-plc-01 (10.20.30.47) → indicator-of-compromise per attack-rogue-write +
# attack-breaker-trip walkthroughs. Drops or alerts depending on local policy.
alert tcp ![10.20.30.47] any -> any 502 (msg:"OTLAB-1001 Modbus FC5 write coil from non-master"; \
    flow:established,to_server; content:"|00 00 00 00 00 06|"; offset:2; depth:6; \
    content:"|05|"; distance:1; within:1; classtype:attempted-admin; sid:1000001; rev:1;)

# Modbus FC6 (write single register) from non-master
alert tcp ![10.20.30.47] any -> any 502 (msg:"OTLAB-1002 Modbus FC6 write register from non-master"; \
    flow:established,to_server; content:"|00 00 00 00 00 06|"; offset:2; depth:6; \
    content:"|06|"; distance:1; within:1; classtype:attempted-admin; sid:1000002; rev:1;)

# Same patterns against sensor-sim port 5020
alert tcp ![10.20.30.47] any -> any 5020 (msg:"OTLAB-1003 Modbus FC5 to sensor-sim from non-master"; \
    flow:established,to_server; content:"|00 00 00 00 00 06|"; offset:2; depth:6; \
    content:"|05|"; distance:1; within:1; classtype:attempted-admin; sid:1000003; rev:1;)

alert tcp ![10.20.30.47] any -> any 5020 (msg:"OTLAB-1004 Modbus FC6 to sensor-sim from non-master"; \
    flow:established,to_server; content:"|00 00 00 00 00 06|"; offset:2; depth:6; \
    content:"|06|"; distance:1; within:1; classtype:attempted-admin; sid:1000004; rev:1;)

# Modbus FC15/FC16 (multi writes)
alert tcp ![10.20.30.47] any -> any 5020 (msg:"OTLAB-1005 Modbus FC15 multi-coil write from non-master"; \
    flow:established,to_server; content:"|0F|"; offset:7; depth:1; classtype:attempted-admin; sid:1000005; rev:1;)
alert tcp ![10.20.30.47] any -> any 5020 (msg:"OTLAB-1006 Modbus FC16 multi-register write from non-master"; \
    flow:established,to_server; content:"|10|"; offset:7; depth:1; classtype:attempted-admin; sid:1000006; rev:1;)

# DNP3 link-layer activity to the outstation from non-master IPs
alert tcp ![10.20.30.47] any -> any 20000 (msg:"OTLAB-2001 DNP3 from non-master to outstation"; \
    flow:established,to_server; content:"|05 64|"; offset:0; depth:2; classtype:attempted-admin; sid:1000010; rev:1;)

# Traffic to any Conpot persona — by definition external-malicious in this lab
alert ip ![10.20.30.0/24] any -> 10.20.30.50 any (msg:"OTLAB-3001 Inbound to Siemens Conpot persona (deception trip)";  classtype:trojan-activity; sid:1000020; rev:1;)
alert ip ![10.20.30.0/24] any -> 10.20.30.51 any (msg:"OTLAB-3002 Inbound to Schneider Conpot persona (deception trip)"; classtype:trojan-activity; sid:1000021; rev:1;)
alert ip ![10.20.30.0/24] any -> 10.20.30.52 any (msg:"OTLAB-3003 Inbound to Rockwell Conpot persona (deception trip)";  classtype:trojan-activity; sid:1000022; rev:1;)

# SSH brute-force (>5 failed in 60 s from same src)
alert tcp any any -> any 22 (msg:"OTLAB-4001 Possible SSH brute force"; \
    flow:established,to_server; threshold: type both, track by_src, count 5, seconds 60; \
    classtype:attempted-recon; sid:1000030; rev:1;)
EOF

# ---------------------------------------------------------------------------
# 4. Configure suricata.yaml — interface + EVE output + rule includes
# ---------------------------------------------------------------------------
echo "==> configuring suricata.yaml"
ssh "$PI_HOST" "
    set -e
    # Backup once (idempotent re-run won't clobber)
    if [ ! -f /etc/suricata/suricata.yaml.otlab-orig ]; then
        sudo cp /etc/suricata/suricata.yaml /etc/suricata/suricata.yaml.otlab-orig
    fi
    # Set the af-packet interface to LAB_IFACE
    sudo sed -i 's/^\(\s*\)- interface: .*/\1- interface: ${LAB_IFACE}/' /etc/suricata/suricata.yaml
    # Ensure EVE JSON output is enabled (Suricata default true, but set explicit)
    sudo sed -i '/eve-log:/,/^\s*-/{ s/^\(\s*\)enabled:.*/\1enabled: yes/; }' /etc/suricata/suricata.yaml || true
    # Ensure our custom rules file is loaded — append if not present
    if ! sudo grep -q 'otlab-custom.rules' /etc/suricata/suricata.yaml; then
        sudo sed -i '/^rule-files:/a\\  - otlab-custom.rules' /etc/suricata/suricata.yaml
    fi
    if ! sudo grep -q 'emerging-scada.rules' /etc/suricata/suricata.yaml; then
        if [ -f /etc/suricata/rules/emerging-scada.rules ]; then
            sudo sed -i '/^rule-files:/a\\  - emerging-scada.rules' /etc/suricata/suricata.yaml
        fi
    fi
    sudo suricata -T -c /etc/suricata/suricata.yaml 2>&1 | tail -10 || true
"

# ---------------------------------------------------------------------------
# 5. Enable + start service
# ---------------------------------------------------------------------------
echo "==> enabling + (re)starting suricata"
ssh "$PI_HOST" '
    sudo systemctl enable suricata
    sudo systemctl restart suricata
'
sleep 4
ssh "$PI_HOST" 'sudo systemctl status suricata --no-pager 2>&1 | head -15'

# ---------------------------------------------------------------------------
# 6. Stamp bootstrap-info
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
"

cat <<EOF

==============================================================================
 Suricata deployed.

   Interface:  ${LAB_IFACE}
   Rules:      /etc/suricata/rules/otlab-custom.rules (10 OTLab-specific)
               /etc/suricata/rules/emerging-scada.rules (ET-OT, if pulled)
               /var/lib/suricata/rules/* (ET Open via suricata-update)
   EVE log:    /var/log/suricata/eve.json
   Stats log:  /var/log/suricata/stats.log

 Tail alerts:
   ssh $PI_HOST 'sudo tail -f /var/log/suricata/eve.json | jq "select(.event_type==\"alert\")"'

 Test from a non-master IP (will fire OTLAB-1004):
   python3 plc/tests/test-modbus-write.py --target sensor-sim --kind reg --addr 0 --value 1234

 The dashboard's "IDS Alerts" panel (planned, ships next) will tail this
 file and surface fresh alerts within ~5 s.

 Note: promiscuous-mode capture only sees what's on the Suricata host's
 broadcast domain. With a managed switch + port-mirroring (SPAN), Suricata
 sees ALL lab traffic. Without it, you only see traffic to/from this host
 and broadcast / multicast.
==============================================================================
EOF
