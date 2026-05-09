# OTLab — Maple Ridge ICS Training Lab

Hands-on industrial control systems training lab for [ICS Village](https://icsvillage.com/) (DEF CON village). Built on Raspberry Pi, ESP32, and Arduino hardware, with a multi-vendor honeypot fabric that emulates a small municipal water treatment plant — plus a full-featured operator dashboard for live process visibility, attack telemetry, and interactive teaching.

> **Status (2026-05-09):** Phase 0 (host provisioning) and the honeypot fabric are complete. Phase 1 (Modbus loop between master and outstation) is **live and stable** — currently collapsed onto `l1-plc-01` during the `l1-plc-02` backfill gap. **Naming schema standardized** to `<purdue-level>-<role>-<NN>`; legacy hostnames (`softplc-1`/`softplc-2`/`honeypot-host`) preserved as `/etc/hosts` aliases for one transition window. The Pi 5 + NVMe (formerly `softplc-2`) has been **repurposed as `l3-mon-01`**, the L3 monitoring host (dashboard + planned Suricata IDS + planned Apache Guacamole). **Tailscale** runs on all 3 Pis with subnet routing via l3-mon-01, so the lab is fully reachable from anywhere on the operator's tailnet. The **OTLab Dashboard** (Flask + vanilla HTML/JS) is a complete teaching surface — synoptic, system health, real-time Modbus wire feed, attack telemetry, Modbus write playground, fault injection, audit log, and a Test Library that auto-discovers runnable exercise scripts. **Multi-scenario substrate** ships with three OT verticals (water-treatment / power-substation / natural-gas-pipeline) swappable in one command. **DNP3 outstation** runs on l1-plc-01:20000 alongside the Modbus listener, giving the lab utility-vertical wire surface. Each scenario has 5-8 substantive **Attack / Detect / Defend walkthroughs** with MITRE ATT&CK for ICS technique IDs and real-world incident citations (Oldsmar, Ukraine 2015, Aurora, Industroyer, Triton, Colonial Pipeline). See [`docs/curriculum.md`](docs/curriculum.md) for the syllabus and [`docs/naming-schema.md`](docs/naming-schema.md) for the canonical naming convention. **Phase 2** (physical I/O — pushbutton + relay-driven AD16 indicators + LED strip) is blocked on the 24 V PSU; software side keeps moving in parallel.

## What's here

```
.
├── docs/                  # Build documentation, phase write-ups
│   ├── lab-architecture.md   ← start here
│   ├── phase-1-modbus-loop.md
│   └── arduino-setup.md
├── dashboard/             # Flask + vanilla HTML/JS dashboard (runs on l3-mon-01)
├── honeypot/              # Conpot deployment, ready to bootstrap onto a Pi
├── plc/                   # OpenPLC programs (.st), sensor-sim.py, dnp3-outstation.py, scenarios
├── scripts/               # Bootstrap + install scripts (deploys the whole lab)
├── reference/             # Diagrams, address maps, BOMs, vendor OID list, pcaps
└── requirements.txt       # Python deps for the lab venv on each Pi
```

## The lab in one paragraph

Three Raspberry Pi hosts on a dedicated lab segment (`10.20.30.0/24`):

| Host | Hardware | Role |
|---|---|---|
| `l1-plc-01` | Pi 5 + Freenove GPIO breakout | L1 — OpenPLC master + sensor-sim outstation + DNP3 outstation (polyfunctional during the `l1-plc-02` backfill gap) |
| `l3-mon-01` | Pi 5 + Waveshare PCIe-NVMe HAT | L3 monitoring — dashboard + tailscale subnet router; Suricata IDS + Apache Guacamole planned next |
| `l1-hp-01` | Pi 3 B+ | L1 deception — Conpot Docker host running 3 vendor personas (macvlan) |
| `l1-plc-02` | future Pi | L1 outstation backfill — restores the master ↔ outstation network split |

Naming follows `<purdue-level>-<role>-<NN>` — see [`docs/naming-schema.md`](docs/naming-schema.md). Legacy hostnames (`softplc-1`/`softplc-2`/`honeypot-host`/`ops-host`) remain as `/etc/hosts` aliases for one transition window.

The honeypot fabric presents the **Maple Ridge Treatment Plant** — a fictional municipal water utility with three subsystems on three different vendor controllers (Siemens S7-200 distribution pumps, Schneider M340 chemical-room HVAC, Allen-Bradley CompactLogix chlorination dosing). All three speak vendor-coherent protocols, return vendor-correct SNMP enterprise OIDs, and serve vendor-themed multi-page HTTP admin UIs with internally-consistent process data.

Hardware on hand for later phases includes a Velocio Ace 1600 PLC, two Arduino UNOs with relay shields, three Lonely Binary ESP32-S3 boards, MAX485 transceivers, a Waveshare RS485-to-Ethernet gateway, the ELEGOO 37-sensor kit, AD16 indicators, an LED strip, a Tecmojo 4U rack, and Mean Well DIN-rail power supplies.

## Quick links

- **[Curriculum](docs/curriculum.md)** ← syllabus, modules, MITRE ATT&CK coverage, scenarios
- **[Lab architecture](docs/lab-architecture.md)** — comprehensive build doc covering hosts, network, honeypot personas, process data, deployment, ops, validation tests, phase plan
- **[Phase 1 — Modbus loop](docs/phase-1-modbus-loop.md)** — the master/slave loop between the two real PLCs (complete)
- **[Dashboard](dashboard/README.md)** — operator dashboard architecture + endpoint reference
- **[Test Library](plc/tests/README.md)** — runnable Attack/Detect/Defend exercise scripts
- **[Honeypot fabric](honeypot/README.md)** — Conpot persona configs + cross-Pi verification battery
- **[Scripts](scripts/README.md)** — full bootstrap workflow + disaster recovery runbook

## Deploying the lab from scratch

Every Pi runs two non-root accounts: `otadmin` (NOPASSWD sudo, what scripts use) and `otuser` (non-sudo, what operators log in as). Created from a fresh Pi by `bootstrap-users.sh` against whatever user Pi Imager set during imaging.

```bash
# === one-time per Pi: create otadmin / otuser, disable cloud-init,
#                       fix wifi powersave, install SSH keys ===
ssh-copy-id <imager-user>@RASPLC01.local         # password prompt once
./scripts/bootstrap-users.sh <imager-user>@RASPLC01.local

# === l1-plc-01: provision OS + OpenPLC + lab venv, master role, sensor-sim, DNP3 ===
./scripts/bootstrap-pi.sh                     otadmin@RASPLC01.local             # ~15-20 min (matiec compile)
OPENPLC_PASSWORD='P@ssw0rd!' \
  ./scripts/bootstrap-l1-plc-role.sh         otadmin@RASPLC01.local  l1-plc-01  # ~30 s
./scripts/install-sensor-sim.sh               otadmin@RASPLC01.local             # ~5 s — sensor-sim + scenarios + tests/
./scripts/install-dnp3.sh                     otadmin@RASPLC01.local             # ~5 s — DNP3 outstation on :20000

# === l3-mon-01: L3 monitoring host (dashboard + Suricata + Guacamole) ===
./scripts/bootstrap-pi.sh                     otadmin@RASPLC02.local
./scripts/bootstrap-l3-mon-role.sh            otadmin@RASPLC02.local             # Docker, suricata pkg, lab venv
./scripts/install-suricata.sh                 otadmin@RASPLC02.local             # IDS rules + EVE JSON output
./scripts/install-guacamole.sh                otadmin@RASPLC02.local             # clientless RDP/SSH gateway
./scripts/install-dashboard.sh                otadmin@RASPLC02.local --target-host=l3-mon-01

# === honeypot fabric ===
./scripts/bootstrap-l1-hp-role.sh             otadmin@l1-hp-01.local             # ~3-5 min first run

# === future: l1-plc-02 backfill ===
# (when a 4th Pi lands, sensor-sim + DNP3 move off l1-plc-01 onto l1-plc-02)
# ./scripts/bootstrap-l1-plc-role.sh otadmin@l1-plc-02.local l1-plc-02
# ./scripts/install-sensor-sim.sh    otadmin@l1-plc-02.local
# ./scripts/install-dnp3.sh          otadmin@l1-plc-02.local
```

Total time per fresh Pi: ~20 min for the L1 PLC (OpenPLC compile is the long pole), ~10 min for l3-mon-01, ~5 min for l1-hp-01. All scripts are idempotent — safe to re-run any time to bring a Pi back to canonical state.

The OpenPLC web UI password (`OPENPLC_PASSWORD`) and dashboard / lab WiFi password default to the lab's intentionally-public convention `P@ssw0rd!` (matches MFCTP). Rotate per DEF CON event so creds don't leak between cohorts.

After deploy, browse the dashboard at:
- `https://10.20.30.49:8000/` (lab segment)
- `https://192.168.120.19:8000/` (mgmt network — IP varies by your home WiFi)
- `https://rasplc02:8000/` (anywhere on your tailnet via subnet route)

Login: `otlab` / `P@ssw0rd!`.

Full deployment walkthrough + disaster recovery runbook: [`scripts/README.md`](scripts/README.md).

## Operating the honeypot fabric

The full bootstrap chain above takes care of l1-hp-01. To rebuild manually on a Pi 3 B+ (or any arm64/amd64 Docker host on the lab segment), see [`honeypot/README.md`](honeypot/README.md). Validation probes (cross-Pi snmpwalk / curl / Modbus reads) are documented there too.

## Operating the dashboard

Once `install-dashboard.sh` has run, the dashboard is a single-page browser app reachable at the URLs above. It surfaces, in roughly top-down order:

- **Process schematic** — animated SVG synoptic of the Maple Ridge process (tank, thermometer, pressure gauge, pump, status panel)
- **Network topology** — auto-discovered: internet → TP-Link → switch → 3 Pis → Conpot personas as macvlan children → other ARP-discovered DHCP clients
- **Network / PLC / system-health / honeypot rows** — live cards with sparklines, system metrics, tailscale info, attack telemetry
- **Live Modbus wire feed** — Wireshark-lite real-time decoded packets (SSE-streamed)
- **Modbus write playground** — fire real FC5/FC6 writes against sensor-sim or l1-plc-01 mirror (teaching artifact: Modbus has no auth)
- **Cohort reset** — single button between booth visitors to clear faults + writes + pcaps + restart services
- **Inject fault** — pause sensor-sim / freeze heartbeat / force HI_TEMP_ALARM (demonstrates the SCADA cause-and-effect chain on the synoptic)
- **Pcap captures** — 60 s tcpdump per Pi, downloadable from the panel
- **Lab credentials** — collapsible at-a-glance MFCTP / OpenPLC / dashboard creds

Architecture, endpoint reference, and feature deep-dive: [`dashboard/README.md`](dashboard/README.md).

## License

[MIT](LICENSE). Documentation and code free to fork, adapt, and use in your own training environments.

## Contributing

This is a personal/ICS Village lab build. If you've found something useful and want to suggest improvements or share what you've built on top, open an issue or PR.
