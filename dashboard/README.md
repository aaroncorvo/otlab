# OTLab Dashboard

Single-page status + control dashboard for the lab. Lives on `l3-mon-01` so it's reachable from the lab segment (`10.20.30.49:8000`), the mgmt network (`192.168.120.19:8000`), and via tailscale (`100.77.255.56:8000` / `rasplc02:8000`). Runs as `otuser` under systemd. Vanilla HTML/CSS/JS frontend, no build step — pedagogical and reload-immediate.

> **What this is:** an instructor-grade ops + teaching panel for the lab. Live process telemetry, system health, wire-level Modbus visibility, attack telemetry, and interactive controls (reboot, service restart, pcap capture, fault injection, Modbus writes, cohort reset). Browse it from anywhere on your tailnet.

## What's on the page (top → bottom)

1. **Process Schematic ("Maple Ridge — P&ID")** — animated SVG synoptic showing tank level, water-temp thermometer with color zones, sweeping pressure gauge, pump symbol, and a status panel with RUN / HI_TEMP_ALARM (pulsing red when active) / LINK l1-plc-01↔l3-mon-01 / heartbeat / link_loss. Reads from l1-plc-01's `:502` mirror — what the master sees.

2. **Network Topology** — auto-rendered SVG of the actual physical/logical lab plumbing: internet uplink → TP-Link router → switch → 3 Pis → Conpot personas hanging off l1-hp-01 as macvlan children → ARP-discovered other DHCP clients along the bottom row. Edge colors track card state on each leg; the Phase 1 Modbus loop arc is colored by `link_ok`. Other-client labels include vendor hints from a baked-in OUI prefix table (Raspberry Pi, TP-Link, Docker, Apple, etc.).

3. **Network row** — WAN (1.1.1.1 ping), Mgmt Gateway, Firewall (TP-Link ping at 10.20.30.1).

4. **Process Control** — l1-plc-01, l3-mon-01, l1-hp-01. Each soft-PLC card shows ping liveness, OpenPLC web-UI reachability, live Modbus reads in engineering units, **5-min sparklines** for tank/temp/press, RUN coil, HI_ALARM coil. Reboot button + per-service restart buttons (`↻ openplc`, `↻ sensor-sim`, `↻ otlab-dashboard`).

5. **System Health** — per-Pi: CPU%, mem%, disk %, disk size, SoC temp (color zones at 65/75 °C), uptime, load, failed systemd units, boot device (so you can tell at a glance which l3-mon-01 SD vs NVMe boot is active), **failed-SSH attempts in last hour** (attack telemetry), pending apt updates, **tailscale identity + advertised routes**, Modbus poll rate (l3-mon-01 only), and the **last-bootstrap timestamp + git commit + script** so you know exactly what version is running.

6. **Honeypot Fabric** — three Conpot personas (Siemens, Schneider, Rockwell). Liveness via TCP probes against each persona's vendor protocol stack (HTTP / S7 / Modbus / EtherNet/IP). Per-persona connection counts (last 1m / 5m / 1h, **filtered to exclude internal lab IPs**) + top external attacker IPs.

7. **Live Modbus Wire Feed** — Wireshark-lite real-time scrolling list of decoded Modbus frames captured on l3-mon-01's eth0. SSE-streamed (Server-Sent Events) — frames appear as they hit the wire. Read FCs styled blue, writes amber, exceptions red. Shows time, src/dst, FC name, address/count/value/regs.

8. **Modbus Write Playground** *(teaching artifact — there's no auth)*. Pick a target (sensor-sim @ `:5020` for persistent overrides; l1-plc-01 mirror @ `:502` for ephemeral), kind (coil/register), address, value → click "Send Modbus Write". Real Modbus FC5/FC6 issued via pymodbus. Two deliberately-different teaching outcomes:
   - **sensor-sim**: writes stick. Synoptic gets a `WRITES OVERRIDE` amber badge.
   - **l1-plc-01 mirror**: write flickers for one OpenPLC scan, then the ST program overwrites it. Demonstrates anti-tamper through control loop.

9. **Cohort Reset (booth ops)** — single button between students. Clears all sensor-sim faults + Modbus write overrides, deletes stored pcaps, restarts sensor-sim + l1-plc-01 OpenPLC. Returns step-by-step result panel.

10. **Inject Fault** — three toggles + Clear all:
    - **Pause sensor-sim** — freezes all waveforms
    - **Pause heartbeat** — freezes only HB; l1-plc-01's link-liveness watchdog trips after ~3s, demonstrating remote-endpoint detection
    - **Force HI_TEMP_ALARM** — flips the alarm coil regardless of actual temp

    Synoptic title bar gets a `FAULT INJECTED` red badge while any flag is active.

11. **Lab Credentials (booth ops)** — collapsible panel showing MFCTP WiFi, OpenPLC, and dashboard creds at a glance. All intentionally-public per project convention. Loaded from `/api/creds` (auth-gated).

12. **Pcap Captures** — buttons per Pi to fire a 60 s `tcpdump` on eth0. Captures land in `~/lab/dashboard/captures/` and surface in a list with download links. Each captured file is openable in Wireshark.

Plus header niceties:
- **◐ theme** toggle (dark ↔ light, persisted in localStorage)
- **Dynamic favicon + tab title** — green/yellow/red dot reflects worst card state, with `!!!` / `!` prefix in the tab title so booth attendants notice while the dashboard is buried behind other tabs
- **Browser notifications** on green→red transitions (one-time permission ask)

## How it works

```
        ┌──────────────────────────────────┐
        │  browser :8000 (HTTPS, basic auth) │
        └────────────────┬─────────────────┘
                         │ vanilla fetch / EventSource
                         ▼
        ┌──────────────────────────────────┐
        │  l3-mon-01 :8000                 │
        │  otlab-dashboard.service         │
        │   - Flask app (otuser)           │
        │   - probe_loop thread (2.5s)     │
        │   - wire-capture thread (tcpdump)│
        │   - cached STATE via /api/status │
        └────────────────┬─────────────────┘
                         │ ping, TCP, HTTP HEAD, Modbus, ssh, tcpdump
                         ▼
        ┌──────────────────────────────────────────────┐
        │ l1-plc-01   l3-mon-01  l1-hp-01         │
        │             (sensor-sim                      │
        │              :5020 + /control                │
        │              :5021)                          │
        │                          + Conpot .50/.51/.52│
        └──────────────────────────────────────────────┘
```

A background **probe loop** runs every `PROBE_INTERVAL` seconds (default 2.5 s) populating `STATE['cards']`, `STATE['faults']`, `STATE['writes']`. Slower cadences for heavier probes:

- **System health** (`HEALTH_INTERVAL`, 8 s): SSH out to each remote Pi, run an inline shell that gathers CPU/mem/disk/temp/uptime/load/failed-units/SSH-failures/tailscale-info/apt-pending/bootstrap-info, return as JSON.
- **Honeypot intel** (8 s): SSH to l1-hp-01, sudo-tail the per-persona Conpot logs, parse connection events into 1m/5m/1h windows + top external IPs.
- **ARP / DHCP discovery** (`NEIGHBORS_INTERVAL`, 30 s): parallel ping-sweep of `10.20.30.1..254`, then read `ip neigh show dev eth0` to harvest IP/MAC/state. Drives the topology graph's "other clients" row.
- **Modbus poll-rate gauge** (every health cycle): 1.5 s `tcpdump` count of inbound polls on l3-mon-01's eth0.

A separate **wire-capture thread** runs a long-lived `tcpdump -x` on l3-mon-01's eth0 (port 502 + 5020), decodes each frame's MBAP+PDU with stdlib `struct`, and pushes parsed frames into a bounded deque. The `/api/wire/stream` endpoint streams new frames as Server-Sent Events.

HTTP requests just read the cached snapshot — no probing on the request path. So 10 viewers don't multiply the probe load on the lab.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | The HTML page |
| GET | `/api/status` | All cached state — cards, health, honeypot, faults, writes, neighbors |
| GET | `/api/creds` | Lab credentials panel data |
| GET | `/api/neighbors` | Latest ARP discovery |
| GET | `/api/wire/recent` | Snapshot of last ~50 decoded Modbus frames |
| GET | `/api/wire/stream` | SSE stream of new Modbus frames as they're decoded |
| POST | `/api/reboot/<host>` | Issue `sudo systemctl reboot` on a Pi |
| POST | `/api/restart/<host>/<svc>` | Restart a single service (allowlisted) |
| POST | `/api/inject` | Set fault flags `{paused, hb_paused, force_alarm}` on sensor-sim |
| POST | `/api/inject/clear` | Clear all fault flags |
| POST | `/api/write` | Issue a real Modbus write (FC5/FC6) to a configured target |
| POST | `/api/write/clear` | Clear sensor-sim's persistent write-overrides |
| POST | `/api/cohort/reset` | Clear faults + writes, delete pcaps, restart sensor-sim + l1-plc-01 openplc |
| POST | `/api/capture/<host>` | Kick off a 60 s pcap capture |
| GET | `/api/captures` | List captures + status |
| GET | `/api/capture-download/<id>` | Download a completed pcap |

All endpoints require basic auth.

## Install

```bash
./scripts/install-dashboard.sh                                 # default otadmin@RASPLC02.local
./scripts/install-dashboard.sh otadmin@192.168.120.19          # mgmt IP
./scripts/install-dashboard.sh otadmin@100.77.255.56           # via tailscale
```

The script (idempotent end-to-end):

1. rsyncs `dashboard/` to `/home/otuser/lab/dashboard/` (owned by otuser)
2. installs Flask + Flask-HTTPAuth into the lab venv (pymodbus is already there from `bootstrap-pi.sh`)
3. generates a 10-yr self-signed TLS cert with SANs covering all four access paths (mgmt IP, lab IP, tailscale IP, MagicDNS) if one isn't present
4. lays down `/etc/sudoers.d/099_otuser_reboot` with narrow NOPASSWD rules for: `systemctl reboot`, `tcpdump`, `timeout`, and `systemctl restart {sensor-sim,openplc,otlab-dashboard}`
5. ensures `~/lab/dashboard/captures/` and `~/lab/dashboard/.ssh-cm/` exist (the latter for SSH ControlMaster sockets — the systemd unit's `ProtectHome=read-only` prevents writing under `~/.ssh/`)
6. generates an ed25519 keypair for `otuser` if missing and authorizes its pubkey on `otadmin@l1-plc-01` + `otadmin@l1-hp-01` (so remote reboots / restarts work)
7. installs + enables `otlab-dashboard.service`
8. stamps `/etc/otlab-bootstrap-info` with the run timestamp + git commit

## Config

`/home/otuser/lab/dashboard/dashboard.env` (template at `dashboard/dashboard.env.example`):

```ini
DASH_USER=otlab
DASH_PASS=P@ssw0rd!
PROBE_INTERVAL=2.5
PROBE_TIMEOUT=1.5
HEALTH_INTERVAL=8.0
HEALTH_TIMEOUT=12.0
NEIGHBORS_INTERVAL=30
LISTEN_PORT=8000
SSH_USER=otadmin

# Surfaced through /api/creds → "Lab Credentials" panel
WIFI_SSID=MFCTP
WIFI_PASS=P@ssw0rd!
OPENPLC_USER_NAME=openplc
OPENPLC_USER_PASS=P@ssw0rd!
```

Edit, then `sudo systemctl restart otlab-dashboard`.

## Reboot + restart mechanism

| Target | Path |
|---|---|
| `l3-mon-01` reboot (self) | `sudo -n systemctl reboot` (narrow sudoers rule) |
| `l1-plc-01` / `l1-hp-01` reboot | `ssh otadmin@<lab-ip> sudo systemctl reboot` (otadmin has full NOPASSWD sudo on the target) |
| `l3-mon-01` service restart | `sudo -n systemctl restart <svc>` (narrow sudoers — only sensor-sim/openplc/otlab-dashboard) |
| `l1-plc-01` openplc restart | `ssh otadmin@10.20.30.47 sudo systemctl restart openplc` |

Reboots are fire-and-forget (`subprocess.Popen`) so the HTTP response goes out before the box dies. Service restarts are synchronous (15 s timeout) so the user sees immediate success/failure.

## Known limits

- **Polling, not push, for cards.** A reboot or service restart shows up on the next probe cycle, not instantly. The wire feed *is* push (SSE), so writes/scans land in real time.
- **No long-term history.** Sparklines hold 5 min, persisted in memory only. If we want hours-day trend charts, add SQLite-backed retention.
- **No firewall control.** TP-Link card shows up/down only — no API to its admin UI from this dashboard. Adding a "reboot firewall" button is feasible (some TP-Links support SSH) but model-specific.
- **Reboot is "kick the box".** No graceful shutdown of OpenPLC's runtime, no Conpot container draining. Fine for a teaching lab where the goal is fast iteration.
- **Modbus writes go straight through.** No auth, by design — that's the teaching artifact. Don't expose the dashboard's `/api/write` endpoint to untrusted users; the basic-auth gate is what keeps booth visitors from pressing it accidentally.

## Adding cards / probes / actions

- **New status card**: add to `HOSTS` or `CONPOTS` in `dashboard.py`, then to `ROW_ORDER` / `HEALTH_ORDER` in `app.js`. Probe primitives `ping` / `tcp_probe` / `http_probe` / `modbus_probe` are reusable.
- **New live data on a soft-PLC card**: extend `plcExtras()` in `app.js` and the corresponding fields in `STATE['cards'][name]['modbus']` on the backend.
- **New section / panel**: add a `<section class="row">` to `index.html` with a target div, a render function in `app.js`, and styling in `style.css`. Sections so far: synoptic, topology, network, plc, health, honeypot, wire feed, write playground, cohort reset, inject fault, creds, captures.
- **New action button**: add an endpoint in `dashboard.py` (auth-gated), wire a click handler in `app.js`, sudoers entry in `install-dashboard.sh` if it needs root.

The whole frontend is vanilla browser JS — no build step. Edit, refresh.
