# Single-Pi Lab — Setup

How to build one OTLab student kit from a fresh Raspberry Pi 5.
Outcome: a self-contained ICS/OT training lab on one Pi — firewall,
DMZ, PCN, dual PLCs, sensor-sim, Suricata IDS, operator dashboard.

> **Repo source of truth**: [`docs/setup-from-scratch.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/setup-from-scratch.md)

## Hardware

| Item | Spec | Notes |
|---|---|---|
| Raspberry Pi 5 | 8 GB minimum, 16 GB recommended | 8 GB is tight (~7 GB working set with full stack) |
| Power | Official Pi 5 27 W USB-C PSU | Critical — under-powered Pi 5 throttles + behaves weirdly |
| Storage | NVMe 256 GB via PCIe HAT (recommended) OR microSD 64 GB | NVMe much faster + survives event cycling |
| Network | Wired Ethernet (recommended) OR WiFi | Wired is more reliable for events |

For classroom rollout: use the Exaviz Cruiser Keel carrier board instead — see **Hardware Kit — Cruiser Keel + Cisco** child page.

## Image the Pi

Use Pi Imager → Raspberry Pi OS Lite (Bookworm 64-bit). In Advanced Options:

- Hostname: `otlab-student-01` (or whatever for single-Pi mode)
- Username: `otadmin`
- Password: `P@ssw0rd!`
- SSH: enabled
- WiFi: configure if needed (skip if wired)

Boot, find the IP from your router's connected-devices page, then from your laptop:

```bash
ssh-copy-id otadmin@<pi-ip>
```

## Bootstrap (from your laptop)

Clone the repo:

```bash
git clone https://github.com/aaroncorvo/otlab.git
cd otlab
```

Run the bootstrap chain:

```bash
# 1. Create otadmin (NOPASSWD sudo) + otuser (no sudo), set SSH keys
./scripts/bootstrap-users.sh otadmin@<pi-ip>

# 2. apt deps + Docker + Suricata + lab venv (~10 min)
./scripts/bootstrap-pi.sh otadmin@<pi-ip>

# 3. l3-mon role (Docker for containerlab + Suricata package)
./scripts/bootstrap-l3-mon-role.sh otadmin@<pi-ip>

# 4. Deploy the virtualized fabric (~10 min — builds 7 Docker images first time)
./scripts/install-virtual-lab.sh otadmin@<pi-ip>
```

After the chain completes (~25 min total):

- Dashboard: `http://<pi-ip>:8000` (login `otlab` / `P@ssw0rd!`)
- OpenPLC #1: `http://<pi-ip>:8081` (login `openplc` / `P@ssw0rd!`)
- OpenPLC #2: `http://<pi-ip>:8082`

## What's running

| Container | Role | IP (DMZ/PCN) |
|---|---|---|
| `fw-dmz-pcn` | Firewall between zones | DMZ .1, PCN .1 |
| `dashboard` | Operator surface (Flask) | DMZ .40 |
| `dhcp-dmz` | DHCP for DMZ | DMZ .2 |
| `dhcp-pcn` | DHCP for PCN | PCN .2 |
| `plc-1-virt` | Virtual OpenPLC (master) | PCN .60 |
| `plc-2-virt` | Virtual OpenPLC (outstation) | PCN .61 |
| `modbus-master` | Polls sensor-sim every 100 ms | PCN .43 |
| `sensor-sim` | Modbus TCP slave with water-treatment scenario | PCN .70 |
| `dnp3-outstation` | DNP3 outstation on port 20000 | PCN .71 |

Default subnets: DMZ `192.168.75.0/24`, PCN `10.20.30.0/24`. Classroom mode overrides these to be per-student unique.

## Daily ops

```bash
# Check fabric
ssh otadmin@<pi-ip> 'sudo containerlab inspect -t /home/otuser/lab/virtual/topologies/otlab.clab.yaml'

# Reset everything (between lab steps)
./scripts/otlab-reset.sh --step otadmin@<pi-ip>

# Full reset (end of class — back to fresh state)
./scripts/otlab-reset.sh --full otadmin@<pi-ip>
```

## Troubleshooting

| Symptom | First check |
|---|---|
| Dashboard 502 | `sudo systemctl status otlab-dashboard.service` |
| PLCs not reachable | `sudo containerlab inspect` — verify all containers running |
| `voltage low` warnings | Better PSU (official Pi 5 27 W) |
| Suricata not firing | Check `/var/log/suricata/eve.json` has events; check rules loaded |
| Pi gets renamed on reboot | cloud-init not disabled — re-run `bootstrap-users.sh` |

## See also

- **Architecture Reference** (this Notion space) — what each zone does
- **Classroom Installer & Reset** — how this scales to 20 students
- [`docs/setup-from-scratch.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/setup-from-scratch.md) — full repo walkthrough
- [`docs/dashboard-tour.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/dashboard-tour.md) — what every dashboard tab does
