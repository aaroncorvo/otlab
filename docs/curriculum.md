# OTLab Curriculum

The OTLab is a hands-on industrial cybersecurity teaching environment. This document is the syllabus — what the lab teaches, in what order, and how exercises map to industry-standard frameworks.

## Pedagogical model

Every teaching artifact in the lab is structured as an **Attack → Detect → Defend** triad:

- **Attack** — students perform the malicious action against live infrastructure, observe wire-level evidence of what they did
- **Detect** — students examine the artifacts (pcaps, logs, dashboard signals) the attack produced; learn what to grep for in real plants
- **Defend** — students implement controls (firewall rules, IDS signatures, segmentation) and verify they work by re-running the attack

Every exercise comes with a real-world incident citation (Oldsmar, Stuxnet, Ukraine 2015, Aurora, Industroyer, Triton, Colonial Pipeline). The lab's job is to make those incidents reproducible at booth scale.

## Scenarios

The lab supports three OT verticals out of the box, swappable in one command:

| Scenario | Vertical | Real PLC equivalents | Regulatory frameworks |
|---|---|---|---|
| `water-treatment` | Water & Wastewater (default) | Allen-Bradley CompactLogix, Modicon M340, Siemens S7-1200 | EPA AWIA 2018, NIST SP 800-82r3, WaterISAC, CISA CPGs |
| `power-substation` | Electric Power | SEL relays, GE Multilin, ABB REL/REF | NERC CIP-002 → CIP-014, NIST SP 800-82r3, IEEE 1686, DOE C2M2 |
| `natural-gas-pipeline` | Oil & Gas (Midstream) | Allen-Bradley ControlLogix, Schneider Modicon | TSA SD2021-02, PHMSA 49 CFR 195/192, API 1164, NIST SP 800-82r3 |

Each scenario defines:
- Process description + L1 PLC role
- 4 holding-register waveforms (sine/cosine/sawtooth/counter) with engineering units
- 2 coils (one always-on running indicator, one threshold-driven alarm)
- Synoptic diagram labels (instrument tags follow ISA-5.1 conventions)
- Regulatory tags (with scope explanations)
- Risk matrix (likelihood × impact) of intentional vulnerabilities
- Incident walkthroughs (Attack/Detect/Defend exercises)

Switch scenarios by writing a systemd drop-in:

```bash
ssh otadmin@<host> "
sudo mkdir -p /etc/systemd/system/sensor-sim.service.d
sudo tee /etc/systemd/system/sensor-sim.service.d/scenario.conf >/dev/null <<EOF
[Service]
Environment=\"SENSOR_SIM_SCENARIO=/home/otuser/lab/scenarios/power-substation.json\"
EOF
sudo systemctl daemon-reload && sudo systemctl restart sensor-sim
"
```

The dashboard's scenario strip, synoptic instrument labels, units, risk heatmap, and walkthroughs all adapt automatically.

## Protocol surfaces

The lab speaks four ICS protocols on the wire so students can compare attack surfaces:

| Protocol | Port | Implementation | Teaching focus |
|---|---|---|---|
| **Modbus TCP** | 5020 (sensor-sim slave) + 502 (softplc-1 OpenPLC mirror) | Pure-stdlib `plc/sensor-sim.py` (FC1/2/3/4 reads + FC5/6/15/16 writes) | The classic — no auth, no encryption, easy to attack and defend |
| **DNP3** | 20000 (sensor-sim) | Pure-stdlib `plc/dnp3-outstation.py` (link layer + READ Class 0) | Utility-sector standard, more complex framing, optional Secure Authentication |
| **HTTP / HTTPS** | 8080 OpenPLC web UI · 8000 dashboard · :80 each Conpot persona | Various | Cleartext / self-signed cert detection patterns |
| **SNMP** | 161 each Conpot persona | Conpot pysnmp | Reconnaissance, vendor fingerprinting via enterprise OIDs |
| **Vendor protocols** (S7, EtherNet/IP) | 102 + 44818 (Conpot personas) | Conpot built-ins | Vendor-coherent honeypot deception |

## Teaching modules (recommended order)

### Module 1 — Lab Orientation (30 min)

Students explore the dashboard's Overview, Architecture, and Live Data tabs.

- Identify the lab's three Pis on the **Network Topology** view (TP-Link → switch → Pis)
- Map the lab to standard zones using the **Purdue Reference Model**
- Watch a few seconds of **Live Modbus Wire Feed** and identify FC2 vs FC3 polls
- Read the active scenario's regulatory tags + description in the scenario strip
- Find the lab credentials via the **Lab Credentials** panel

**Deliverable:** sketch the lab on paper showing every Pi, the Conpot personas, and the tailscale uplink in their correct Purdue levels.

### Module 2 — Modbus Fundamentals (45 min)

Students see Modbus on the wire and understand its security properties.

- Run **`test-modbus-read-sweep`** to see all 8 registers + 8 coils from both endpoints
- Capture a 60 s pcap (**Live Data tab → Capture softplc-2**), open in Wireshark
- Identify MBAP header fields (transaction ID, protocol, length, unit ID)
- Identify FC3 request structure (start address + count) and FC3 response (byte count + register values)
- Note the absence of authentication, encryption, replay protection

**Deliverable:** filter pcap to FC3 responses, calculate the engineering value (raw × scale) for one tank-level reading, and verify it matches the synoptic.

### Module 3 — The Attack: Modbus Write Playground (30 min)

Students perform a real Modbus write attack and watch the consequences.

- Open Teaching tab → **Modbus Write Playground**
- Walk through **walkthrough: ATTACK · Rogue Modbus write** (water-treatment scenario)
- Observe the FC6 frame in the wire feed, the synoptic update, the WRITES OVERRIDE badge
- Compare against the Oldsmar 2021 real-world incident citation
- **Cleanup**: Clear sensor-sim overrides

**Deliverable:** describe the threat model — what an attacker needs (network access), what they do not need (auth credentials, exploits, malware).

### Module 4 — Detection: Forensic pcap Analysis (45 min)

Students learn to spot the attack from artifacts after the fact.

- Run the **DETECT · Forensic analysis** walkthrough
- Use `tshark -r capture.pcap -Y 'modbus.func_code == 6'` to extract write frames
- Cross-reference against the **Audit Log** panel to attribute the dashboard-side action
- Build an indicator-of-compromise (IoC) document: src IPs, register addresses, value-out-of-range thresholds

**Deliverable:** a written IoC for "rogue Modbus write" with specific pcap signatures, log correlations, and dashboard alerts.

### Module 5 — Defense: Network Segmentation (60 min)

Students implement a control and verify it works.

- Run the **DEFEND · Restrict Modbus to known masters** walkthrough
- Apply iptables rules on softplc-2 to allow Modbus only from softplc-1's IP
- Re-run the attack from Module 3 → verify it now fails
- Observe the OTLAB-MODBUS-DROP entries in `journalctl`
- Discuss real-world equivalents: Tofino, Hirschmann Eagle, Belden Tofino OT firewalls

**Deliverable:** iptables ruleset that allows reads from anywhere but writes only from a defined master. Test results showing reads succeed + writes fail.

### Module 6 — Honeypot Operations (45 min)

Students see deception technology in action.

- Survey the three Conpot personas (Siemens .50, Schneider .51, Rockwell .52)
- Run **`test-snmp-fingerprint.sh`** to probe each persona's vendor enterprise OIDs
- Observe their IPs appearing in the **Honeypot Fabric** intel panel within ~8 seconds
- Capture honeypot-host pcap → see the full forensic record of the scan
- Discuss why honeypots are "tripwires" — any external connection is high-confidence-malicious

**Deliverable:** description of what happens at the SOC when a Conpot persona logs an external connection.

### Module 7 — DNP3 + Utility-Sector Specifics (60 min)

Students compare DNP3 to Modbus and learn utility-specific attack patterns.

- Run **`test-dnp3-scan.py`** against softplc-2:20000
- Read the test script's source — understand link-layer framing (0x05 0x64 start, CRC, addressing)
- Switch the lab to `power-substation` scenario, re-explore the synoptic + walkthroughs
- Walk through **ATTACK · Aurora-style out-of-phase reclose**
- Discuss DNP3 Secure Authentication (SAv5/SAv6) as the production answer

**Deliverable:** explanation of why a Wireshark filter on DNP3 + analysis of the actual frames captured during the test script run.

### Module 8 — Process Anomaly Detection (45 min)

Students design physics-aware detection rules.

- Walk through **DETECT · Identify spoofed pressure via cross-reference** (gas-pipeline scenario)
- Discuss the difference between IT IDS (signature-based) and OT IDS (physics-based)
- Reference Claroty / Dragos / Nozomi commercial OT IDS products
- Use the **Inject Fault** panel to trigger a heartbeat freeze, observe the watchdog detection

**Deliverable:** three detection rules that combine state from multiple sensors / coils / thresholds to detect a single attack. Describe in pseudocode.

### Module 9 — Resilience + Recovery (30 min)

Students practice incident response and recovery.

- Trigger a fault (heartbeat pause), observe the dashboard's red indicators
- Capture a pcap of the period — that's the incident-evidence package
- Use the **Cohort Reset** button to restore the lab to clean state
- Discuss real-world IR: who gets called, what evidence is preserved, how recovery is validated

**Deliverable:** a 1-page incident-response runbook tailored to one of the scenarios.

## MITRE ATT&CK for ICS coverage

Each walkthrough is tagged with its MITRE ATT&CK for ICS technique ID. Currently exercised:

| Technique | Where | Scenarios |
|---|---|---|
| T0814 — Denial of Service | test-modbus-write-storm | water |
| T0830 — Adversary-in-the-Middle | test-modbus-replay, attack-replay-rtu-radio | water, gas |
| T0831 — Manipulation of Control | attack-rogue-write, attack-aurora | water, power |
| T0855 — Unauthorized Command Message | attack-breaker-trip | power |
| T0856 — Spoof Reporting Message | attack-relay-setpoint, attack-overpress-spoof | power, gas |
| T0858 — Change Operating Mode | attack-disable-safety-trip | gas |
| T0859 — Valid Accounts | attack-replay-rtu-radio | gas |

Each technique has at least one defense walkthrough that demonstrates a corresponding D3FEND mitigation.

## Test Library

The dashboard's **Teaching tab → Test Library** auto-discovers `~/lab/tests/*.{py,sh}` and lets students click-to-run each. Current catalog:

| Test | What it does | Detection signature |
|---|---|---|
| `test-modbus-read-sweep` | FC1/2/3/4 across both Modbus endpoints | Reads to addresses outside master's known poll range |
| `test-modbus-write` | Single FC5/FC6 write to chosen target | Any FC5/6 from non-master src |
| `test-modbus-write-storm` | 200-write flood with auto-cleanup | Burst > 5 writes/sec |
| `test-modbus-replay` | Extract FC5/6 from pcap, replay them | Two identical FC6 PDUs from same src at separated timestamps |
| `test-dnp3-scan` | DNP3 link-status + READ Class 0 | DNP3 from any non-master IP |
| `test-snmp-fingerprint` | Vendor enterprise OIDs against Conpot personas | Any SNMP query to .50/.51/.52 |
| `test-arp-discovery` | Passive + active /24 enumeration | Burst of ICMP echo to 254 hosts |
| `test-tls-cipher-scan` | nmap ssl-enum-ciphers + cert dump | Multiple TLS handshakes with varied cipher offers |
| `test-ssh-bruteforce` | Controlled failed-auth generator | ≥5 sshd failures in 60 s |

## Audit log + accountability

Every action a student or instructor takes through the dashboard (Modbus writes, fault injections, service restarts, reboots, captures, cohort resets, test runs) is recorded to `/home/otuser/lab/dashboard/audit.db` with timestamp + user + action + target + outcome.

The **Live Data tab → Audit Log** panel lets you filter the log by action or user. This is the operator-action telemetry that pairs with the dashboard's failed-SSH attack telemetry — together they cover both who did what and what the lab observed.

For DEF CON booth use: review the audit log between cohorts to understand what each visitor explored. Use the **Cohort Reset** button to start each new student with a clean slate.

## Adding new content

The curriculum is data-driven — adding new exercises does not require dashboard code changes:

- **New walkthrough**: add an entry to the relevant `plc/scenarios/<name>.json` under the `walkthroughs` array. Required fields: `id`, `name`, `category` (attack/detection/defense), `minutes`, `mitre`, `real_world`, `prereq`, `steps[]`, `cleanup`. Each step needs `title` and `body`; optional: `command`, `highlight`, `expected`.
- **New test**: drop a `test-*.{py,sh}` script into `plc/tests/`. The dashboard auto-discovers it. Header docstring becomes the description. Re-run `install-sensor-sim.sh` to deploy.
- **New scenario**: copy one of the existing JSONs as a template, edit waveforms + thresholds + risks + walkthroughs, drop into `plc/scenarios/`. Re-run `install-sensor-sim.sh`.
- **New protocol**: needs new code (see `plc/dnp3-outstation.py` as a template). Long-form work, but the dashboard's probe + card mechanism is generic — just add a probe call + display field.

## Hardware requirements + remote-feasibility

The full curriculum runs on three Raspberry Pis with no extra hardware. All exercises are accessible:

- **In-person at the booth**: students sit at a laptop on MFCTP WiFi, connect to the dashboard at `http://10.20.30.49:8000/`
- **Remote**: instructor on tailnet can drive the lab from anywhere; students join via tailscale subnet route (see `OTLab-private/tailscale.md`)
- **Air-gapped**: the lab segment can run fully isolated; tailscale is optional, MFCTP optional

Phase 2 hardware (relays, indicators, pushbutton, real Velocio PLC) adds physical-loop teaching but every Module 1-9 above is doable with current hardware.
