# OTLab — Maple Ridge ICS Training Lab

Hands-on industrial control systems training lab for [ICS Village](https://icsvillage.com/) (DEF CON village). A small municipal-water-treatment scenario implemented as **virtual + physical**: one Raspberry Pi 5 runs the entire DMZ + Process Control fabric as containers (ContainerLab), two physical Pis extend it onto real wire for on-the-wire authenticity, and a 7-tab operator dashboard surfaces everything — live process state, IDS alerts, firewall policy, DHCP leases, and curriculum exercises.

> **Status (V2.y.5+):** Virtual fabric shipped end-to-end — 9 containers running on `l3-mon-01`, full DHCP/DNS/firewall infrastructure, Suricata IDS, modbus-master polling sensor-sim at 10 Hz, dashboard surfacing everything across 7 tabs. Physical Pis (`l1-plc-01`, `l1-hp-01`) integrate via USB-NIC bridged onto `pcn-br0` (opt-in via `/etc/otlab/bridge-attach.conf`). Three Conpot vendor personas (Siemens / Schneider / Rockwell) live on `l1-hp-01`. **V2.z next**: Authentik IdP + Ignition SCADA + Apache Guacamole on the DMZ. **V3**: CODESYS Control SL + Web HMI. Curriculum + lessons + CTF exercises start once the lab is verified end-to-end.

## Architecture in one paragraph

Three Pis, dual-mode. **`l3-mon-01`** (Pi 5 16GB + NVMe) virtualizes the core: 9 containers across two zone bridges (`dmz-br0` 192.168.75.0/24, `pcn-br0` 10.20.30.0/24), with a containerized firewall enforcing the L3.5↔L1/L2 conduit, dnsmasq DHCP servers per zone, dnsmasq DNS forwarder on the firewall, modbus-master polling sensor-sim, plus the OTLab Dashboard. **`l1-plc-01`** (Pi 5 + Phase 2 hardware) runs real OpenPLC with real GPIO/relays/buttons; joins the virtual fabric via macvlan when eth1 is bridged in. **`l1-hp-01`** (Pi 3 B+) runs the three Conpot vendor personas. Suricata IDS sniffs `pcn-br0` (sees ALL traffic — virtual, physical, cross-segment). Tailscale subnet routing on `l3-mon-01` advertises both subnets so operators can reach the lab from anywhere.

```
┌────── operator (browser / ssh / tailscale) ──────┐
│                                                    │
│   https://l3-mon-01:8000/   OTLab Dashboard         │
│   https://l3-mon-01:9090/   Cockpit (Linux admin)   │
│   https://l3-mon-01:9443/   Portainer (Docker)      │
│   http://l3-mon-01:5001/    EdgeShark (pcap)        │
│                                                    │
└──────────────────────┬─────────────────────────────┘
                       ▼
        ┌──────────────────────────┐         ┌─────────────┐  ┌─────────────┐
        │   l3-mon-01  (Pi 5 16GB) │         │ l1-plc-01    │  │ l1-hp-01    │
        │   ─────────────────────   │  bridge │ Pi 5         │  │ Pi 3 B+     │
        │   ContainerLab fabric:    │◄───────►│ OpenPLC :502 │  │ Conpot fabric│
        │     dmz-br0 (.75/24)      │  via    │ :8080 web UI │  │  .50 siemens│
        │     pcn-br0 (.30/24)      │  USB-NIC│ Phase 2 hw   │  │  .51 schneider│
        │   firewall + dhcp + dns   │  +      │              │  │  .52 rockwell│
        │   modbus-master + plc-1/2 │  switch │              │  │              │
        │   sensor-sim + dnp3       │         │              │  │              │
        │   Suricata IDS            │         │              │  │              │
        │   tailscale subnet router │         │              │  │              │
        └──────────────────────────┘         └─────────────┘  └─────────────┘
```

## Quick links

| | |
|---|---|
| **[Setup from scratch](docs/setup-from-scratch.md)** | Linear 10-step playbook for a fresh-Pi build |
| **[Dashboard tour](docs/dashboard-tour.md)** | What each of the 7 dashboard tabs does |
| **[Network topology](docs/network-topology.md)** | Physical NIC ↔ virtual fabric mapping |
| **[Naming schema](docs/naming-schema.md)** | Hostnames, IPs, MAC reservations |
| **[Virtualization](docs/virtualization.md)** | ContainerLab topology + V1/V2/V3 phasing |
| **[Lab architecture](docs/lab-architecture.md)** | Full build doc — hosts, network, personas, ops |
| **[Architecture evolution](docs/architecture-evolution.md)** | Phase plan + decision log |
| **[Curriculum](docs/curriculum.md)** | Lessons + Attack/Detect/Defend exercises |
| **[Phase 1 Modbus loop](docs/phase-1-modbus-loop.md)** | First lesson walkthrough |

## Hosts

| Host | Hardware | Role |
|---|---|---|
| `l3-mon-01` | Pi 5 16GB + NVMe + USB Ethernet | Virtualization host. Runs the entire DMZ + PCN fabric as containers + Suricata IDS + admin UIs. Tailscale subnet router. |
| `l1-plc-01` | Pi 5 + Freenove HAT + Phase 2 hw | Physical OpenPLC. Real Modbus on the wire, real GPIO/relays/buttons. `10.20.30.47/24`. |
| `l1-hp-01` | Pi 3 B+ | Physical Conpot fabric. 3 vendor personas as macvlan children at `.50/.51/.52`. `10.20.30.48/24`. |

Naming schema: `<purdue-level>-<role>-<NN>` ([details](docs/naming-schema.md)). Legacy hostnames (`softplc-1`, `softplc-2`, `honeypot-host`, `RASPLC01`/`RASPLC02`) preserved as `/etc/hosts` aliases for one transition cycle.

## What's in the repo

```
.
├── README.md                          ← you are here
├── docs/                               # Architecture + setup + curriculum
│   ├── setup-from-scratch.md          ← linear from-zero playbook
│   ├── dashboard-tour.md              ← 7-tab dashboard walkthrough
│   ├── lab-architecture.md            ← deep-dive build doc
│   ├── virtualization.md              ← ContainerLab fabric + V1/V2/V3 phasing
│   ├── naming-schema.md               ← canonical names, IPs, MAC reservations
│   ├── network-topology.md            ← physical NIC ↔ virtual fabric
│   ├── architecture-evolution.md      ← phase plan + decision log
│   ├── curriculum.md                  ← lessons, MITRE ATT&CK coverage
│   ├── phase-1-modbus-loop.md         ← first lesson walkthrough
│   └── arduino-setup.md               ← Arduino UNO breakout boards
├── virtual/                            # ContainerLab fabric
│   ├── topologies/otlab.clab.yaml     # full topology (9 nodes + 2 bridges)
│   └── dockerfiles/                   # 7 OTLab images
│       ├── sensor-sim/                # Modbus outstation (water-treatment scenarios)
│       ├── dnp3-outstation/           # DNP3 outstation
│       ├── modbus-master/             # Master polling sensor-sim @10Hz
│       ├── firewall/                  # iptables + dnsmasq DNS + state exporter
│       ├── dhcp/                      # dnsmasq DHCP (one image, two containers)
│       ├── dashboard/                 # Flask + JS operator surface
│       └── openplc/                   # OpenPLC v3 source-build
├── dashboard/                          # Dashboard source (mounted into the container)
├── plc/                                # PLC programs + Python services + scenarios
├── honeypot/                           # Conpot persona configs (deployed on l1-hp-01)
├── scripts/                            # Bootstrap + install scripts
│   ├── bootstrap-users.sh             # creates otadmin + otuser on a fresh Pi
│   ├── bootstrap-pi.sh                # generic Pi-OS provisioning
│   ├── bootstrap-l3-mon-role.sh       # Docker, lab venv, deps for l3-mon-01
│   ├── bootstrap-l1-plc-role.sh       # OpenPLC + Phase 2 hardware on l1-plc-01
│   ├── bootstrap-l1-hp-role.sh        # Conpot fabric on l1-hp-01
│   ├── install-virtual-lab.sh         # the big one — containerlab + 7 image builds + deploy
│   ├── install-cockpit.sh             # Cockpit (Linux admin UI) on l3-mon-01
│   ├── install-portainer.sh           # Portainer CE (Docker UI)
│   ├── install-edgeshark.sh           # EdgeShark (live packet capture in browser)
│   └── install-suricata.sh            # Suricata IDS (host-mode sniffing pcn-br0)
└── reference/                          # Diagrams, BOMs, vendor OIDs, pcaps
```

## Deploying the lab from scratch

See [`docs/setup-from-scratch.md`](docs/setup-from-scratch.md) for the full 10-step playbook. The condensed version:

```bash
# === one-time per Pi: create otadmin / otuser, ssh keys, posture ===
./scripts/bootstrap-users.sh <imager-user>@l3-mon-01.local
./scripts/bootstrap-users.sh <imager-user>@l1-plc-01.local
./scripts/bootstrap-users.sh <imager-user>@l1-hp-01.local

# === l3-mon-01: the virtualization host ===
./scripts/bootstrap-pi.sh           otadmin@l3-mon-01.local
./scripts/bootstrap-l3-mon-role.sh  otadmin@l3-mon-01.local
./scripts/install-virtual-lab.sh    otadmin@l3-mon-01.local   # ~30 min first run
./scripts/install-cockpit.sh        otadmin@l3-mon-01.local
./scripts/install-portainer.sh      otadmin@l3-mon-01.local
./scripts/install-edgeshark.sh      otadmin@l3-mon-01.local
./scripts/install-suricata.sh       otadmin@l3-mon-01.local

# === l1-plc-01: physical OpenPLC + Phase 2 hardware ===
./scripts/bootstrap-pi.sh                                  otadmin@l1-plc-01.local
OPENPLC_PASSWORD='P@ssw0rd!' \
  ./scripts/bootstrap-l1-plc-role.sh                       otadmin@l1-plc-01.local  l1-plc-01

# === l1-hp-01: physical Conpot fabric ===
./scripts/bootstrap-l1-hp-role.sh                          otadmin@l1-hp-01.local
```

Total time per fresh provision: ~30 min for `l3-mon-01` (image builds), ~20 min for `l1-plc-01` (matiec compile), ~5 min for `l1-hp-01`. All scripts are idempotent — safe to re-run.

After deploy, browse to:

| URL | What | Login |
|---|---|---|
| `https://l3-mon-01:8000/` | OTLab Dashboard (the main thing) | `otlab` / `P@ssw0rd!` |
| `https://l3-mon-01:9090/` | Cockpit (Linux admin) | `otadmin` / your sudo password |
| `https://l3-mon-01:9443/` | Portainer CE (Docker UI) | (set on first visit) |
| `http://l3-mon-01:5001/`  | EdgeShark (live pcap in browser) | none |
| `http://l3-mon-01:8081/`  | Virtual OpenPLC #1 web UI | `openplc` / `P@ssw0rd!` |
| `http://l3-mon-01:8082/`  | Virtual OpenPLC #2 web UI | same |
| `http://l1-plc-01:8080/`  | Physical OpenPLC web UI | same |

> Lab convention: intentionally-public passwords for booth use. **Rotate per DEF CON event** so creds don't leak between cohorts.

## What the dashboard shows

Seven tabs — see [`docs/dashboard-tour.md`](docs/dashboard-tour.md) for the full walkthrough.

| Tab | What |
|---|---|
| Overview | Live process state (synoptic SVG) + cards for every host/container with role-specific telemetry |
| Architecture | Purdue model with the lab's actual assets placed at their levels + auto-discovered topology |
| **IDS** | Suricata stats — counts (5m/1h/24h), 24h timeline, top signatures, top sources, top targets, recent alerts |
| **Firewall** | Live iptables (5 chains) with packet counters · conntrack snapshot · DNS query stats + log |
| **DHCP** | Per-zone (DMZ + PCN) lease tables + static reservations + recent transactions |
| Live Data | System health, live Modbus wire feed, audit log, pcap captures |
| Teaching | Risks, walkthroughs, Test Library (runnable scripts), Modbus Write Playground, Inject Fault, Cohort Reset |

## Operating the lab

### Day-to-day

```sh
# Inspect topology state
ssh otadmin@l3-mon-01.local 'sudo containerlab inspect -t /home/otuser/lab/virtual/topologies/otlab.clab.yaml --format table'

# Tail a container's log
ssh otadmin@l3-mon-01.local 'sudo docker logs -f clab-otlab-modbus-master'

# Live firewall counters
ssh otadmin@l3-mon-01.local 'sudo docker exec clab-otlab-fw-dmz-pcn iptables -nvL FORWARD'

# Recent IDS alerts
ssh otadmin@l3-mon-01.local 'sudo grep \"event_type\":\"alert\" /var/log/suricata/eve.json | tail -10'

# Reset between cohorts (browser): Teaching tab → Reset Lab for Next Cohort
```

### Disaster recovery

```sh
# Nuke topology + start fresh (preserves images)
ssh otadmin@l3-mon-01.local 'sudo bash -c "
  cd /home/otuser/lab/virtual
  containerlab destroy -t topologies/otlab.clab.yaml --cleanup
  containerlab deploy  -t topologies/otlab.clab.yaml"'

# Full rebuild from repo
./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
```

## V2.x → V2.y → V2.z roadmap

| Version | What | Status |
|---|---|---|
| V0 | Pre-virt: services on physical Pis, OpenPLC as the master | superseded |
| V1 | ContainerLab MVP: firewall + virtual OpenPLC + sensor-sim + DNP3 | shipped |
| V2.x | + modbus-master container + Suricata IDS + physical Pi macvlan | shipped |
| V2.y | + DHCP servers + DNS forwarder + DMZ extends to physical wire | shipped |
| V2.y.5 | + IDS / Firewall / DHCP dashboard tabs + DHCP reservations | shipped |
| V2.z | + Authentik IdP + Ignition SCADA + Apache Guacamole | next |
| V3 | + CODESYS Control SL + CODESYS Web HMI | planned |
| V4 | Curriculum + CTF + take-home topologies | planned |

Detailed phase plan: [`docs/architecture-evolution.md`](docs/architecture-evolution.md). Decision log: same doc, bottom.

## License

[MIT](LICENSE). Documentation and code free to fork, adapt, and use in your own training environments.

## Contributing

This is a personal / ICS Village lab build. If you've found something useful and want to suggest improvements or share what you've built on top, open an issue or PR.
