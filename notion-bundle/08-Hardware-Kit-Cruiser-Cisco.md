# Hardware Kit — Cruiser (teacher) + Cruiser Keel (students) + Cisco

What you actually build for a 20-student rollout. **Two-tier hardware**: teacher gets the premium 8-port Cruiser with ESP32 wireless; students each get the simpler 4-port Cruiser Keel.

> **Repo source of truth**: [`docs/classroom-network.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-network.md) (Hardware Kit section)

## Teacher Pi (1×, ~$430)

The "command center" — runs the teacher panel + Loki/Grafana SIEM + Cisco/MikroTik admin access.

| Item | Qty | Why |
|---|---|---|
| **Exaviz Cruiser Carrier Board** | 1 | Premium CM5 carrier — 1× 2.5GbE WAN + 8× PoE switch ports (2× RTL8365MB chips daisy-chained via DSA) + ESP32 wireless + M.2 NVMe slot, fanless. [Product page](https://www.exaviz.com/product-page/cruiser-carrier-board-v1-0) |
| Raspberry Pi **CM5** (8 GB RAM, 32 GB eMMC) | 1 | Cruiser requires CM5 — CM4 not supported on this carrier |
| NVMe SSD (256 GB) | 1 | e.g. fanxiang S500 Pro M.2 2280 PCIe 3.0 — ~10× SD capacity, ~10× faster |
| 48-57V DC PoE-capable PSU | 1 | Lets the teacher Pi supply PoE power to student Pis via `poe0`-`poe7` |
| Cat6 patch cable (1 m) | 1 | `eth1` (2.5 GbE WAN) → classroom uplink (Cisco trunk or MikroTik) |

## Per student (20×, ~$200/each)

The "learner unit" — runs the OTLab fabric (firewall + DMZ + PCN containers) + ships logs to teacher SIEM.

| Item | Qty | Why |
|---|---|---|
| **Exaviz Cruiser Keel** | 1 | Simpler CM4/CM5 carrier — 4× 1 GbE ports, NVMe slot, fanless |
| Raspberry Pi CM4 (8 GB RAM, 32 GB eMMC) | 1 | CM5 also works if budget allows |
| NVMe SSD (256 GB) | 1 | Matches teacher for consistent rollout (same migrate-to-nvme.sh script) |
| 12V 5A barrel-jack PSU | 1 | Cruiser Keel power input — NOT USB-C |
| Cat6 patch cable (1 m) | 1 | `otlab-mgmt` port → Cisco access port |
| *(future)* Cat6 patch cable (1 m) | 1 | `otlab-otext` → 2nd switch when added |

## Per classroom (instructor's kit)

| Item | Cost | Role |
|---|---|---|
| Instructor laptop | already have | Runs `git`, install scripts, tailscale for remote ops |
| **Cisco Catalyst 2960 24-port** | ~$200 used | L2-only classroom switch. 20 student-mgmt ports + teacher + MikroTik trunk |
| **MikroTik RB5009** | ~$250 | DHCP + routing + ACLs |
| Cat6 patch cables (40 total) | $80 | Primary set + spares |
| Power strips (4) | $40 | 5 Pis each |
| *(future)* 24-port unmanaged switch | $80 | For OT-shared VLAN 200 when otlab-otext ports go live |
| *(future)* Out-of-band Suricata sensor (Pi 5 + 1TB NVMe) | $200 | Receives the mirror feed |

## Teacher Pi port assignments — full Cruiser (10 network interfaces)

| Port | Linux name | Role | Wired today? |
|---|---|---|---|
| 2.5 GbE WAN | `eth1` | Upstream connection — classroom mgmt or venue WAN | ✅ yes |
| DSA master | `eth0` | Internal CPU-side link to switch chips (not user-facing) | n/a |
| PoE 1-8 | `poe0`–`poe7` | Available — can bridge into mgmt, serve direct student ports (with PoE), or stay as independent | ❌ reserved |
| ESP32 WiFi | `wlan0` | Future: classroom AP for wireless IoT teaching | ❌ reserved |

**Flexibility tip**: a ≤ 8-student rollout could collapse the Cisco switch into the teacher Pi entirely (teacher serves DHCP, routes, AND powers students via PoE). For 9-20 students the Cisco still serves the L2.

## Student Pi port assignments — Cruiser Keel (4 ports)

Names pinned by MAC via systemd `.link` files written by `scripts/configure-4port-pi.sh`. Survives reboots, PCIe enumeration changes, CM4/CM5 swaps.

| Port name | Linux default | Role | Wired today? |
|---|---|---|---|
| **otlab-mgmt** | eth0 (onboard) | Pi mgmt / classroom segment — DHCP, SSH, teacher panel, SIEM | ✅ yes |
| **otlab-otext** | eth1 (PCIe NIC #1) | OT lab extension — bridges into `pcn-br0` for physical OT gear | 🟡 wired but inactive (waiting on 2nd switch) |
| **otlab-mirror** | eth2 (PCIe NIC #2) | SPAN destination — future out-of-band Suricata feed | ❌ reserved |
| **otlab-spare** | eth3 (PCIe NIC #3) | Reserved (IPMI-style mgmt, 2nd uplink, etc.) | ❌ reserved |

## Software stack (both teacher and students run Debian 13 Trixie)

| Component | Version | Role |
|---|---|---|
| Raspberry Pi OS / Debian 13 (Trixie) | Bookworm-equivalent or newer | Base OS |
| Exaviz DKMS driver | 1.1.9 | `rtl8365mb` + `realtek_dsa` + `dsa_core` kernel modules for the PoE switch (teacher only; Cruiser Keel students don't need it) |
| Docker engine | 26.1.5+ | Container runtime for OTLab fabric + teacher panel + SIEM |
| Docker Compose v2 | 2.29.7+ | Multi-container orchestration (SIEM stack) |
| ContainerLab | 0.59.0 | Student-side lab fabric topology (clab) |
| Promtail | 2.9.4 | Per-student log shipper to teacher Loki |

## Why two-tier hardware and not "Cruisers everywhere"

| | Teacher needs | Student needs |
|---|---|---|
| Upstream port | 2.5 GbE for SIEM ingest from 20 students | 1 GbE plenty |
| Number of physical ports | Up to 10 (for OT extension, mirror, expansion) | 4 (mgmt + OT-ext + mirror + spare) |
| ESP32 wireless | Yes (for wireless IoT teaching) | No |
| PoE-out capable | Yes (powers student Pis if collapsed-switch design) | No |
| Cost premium | $230 extra ($430 vs $200) | n/a |

For a single teacher Pi the cost premium is trivial. Putting Cruisers at every student would 20× the premium with no gain — students only need mgmt + future OT-ext.

## Estimated total

| | $ |
|---|---:|
| 1× teacher Pi (Cruiser + CM5 + NVMe + PSU) | 430 |
| 20× student Pi (Cruiser Keel + CM4 + NVMe + PSU) | 4,000 |
| Cisco 2960 24-port (used) | 200 |
| MikroTik RB5009 | 250 |
| Cables + power strips | 200 |
| **Subtotal (production-ready)** | **5,080** |
| Future: 2nd 24-port unmanaged switch | +80 |
| Future: out-of-band Suricata sensor | +200 |
| **Full-build total** | **~5,360** |

For a 5-student travel kit: ~$1,500.

## What we ruled out

| Hardware | Why ruled out |
|---|---|
| Standard Pi 5 with 1 onboard NIC (any role) | Only 1 port — needs USB Ethernet dongles for multi-network, fragile |
| Cruiser (teacher-class hardware) for every student | 20× cost premium for capabilities students don't need |
| MikroTik CRS328-24P-4S+RM (PoE switch) | PoE-out is nice but Cisco 2960 + barrel-jack PSUs is cheaper and works |
| Netgear GS324T | Cheaper but no L3, no PoE, no real upside over the Cisco |
| FortiGate as classroom router | More expensive; MikroTik handles the same load fine; we use FortiGate only for the optional teacher-panel port-monitor |

## See also

- **Classroom Network Map** — full network map
- **Classroom Installer & Reset** — install walkthrough
- `scripts/configure-4port-pi.sh` (repo) — Cruiser Keel student-side NIC role pinning
- `scripts/migrate-to-nvme.sh` (repo) — SD-to-NVMe clone for either hardware tier
- `reference/router-configs/cisco/24-port-classroom.ios` (repo) — Cisco paste config
- `reference/router-configs/mikrotik/20-student-classroom.rsc` (repo) — MikroTik paste config
