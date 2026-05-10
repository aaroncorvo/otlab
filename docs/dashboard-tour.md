# OTLab Dashboard — Tab Tour

The dashboard at `https://l3-mon-01:8000/` is the operator surface for
the lab. Login: `otlab` / `P@ssw0rd!` (lab convention; rotate per
event). Auto-refreshes every 3-8 s depending on the data type.

Seven top-level tabs, in order they appear:

| # | Tab | Audience | Purpose |
|---|---|---|---|
| 1 | Overview | Everyone | Live process state + system health at a glance |
| 2 | Architecture | Students learning the stack | Purdue model, network topology, credentials |
| 3 | IDS | SOC analysts, instructors | Suricata alerts dashboard |
| 4 | Firewall | Network admins | iptables, NAT, conntrack, DNS forwarder |
| 5 | DHCP | Sysadmins | Per-zone leases + reservations + transactions |
| 6 | Live Data | Forensics, advanced students | Wire feed, audit log, pcap captures |
| 7 | Teaching | Cohort instructor | Risks, walkthroughs, write playground, fault inject, cohort reset |

---

## 1. Overview

The default landing tab. Top-down:

**Process Schematic (synoptic)** — animated SVG of the Maple Ridge
water treatment process. Tank level, water temp gauge with color bands
(65-73 °F normal, 73-75 warn, >75 alarm), discharge pressure dial,
pump animation, status panel (RUN, HI_TEMP_ALARM, LINK ok/loss,
heartbeat pulse). Reads from `modbus-master.master_state` (the master
container's tick file) — falls back to direct `sensor-sim` probe if
the master is offline.

**Process Control · PLCs** — three cards for the physical hosts:
`l1-plc-01` (with OpenPLC web link + heartbeat/link/run from Modbus),
`l3-mon-01` (the virt host's own status), `l1-hp-01`.

**PCN Services** — five cards for the virtual L1 fabric:
`modbus-master` (live poll rate, polls_ok/err, last hr/coils),
`sensor-sim`, `dnp3-outstation`, `plc-1-virt`, `plc-2-virt`. Each
shows ping ms + role-specific telemetry.

**Honeypot Fabric** — three Conpot persona cards:
`siemens-PS4` (`:80` web + `:102` S7), `schneider-M340` (`:80` web +
`:502` Modbus), `rockwell-CHEM` (`:80` web + `:44818` EthernetIP).
Card state degrades to "warn" if some ports are open and others
aren't — common when only the HTTP UI persona is enabled in
`conpot.cfg` but the protocol persona isn't.

**Lab Infrastructure** — three cards: `fw-dmz-pcn` (firewall + DNS
forwarder reachability on `.1` in both zones), `dhcp-dmz` and
`dhcp-pcn` (active lease counts). Click DHCP cards on the dashboard
or visit the dedicated DHCP tab for full lease tables.

**Network** — wan / mgmt_gw / dmz_gw / pcn_gw ping-only health cards.

**Use it for**: at-a-glance "is the lab healthy" check, quick links
to OpenPLC web UIs, watching the synoptic respond to the Teaching
tab's fault-injection buttons.

---

## 2. Architecture

Read-only reference views. Useful for teaching the Purdue model and
showing students where each lab artifact sits.

**Purdue Reference Model** — 6-level hierarchy (L0 sensors → L5
internet) with the OTLab's actual assets placed at their canonical
level. Trust-boundary lines drawn at L1↔L2 and L3↔L4↔L5 (the
well-known segmentation choke points every ICS curriculum teaches).
Color-coded: red for the firewall conduit, blue for L2/L3 zones,
gray for "not deployed yet" assets (Ignition, Authentik,
Guacamole, CODESYS).

**Network Topology** — auto-discovered diagram: WAN gateway → lab
switch → 3 Pis → containers as children. Updates as DHCP leases
populate.

**Lab Credentials** — collapsible block listing default creds
(WiFi, OpenPLC, dashboard, Cockpit/Portainer accounts). Stays
collapsed by default to keep the screen clean during demos.

---

## 3. IDS

Suricata alerts dashboard. Reads `/var/log/suricata/eve.json`
(stream-parsed so it scales to 50 MB+ files).

**Counts strip** — four big number cards: total in window / 5 min
/ 1 hour / 24 hours.

**Last 24 Hours timeline** — 24 vertical bars, one per hour. Hover
to see the per-hour count. Bar 23 is the current hour.

**Top Signatures** — table of (signature_id, signature, severity,
count). For a typical lab session you'll see:
- `OTLAB-1004` — Modbus FC6 write to sensor-sim from non-master
- `OTLAB-1005` — Modbus FC15 multi-coil write
- `OTLAB-1006` — Modbus FC16 multi-register write
- `OTLAB-4001` — SSH brute force

**Top Source IPs** — top attacker source IPs by alert count.
Note: in V2.y+ the firewall does SNAT for DMZ→PCN, so DMZ-originated
attacks appear with src=`10.20.30.1` (the firewall) rather than the
real DMZ client.

**Top Targets** — `dest_ip:port` pairs sorted by hit count.

**Recent Alerts** — newest 25 alerts in a table. Severity-1 alerts
are highlighted red; severity-2 amber.

**Use it for**:
- After firing a Modbus write attack, confirm the rule fires
- Showing students that a passive IDS catches even unauthorized
  writes that succeed on the wire (Modbus has no auth — Suricata
  is the audit layer)
- Comparing pre/post counts to demonstrate detection coverage

---

## 4. Firewall

Live view of the firewall container's policy + state.

**Firewall Policy** — chain selector with 5 buttons: FORWARD,
INPUT, OUTPUT, NAT (POSTROUTING), NAT (PREROUTING). Click a button
to see the live `iptables -nvL <chain>` output (with packet/byte
counters). Refreshed every 5 s by the firewall sidecar exporter.

The most useful chain is **FORWARD** — shows the OTLab security
policy in action:
```
ACCEPT     RELATED,ESTABLISHED          (return traffic)
ACCEPT     192.168.75.0/24 -> 10.20.30.0/24 tcp dpt:22,80,502,5020,8080,20000
ACCEPT     192.168.75.0/24 -> 10.20.30.0/24 icmp
ACCEPT     10.20.30.0/24 -> eth0       (PCN egress NAT)
ACCEPT     192.168.75.0/24 -> eth0      (DMZ egress NAT)
LOG        prefix "OTLAB-FW-DROP: "
DROP       all
```

The packet counters tick up in real time as traffic flows — useful
teaching artifact ("watch the counters change as we run the attack").

**NAT POSTROUTING** shows the SNAT rule that lets the DMZ reach
physical Pis (sources DMZ traffic as `.1` so Pis can reply on their
local subnet) plus the MASQUERADE rules for WAN egress.

**Conntrack Snapshot** — `conntrack -C` count + tail of `conntrack -L`
showing the top 50 active flows. Lets students see live NAT
translations and connection-tracking state.

**DNS Query Stats** — total queries through the firewall's dnsmasq
forwarder, top 10 names, top 10 sources. The firewall is the only
DNS resolver for both zones (DHCP servers advertise `.1` as DNS),
so this surfaces every internal name resolution. Teaching artifact:
"DNS exfiltration detection — every query lands here."

**Recent DNS Queries** — newest 50 queries with type / name / source.

**Use it for**:
- Showing the FORWARD policy enforcing zone segmentation
- Demonstrating that DMZ → PCN is allowed but PCN → DMZ is
  blocked except for established responses
- Watching SNAT rule packet counters tick during a cross-segment
  Modbus poll
- Spotting unusual DNS patterns (typosquatted domains, DGA-style
  queries, etc.)

---

## 5. DHCP

Per-zone DHCP server view. One block for the DMZ DHCP server, one
for the PCN.

**Each zone shows**:
- Zone metadata (subnet, scope, gateway, DNS — all clickable to
  copy)
- **Active leases** table — IP / MAC / hostname / time-to-expiry.
  Updated as clients renew or new clients DHCP.
- **Static reservations** table — IP / MAC / hostname pinned via
  `DHCP_HOSTS` in the topology YAML. The PCN zone ships with five
  reservations (l1-plc-01, l1-hp-01, three Conpot personas).
- **Recent transactions** — last 30 lines from dnsmasq's transaction
  log: DHCPDISCOVER / DHCPOFFER / DHCPREQUEST / DHCPACK /
  DHCPRELEASE / DHCPNAK. Useful for debugging "why did this device
  get this IP?"

**Use it for**:
- Confirming a new device acquired the expected reservation IP
- Spotting unauthorized devices on the wire (their lease shows up
  in the dynamic scope)
- Teaching the DHCP handshake — point at a transaction in the log
  and walk through DISCOVER → OFFER → REQUEST → ACK
- Audit trail of "which MAC was at which IP, and when"

**To add a reservation**: edit `virtual/topologies/otlab.clab.yaml`,
find the right zone's `DHCP_HOSTS` env var, add a `MAC,name,IP`
line, then `containerlab deploy --reconfigure`. See
[`docs/setup-from-scratch.md`](setup-from-scratch.md) Step 10.

---

## 6. Live Data

Forensics + raw observability tab.

**System Health** — three cards (l1-plc-01, l3-mon-01, l1-hp-01)
with full system metrics: CPU load, memory, disk, uptime,
temperature, network traffic counters. Auto-refreshes every 8 s.

**Live Modbus Wire Feed** — Wireshark-lite real-time decoded
Modbus packets streamed via Server-Sent Events. *Note: in V2.y+
this currently sniffs `eth0` inside the dashboard container,
which is the clab management network — Modbus traffic doesn't
traverse it. Tracked as a follow-up to add a sidecar sniffer
container on `pcn-br0`.*

**IDS Alerts** — same data as the IDS tab, in a more compact feed
view. Useful when you want IDS visible while doing other work
(Live Data tab is also where the wire feed and audit log live, so
this is a "single pane of glass" view).

**Audit Log** — every dashboard action (Modbus writes, fault
injects, reboots, captures) logged to a SQLite database with
timestamp + user + action + params + outcome. Filterable by
action and user.

**Pcap Captures** — buttons to launch a 60-second `tcpdump`
capture on l1-plc-01 / l3-mon-01 / l1-hp-01. Captures appear in
the list as they finish, downloadable as `.pcap` for offline
analysis in Wireshark.

**Use it for**:
- Forensic reconstruction after a teaching exercise
- "Show me everything the previous cohort did" via audit log
- Exporting pcaps for follow-up Wireshark labs

---

## 7. Teaching

The cohort instructor's panel. Where exercises live.

**Risks · Attack Surface** — table of intentionally-vulnerable
attack vectors in the lab, with severity + MITRE ATT&CK for ICS
technique IDs + a "this is intentional" disclaimer for each.

**Incident Walkthroughs** — clickable cards that walk through
historical ICS incidents (Oldsmar 2021, Ukraine 2015, Aurora,
Industroyer, Triton, Colonial Pipeline) mapped onto OTLab artifacts:
"do these steps in this lab, and you'll reproduce the technique."

**Test Library** — runnable scripts from `~/lab/tests/` discovered
at runtime as cards. Click to run, output captured + displayed.
Includes Attack tests (FC6 write from non-master, FC15 coil flood,
DNS exfiltration) and Detect tests (verify Suricata fires, verify
firewall blocks).

**Modbus Write Playground** — interactive form to fire FC5 (coil)
or FC6 (register) writes against `sensor-sim` (persistent override)
or l1-plc-01 mirror (ephemeral). Teaching artifact: **Modbus has
no authentication** — anyone on the wire can change process state.

**Inject Fault** — three buttons: Pause sensor-sim (freezes
waveforms), Pause heartbeat (link goes red after 3 s), Force
HI_TEMP_ALARM (synoptic alarm pulses). Demonstrates SCADA
cause-and-effect: changes here ripple through to the Overview's
synoptic + l1-plc-01's master link counters.

**Cohort Reset** — single button that clears all faults + all
Modbus write overrides + deletes pcaps + restarts sensor-sim. One
click between booth visitors.

**Use it for**:
- Cohort sessions — start with the Walkthrough cards, run the
  Test Library exercises, end with a Cohort Reset
- Demonstrating "no-auth-Modbus" lessons via the Write Playground
- Showing the cause-and-effect chain: Inject Fault → synoptic
  reacts → IDS fires → Audit Log records it

---

## API endpoints (for scripting / external tools)

All require basic auth (`otlab` / `P@ssw0rd!`):

| Endpoint | Returns |
|---|---|
| `GET /api/status` | Snapshot of all card data + scenario + audit summary |
| `GET /api/ids/stats` | Counts + top sigs/sources/targets + hourly timeline + recent[25] |
| `GET /api/firewall` | Iptables chains + nat + conntrack + DNS stats |
| `GET /api/dhcp` | Per-zone leases + reservations + recent_tx |
| `GET /api/suricata/alerts` | Last 25 alerts (more compact than /ids/stats) |
| `GET /api/wire/recent` | Last 50 decoded Modbus frames |
| `GET /api/wire/stream` | SSE feed of live Modbus frames |
| `GET /api/health` | Aggregated system health for all hosts |
| `GET /api/audit?limit=100` | Audit log query |
| `POST /api/inject` | Set a fault flag |
| `POST /api/inject/clear` | Clear all faults |
| `POST /api/write` | Modbus write (coil or register) |
| `POST /api/write/clear` | Clear sensor-sim overrides |
| `POST /api/cohort/reset` | One-button cohort reset |
| `POST /api/capture/<host>` | Start a 60s pcap on a Pi |
| `GET /api/captures` | List completed pcap captures |
| `GET /api/capture-download/<id>` | Download a pcap |
| `POST /api/restart/<host>/<svc>` | Restart a systemd service |
| `POST /api/reboot/<host>` | Reboot a Pi |

Useful one-liner pattern:

```sh
curl -sk -u otlab:P@ssw0rd! https://l3-mon-01:8000/api/ids/stats \
  | python3 -m json.tool
```
