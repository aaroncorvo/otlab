# OTLab — Maple Ridge ICS Training Lab

Hands-on industrial control systems training lab for [ICS Village](https://icsvillage.com/) (DEF CON village). Built on Raspberry Pi, ESP32, and Arduino hardware, with a multi-vendor honeypot fabric that emulates a small municipal water treatment plant — plus a full-featured operator dashboard for live process visibility, attack telemetry, and interactive teaching.

> **Status (2026-05-09):** Phase 0 (host provisioning) and the honeypot fabric are complete. **Lab pivoting to dual-mode (virtual + physical) architecture** per the team's whiteboard plan: `l3-mon-01` (Pi 5 16GB + NVMe) becomes a **virtualization host** running the entire DMZ + Process Control fabric as containers (ContainerLab orchestration), while physical Pis (`l1-plc-01` + `l1-hp-01`) extend the virtual lab with on-the-wire authenticity and Phase 2 hardware. **V1 codebase shipped** ([`docs/virtualization.md`](docs/virtualization.md), [`virtual/`](virtual/), `scripts/install-virtual-lab.sh`) — paper design pending physical execution. **V2** layers in Authentik (IdP/SSO), Ignition SCADA (Maker edition), Apache Guacamole, and Suricata IDS. **V3** adds CODESYS Control SL + Web HMI. Naming schema standardized to `<purdue-level>-<role>-<NN>` ([`docs/naming-schema.md`](docs/naming-schema.md)); legacy hostnames preserved as aliases for one transition window. **Tailscale** runs on all 3 Pis with subnet routing via l3-mon-01 (advertises both `192.168.75.0/24` DMZ and `10.20.30.0/24` PCN). The **OTLab Dashboard** (Flask + vanilla HTML/JS) keeps its role as lab-admin + curriculum surface (Test Library, Audit Log, Modbus Write Playground, Fault Injection, Cohort Reset); Ignition will own SCADA in V2. **Multi-scenario substrate** ships with three OT verticals (water-treatment / power-substation / natural-gas-pipeline) swappable in one command. Each scenario has 5-8 substantive **Attack / Detect / Defend walkthroughs** with MITRE ATT&CK for ICS technique IDs and real-world incident citations (Oldsmar, Ukraine 2015, Aurora, Industroyer, Triton, Colonial Pipeline). See [`docs/curriculum.md`](docs/curriculum.md) for the syllabus, [`docs/virtualization.md`](docs/virtualization.md) for the dual-mode architecture, and [`docs/architecture-evolution.md`](docs/architecture-evolution.md) for phasing. **Physical Phase 2** (pushbutton + relay-driven AD16 indicators + LED strip on `l1-plc-01`) is still blocked on the 24 V PSU; virtualization side keeps moving in parallel.

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

Three Raspberry Pi hosts in **dual-mode** architecture: one virtualizes the core (containerlab), two physical Pis extend it.

| Host | Hardware | Role |
|---|---|---|
| `l3-mon-01` | Pi 5 16GB + Waveshare PCIe-NVMe HAT | **Virtualization host** — runs the entire DMZ (`192.168.75.0/24`) + PCN (`10.20.30.0/24`) fabric as containers: firewall, dual virtual OpenPLC, sensor-sim, DNP3, dashboard. V2 adds Ignition SCADA, Authentik, Guacamole, Suricata. V3 adds CODESYS. Tailscale subnet router. |
| `l1-plc-01` | Pi 5 + Freenove GPIO breakout + Phase 2 hardware | **Physical OpenPLC** — real Modbus/DNP3 on the wire, real GPIO/relays/buttons. Joins the virtual `pcn-br0` via macvlan in V2. |
| `l1-hp-01` | Pi 3 B+ | **Physical Conpot fabric** — 3 vendor personas on macvlan (Siemens, Schneider, Rockwell). Joins `pcn-br0` in V2. |

Naming follows `<purdue-level>-<role>-<NN>` — see [`docs/naming-schema.md`](docs/naming-schema.md). Legacy hostnames (`softplc-1`/`softplc-2`/`honeypot-host`/`ops-host`) remain as `/etc/hosts` aliases for one transition window. The previously-planned `l1-plc-02` backfill is **obsolete** — virtual OpenPLC #2 (`plc-2-virt`) covers that role.

The honeypot fabric presents the **Maple Ridge Treatment Plant** — a fictional municipal water utility with three subsystems on three different vendor controllers (Siemens S7-200 distribution pumps, Schneider M340 chemical-room HVAC, Allen-Bradley CompactLogix chlorination dosing). All three speak vendor-coherent protocols, return vendor-correct SNMP enterprise OIDs, and serve vendor-themed multi-page HTTP admin UIs with internally-consistent process data.

Hardware on hand for later phases includes a Velocio Ace 1600 PLC, two Arduino UNOs with relay shields, three Lonely Binary ESP32-S3 boards, MAX485 transceivers, a Waveshare RS485-to-Ethernet gateway, the ELEGOO 37-sensor kit, AD16 indicators, an LED strip, a Tecmojo 4U rack, and Mean Well DIN-rail power supplies.

## Quick links

- **[Virtualization architecture](docs/virtualization.md)** ← dual-mode (virtual core + physical extensions), ContainerLab topology, phasing
- **[Naming schema](docs/naming-schema.md)** — canonical hostnames, IPs, services
- **[Curriculum](docs/curriculum.md)** — syllabus, modules, MITRE ATT&CK coverage, scenarios
- **[Lab architecture](docs/lab-architecture.md)** — comprehensive build doc covering hosts, network, honeypot personas, process data, deployment, ops, validation tests, phase plan
- **[Architecture evolution](docs/architecture-evolution.md)** — phase plan + segmentation history + decision log
- **[Phase 1 — Modbus loop](docs/phase-1-modbus-loop.md)** — the master/slave loop between PLCs
- **[Dashboard](dashboard/README.md)** — lab-admin + curriculum surface; endpoint reference
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

# === l1-plc-01: physical OpenPLC + Phase 2 hardware ===
./scripts/bootstrap-pi.sh                     otadmin@l1-plc-01.local         # ~15-20 min (matiec compile)
OPENPLC_PASSWORD='P@ssw0rd!' \
  ./scripts/bootstrap-l1-plc-role.sh         otadmin@l1-plc-01.local  l1-plc-01

# === l3-mon-01: virtualization host (containerlab + DMZ + PCN fabric) ===
./scripts/bootstrap-pi.sh                     otadmin@l3-mon-01.local
./scripts/bootstrap-l3-mon-role.sh            otadmin@l3-mon-01.local         # Docker, lab venv, etc.
./scripts/install-virtual-lab.sh              otadmin@l3-mon-01.local         # containerlab + builds + deploys topology
                                                                              # ~20-30 min first run (Docker image builds)

# === l1-hp-01: physical Conpot fabric ===
./scripts/bootstrap-l1-hp-role.sh             otadmin@l1-hp-01.local          # ~3-5 min first run
```

Total time per fresh provision: ~20 min for `l1-plc-01` (OpenPLC compile), ~30 min for `l3-mon-01` (image builds), ~5 min for `l1-hp-01`. All scripts are idempotent — safe to re-run any time. The `install-virtual-lab.sh` deployment includes a `containerlab destroy` first, so re-runs reset to clean topology state.

The OpenPLC web UI password (`OPENPLC_PASSWORD`) and dashboard / lab WiFi password default to the lab's intentionally-public convention `P@ssw0rd!` (matches MFCTP). Rotate per DEF CON event so creds don't leak between cohorts.

After deploy, browse the dashboard at:
- `https://l3-mon-01:8000/` or `https://192.168.75.40:8000/` (DMZ — operator side)
- `https://<wifi-ip>:8000/` (mgmt network — IP varies by your home WiFi)
- `https://l3-mon-01:8000/` (anywhere on your tailnet via subnet route)

Login: `otlab` / `P@ssw0rd!`.

V2 will add Ignition SCADA at `https://l3-mon-01:8088/` and Apache Guacamole at `https://l3-mon-01:8443/guacamole/`.

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
