# Hardware Kit — Cruiser Keel + Cisco

What you actually build for a 20-student rollout. Real production hardware.

> **Repo source of truth**: [`docs/classroom-network.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-network.md) (Hardware Kit section)

## Per student (~$300/each)

| Item | Qty | Why |
|---|---|---|
| Exaviz Cruiser Carrier Board v1.0 | 1 | 4× GbE per Pi (1 onboard + 3 PCIe NICs), NVMe slot, fanless. [Product page](https://www.exaviz.com/product-page/cruiser-carrier-board-v1-0) |
| Raspberry Pi CM4 (8 GB RAM, 32 GB eMMC) | 1 | Slots into the Cruiser. WiFi version optional — most labs use wired only |
| NVMe SSD (256 GB) | 1 | Fits the Cruiser's M.2 slot |
| 12V 5A barrel-jack PSU | 1 | Cruiser's power input — NOT USB-C |
| Cat6 patch cable (1 m) | 1 | otlab-mgmt port → Cisco |
| *(future)* Cat6 patch cable (1 m) | 1 | otlab-otext → 2nd switch when added |

## Per classroom (instructor's kit)

| Item | Cost | Role |
|---|---|---|
| Instructor host (laptop or Pi) | already have | Runs teacher panel + Loki/Grafana SIEM |
| **Cisco Catalyst 2960 24-port** | ~$200 used | L2-only classroom switch. 20 student-mgmt ports + teacher + MikroTik trunk |
| **MikroTik RB5009** | ~$250 | DHCP + routing + ACLs |
| Cat6 patch cables (40 total) | $80 | Primary set + spares |
| Power strips (4) | $40 | 5 Pis each |
| *(future)* 24-port unmanaged switch | $80 | For OT-shared VLAN 200 when otlab-otext goes live |
| *(future)* Out-of-band Suricata sensor (Pi 5 + 1TB NVMe) | $200 | Receives the mirror feed |

## Per-Pi port assignments (Cruiser Keel = 4 ports)

Names pinned by MAC via systemd `.link` files written by `scripts/configure-4port-pi.sh`. Survives reboots, PCIe enumeration changes, CM4 swaps.

| Port name | Linux default | Role | Wired today? |
|---|---|---|---|
| **otlab-mgmt** | eth0 (onboard) | Pi mgmt / classroom segment — DHCP, SSH, teacher panel, SIEM | ✅ yes |
| **otlab-otext** | eth1 (PCIe NIC #1) | OT lab extension — bridges into `pcn-br0` for physical OT gear | 🟡 wired but inactive (waiting on 2nd switch) |
| **otlab-mirror** | eth2 (PCIe NIC #2) | SPAN destination — future out-of-band Suricata feed | ❌ reserved |
| **otlab-spare** | eth3 (PCIe NIC #3) | Reserved (IPMI-style mgmt, 2nd uplink, etc.) | ❌ reserved |

## Why 4 ports without VLAN trunking

Considered 802.1Q VLAN trunking (one port per Pi, multiple VLANs on the same wire). Picked dedicated physical ports instead:

| Approach | Pros | Cons |
|---|---|---|
| VLAN trunk on 1 port | Single switch cable per Pi; classic OT design pattern | Requires managed switch trunk config per port; harder to debug for non-network-engineers; needs router sub-interfaces for every VLAN |
| **Dedicated physical ports (chosen)** | Each port = one thing; debugging is trivial (`ip link show`); physical mirror port available; unmanaged 2nd switch is fine for OT VLAN 200 | More cables; needs hardware with multiple NICs (which we have — Cruiser Keel) |

## Port math (why one Cisco 24-port is enough for now)

- 20 student `otlab-mgmt` ports on VLAN 10 (access)
- 1 teacher `otlab-mgmt` port on VLAN 10 (access)
- 1 trunk uplink to MikroTik (carries VLAN 10)
- 2 spare ports for laptops / demo gear
- **Total: 24 ports → fits exactly**

For `otlab-otext` (VLAN 200, 20 student PCN extensions): needs its own 24-port switch. Any unmanaged Gigabit switch works since it's single-VLAN. Deferred until physical OT gear is in the mix.

## Estimated total

| | $ |
|---|---:|
| 20× student kit ($300 each) | 6,000 |
| Cisco 2960 24-port (used) | 200 |
| MikroTik RB5009 | 250 |
| Cables + power strips | 200 |
| **Subtotal (production-ready)** | **6,650** |
| Future: 2nd 24-port unmanaged switch | +80 |
| Future: out-of-band Suricata sensor | +200 |
| **Full-build total** | **~6,930** |

For a 5-student travel kit: ~$1,700.

## What we ruled out

| Hardware | Why ruled out |
|---|---|
| Standard Pi 5 with 1 onboard NIC | Only 1 port — needs USB Ethernet dongles for multi-network, fragile |
| MikroTik CRS328-24P-4S+RM (PoE switch) | Considered for PoE-out (eliminates 20 power bricks) but we already have the Cisco |
| Netgear GS324T | Cheaper but no L3, no PoE, no real upside over the Cisco |
| FortiGate as classroom router | More expensive; MikroTik handles the same load fine; we use FortiGate for the optional teacher-panel integration but not as the primary router |

## See also

- **Classroom Network Map** — full network map
- **Classroom Installer & Reset** — install walkthrough
- `scripts/configure-4port-pi.sh` (repo) — NIC role pinning
- `reference/router-configs/cisco/24-port-classroom.ios` (repo) — Cisco paste config
- `reference/router-configs/mikrotik/20-student-classroom.rsc` (repo) — MikroTik paste config
