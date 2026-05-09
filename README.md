# OTLab — Maple Ridge ICS Training Lab

Hands-on industrial control systems training lab for [ICS Village](https://icsvillage.com/) (DEF CON village). Built on Raspberry Pi, ESP32, and Arduino hardware, with a multi-vendor honeypot fabric that emulates a small municipal water treatment plant — plus a full-featured operator dashboard for live process visibility, attack telemetry, and interactive teaching.

> **Status (2026-05-08):** Phase 0 (host provisioning) and the honeypot fabric are complete. Phase 1 (Modbus loop between the two real PLCs) is **live and stable** — softplc-1's OpenPLC polls softplc-2's sensor-sim every 100 ms, mirrors the values to local registers, and exposes them on its own Modbus TCP server with link-liveness telemetry. **softplc-2 boots from NVMe** (with the SD card retained as fallback). **Tailscale** runs on all 3 Pis (subnet route via softplc-2 advertises the lab segment), so the lab is fully reachable from anywhere on the operator's tailnet — no home-WiFi dependency. The **OTLab Dashboard** (Flask + vanilla HTML/JS, runs on softplc-2) gives you live process state, system health, attack telemetry, a real-time Modbus wire feed, fault injection, and Modbus write playground all in one place. **Phase 2** (physical I/O on the soft-PLCs — pushbutton + relay-driven AD16 indicators + LED strip) is blocked on the 24 V PSU arriving; software side can move ahead in parallel. **ESP32 #1** is at static `10.20.30.40` on the lab WiFi, firmware via Arduino IDE on a Windows laptop ([`docs/arduino-setup.md`](docs/arduino-setup.md)).

## What's here

```
.
├── docs/                  # Build documentation, phase write-ups
│   ├── lab-architecture.md   ← start here
│   ├── phase-1-modbus-loop.md
│   └── arduino-setup.md
├── dashboard/             # Flask + vanilla HTML/JS dashboard for softplc-2
├── honeypot/              # Conpot deployment, ready to bootstrap onto a Pi
├── plc/                   # OpenPLC programs (.st), sensor-sim.py, ESP32 sketches
├── scripts/               # Bootstrap + install scripts (deploys the whole lab)
├── reference/             # Diagrams, address maps, BOMs, vendor OID list, pcaps
└── requirements.txt       # Python deps for the lab venv on softplc-1/-2
```

## The lab in one paragraph

Three Raspberry Pi hosts on a dedicated lab segment (`10.20.30.0/24`):

| Host | Hardware | Role |
|---|---|---|
| `softplc-1` | Pi 5 + Freenove GPIO breakout | OpenPLC #1 — Modbus master polling sensor-sim |
| `softplc-2` | Pi 5 + Waveshare PCIe-NVMe HAT + 3-CH relay HAT | OpenPLC #2 + sensor-sim slave + dashboard host + tailscale subnet router |
| `honeypot-host` | Pi 3 B+ | Conpot Docker host running 3 vendor personas (macvlan) |

The honeypot fabric presents the **Maple Ridge Treatment Plant** — a fictional municipal water utility with three subsystems on three different vendor controllers (Siemens S7-200 distribution pumps, Schneider M340 chemical-room HVAC, Allen-Bradley CompactLogix chlorination dosing). All three speak vendor-coherent protocols, return vendor-correct SNMP enterprise OIDs, and serve vendor-themed multi-page HTTP admin UIs with internally-consistent process data.

Hardware on hand for later phases includes a Velocio Ace 1600 PLC, two Arduino UNOs with relay shields, three Lonely Binary ESP32-S3 boards, MAX485 transceivers, a Waveshare RS485-to-Ethernet gateway, the ELEGOO 37-sensor kit, AD16 indicators, an LED strip, a Tecmojo 4U rack, and Mean Well DIN-rail power supplies.

## Quick links

- **[Lab architecture](docs/lab-architecture.md)** — comprehensive build doc covering hosts, network, honeypot personas, process data, deployment, ops, validation tests, phase plan
- **[Phase 1 — Modbus loop](docs/phase-1-modbus-loop.md)** — the master/slave loop between the two real PLCs (complete)
- **[Dashboard](dashboard/README.md)** — operator dashboard architecture + endpoint reference
- **[Honeypot fabric](honeypot/README.md)** — Conpot persona configs + cross-Pi verification battery
- **[Scripts](scripts/README.md)** — full bootstrap workflow + disaster recovery runbook

## Deploying the lab from scratch

Every Pi runs two non-root accounts: `otadmin` (NOPASSWD sudo, what scripts use) and `otuser` (non-sudo, what operators log in as). Created from a fresh Pi by `bootstrap-users.sh` against whatever user Pi Imager set during imaging.

```bash
# === one-time per Pi: create otadmin / otuser, disable cloud-init,
#                       fix wifi powersave, install SSH keys ===
ssh-copy-id <imager-user>@RASPLC01.local         # password prompt once
./scripts/bootstrap-users.sh <imager-user>@RASPLC01.local

# === softplc-1: provision OS + OpenPLC + lab venv, then configure role ===
./scripts/bootstrap-pi.sh                     otadmin@RASPLC01.local             # ~15-20 min (matiec compile)
OPENPLC_PASSWORD='P@ssw0rd!' \
  ./scripts/bootstrap-openplc-role.sh         otadmin@RASPLC01.local  softplc-1  # ~30 s

# === softplc-2: same, plus sensor-sim service, plus the dashboard ===
./scripts/bootstrap-pi.sh                     otadmin@RASPLC02.local
OPENPLC_PASSWORD='P@ssw0rd!' \
  ./scripts/bootstrap-openplc-role.sh         otadmin@RASPLC02.local  softplc-2
./scripts/install-sensor-sim.sh               otadmin@RASPLC02.local             # ~5 s
./scripts/install-dashboard.sh                otadmin@RASPLC02.local             # ~30 s

# === honeypot fabric ===
./scripts/bootstrap-honeypot.sh               otadmin@honeypot-host.local        # ~3-5 min first run
```

Total time per fresh Pi: ~20 min for soft-PLCs (OpenPLC compile is the long pole), ~5 min for honeypot-host. All scripts are idempotent — safe to re-run any time to bring a Pi back to canonical state.

The OpenPLC web UI password (`OPENPLC_PASSWORD`) and dashboard / lab WiFi password default to the lab's intentionally-public convention `P@ssw0rd!` (matches MFCTP). Rotate per DEF CON event so creds don't leak between cohorts.

After deploy, browse the dashboard at:
- `https://10.20.30.49:8000/` (lab segment)
- `https://192.168.120.19:8000/` (mgmt network — IP varies by your home WiFi)
- `https://rasplc02:8000/` (anywhere on your tailnet via subnet route)

Login: `otlab` / `P@ssw0rd!`.

Full deployment walkthrough + disaster recovery runbook: [`scripts/README.md`](scripts/README.md).

## Operating the honeypot fabric

The full bootstrap chain above takes care of honeypot-host. To rebuild manually on a Pi 3 B+ (or any arm64/amd64 Docker host on the lab segment), see [`honeypot/README.md`](honeypot/README.md). Validation probes (cross-Pi snmpwalk / curl / Modbus reads) are documented there too.

## Operating the dashboard

Once `install-dashboard.sh` has run, the dashboard is a single-page browser app reachable at the URLs above. It surfaces, in roughly top-down order:

- **Process schematic** — animated SVG synoptic of the Maple Ridge process (tank, thermometer, pressure gauge, pump, status panel)
- **Network topology** — auto-discovered: internet → TP-Link → switch → 3 Pis → Conpot personas as macvlan children → other ARP-discovered DHCP clients
- **Network / PLC / system-health / honeypot rows** — live cards with sparklines, system metrics, tailscale info, attack telemetry
- **Live Modbus wire feed** — Wireshark-lite real-time decoded packets (SSE-streamed)
- **Modbus write playground** — fire real FC5/FC6 writes against sensor-sim or softplc-1 mirror (teaching artifact: Modbus has no auth)
- **Cohort reset** — single button between booth visitors to clear faults + writes + pcaps + restart services
- **Inject fault** — pause sensor-sim / freeze heartbeat / force HI_TEMP_ALARM (demonstrates the SCADA cause-and-effect chain on the synoptic)
- **Pcap captures** — 60 s tcpdump per Pi, downloadable from the panel
- **Lab credentials** — collapsible at-a-glance MFCTP / OpenPLC / dashboard creds

Architecture, endpoint reference, and feature deep-dive: [`dashboard/README.md`](dashboard/README.md).

## License

[MIT](LICENSE). Documentation and code free to fork, adapt, and use in your own training environments.

## Contributing

This is a personal/ICS Village lab build. If you've found something useful and want to suggest improvements or share what you've built on top, open an issue or PR.
