# Architecture Reference

Quick reference for the OTLab architecture — zones, subnets, asset inventory. For deep technical detail, see the repo docs linked below.

> **Repo source of truth**:
> - [`docs/network-architecture.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/network-architecture.md) — per-Pi fabric
> - [`docs/classroom-network.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-network.md) — classroom-wide network

## Three layers, no shared IPs

| Layer | What | Subnet (single-Pi default) | Subnet (classroom — student N) |
|---|---|---|---|
| **L3 — Operator plane** | Outside the classroom — venue WAN, tailscale, instructor laptop | (venue WAN) | (venue WAN) |
| **L2 — Classroom segment** | One subnet shared by teacher + all student Pis (mgmt only) | n/a (single Pi) | `192.168.10.0/24` (default) |
| **L1 — Lab fabric** | Inside each Pi — DMZ + PCN + ENT zones | `192.168.75/24` + `10.20.30/24` + `192.168.50/24` | `10.75.N/24` + `10.30.N/24` + `10.50.N/24` |

## Per-Pi internal zones (the OTLab fabric)

Per Purdue model:

| Zone | Purdue level | Subnet (single-Pi) | What lives there |
|---|---|---|---|
| **DMZ** | L3.5 | `192.168.75.0/24` | Dashboard `.40`, DHCP-DMZ `.2`, future Authentik/Ignition/Guacamole |
| **PCN** | L1/L2 | `10.20.30.0/24` | Firewall `.1`, DHCP-PCN `.2`, modbus-master `.43`, OpenPLC #1 `.60`, OpenPLC #2 `.61`, sensor-sim `.70`, DNP3 `.71` |
| **ENT** *(V4.1)* | L4 | `192.168.50.0/24` | Future enterprise zone (corp AD, file server, jump host) |

Conduit: `fw-dmz-pcn` container straddles both bridges, enforces L3.5↔L1/2 policy.

## Per-student fabric subnets (classroom mode)

Every student gets unique /24s in each layer so the teacher SIEM can identify which student fired an alert by source IP alone:

| Layer | Pattern | Student 1 | Student 5 | Student 20 |
|---|---|---|---|---|
| DMZ | `10.75.N.0/24` | 10.75.1.0/24 | 10.75.5.0/24 | 10.75.20.0/24 |
| PCN | `10.30.N.0/24` | 10.30.1.0/24 | 10.30.5.0/24 | 10.30.20.0/24 |
| ENT *(V4.1)* | `10.50.N.0/24` | 10.50.1.0/24 | 10.50.5.0/24 | 10.50.20.0/24 |
| Classroom IP | `192.168.10.{100+N}` | .101 | .105 | .120 |

The upstream router holds 60 static routes (3 layers × 20 students) so the teacher Pi can reach every student's fabric directly without NAT.

## Asset inventory (classroom mode)

| Asset | Qty | Network | Notes |
|---|---|---|---|
| Student Pi (Cruiser Keel + CM4) | 20 | `192.168.10.101`–`.120` mgmt | 4× GbE per Pi (1 onboard + 3 PCIe) |
| Teacher Pi (any) | 1 | `192.168.10.10` | Runs teacher panel + SIEM stack |
| Cisco Catalyst 2960 24-port | 1 | switch mgmt `192.168.10.50` (DHCP reservation) | L2 only, VLAN 10 for classroom |
| MikroTik RB5009 | 1 | `192.168.10.1` (gateway) | DHCP, routing, ACLs |
| (Future) 24-port unmanaged switch | 1 | n/a (L2) | For OT-shared VLAN 200 when `otlab-otext` activates |
| (Future) Out-of-band Suricata sensor | 1 | classroom segment | Receives mirror feed from Cisco SPAN |

## Per-Pi port assignments (Cruiser Keel = 4 ports)

| Port name | Linux default | Role | Wired today? |
|---|---|---|---|
| `otlab-mgmt` | `eth0` (onboard) | Pi mgmt / classroom segment — DHCP, SSH, teacher panel, SIEM | ✅ |
| `otlab-otext` | `eth1` (PCIe NIC #1) | OT lab extension — bridges into `pcn-br0` for physical OT gear | 🟡 wired, inactive (waiting on 2nd switch) |
| `otlab-mirror` | `eth2` (PCIe NIC #2) | SPAN destination — future out-of-band Suricata feed | ❌ reserved |
| `otlab-spare` | `eth3` (PCIe NIC #3) | Reserved | ❌ |

Names pinned by MAC via systemd `.link` files written by `scripts/configure-4port-pi.sh`.

## Trust boundaries (classroom mode)

| Source → Destination | Allowed? | Enforced by |
|---|---|---|
| Teacher → any student SSH | ✅ | Teacher's ed25519 pubkey in student's authorized_keys |
| Teacher → any student fabric (e.g. `10.30.5.43`) | ✅ | MikroTik static routes via student's classroom IP |
| Student → teacher (Loki SIEM port 3100) | ✅ | MikroTik firewall allow rule |
| Student → another student (classroom) | ❌ | MikroTik firewall deny |
| Student → another student (fabric) | ❌ | No route + MikroTik deny |
| Student → venue WAN | ✅ | NAT'd at MikroTik |

## Companion apps

| App | Listen | What |
|---|---|---|
| OTLab Dashboard (per-Pi) | `:8000` | Operator surface — Firewall, DHCP, Live Data, Teaching tabs |
| Teacher Admin Panel | teacher-host `:8080` | Classroom canvas + Pi discovery |
| Loki | teacher-host `:3100` | Log aggregation (push + query API) |
| Grafana | teacher-host `:3000` | SIEM dashboards + alerts |
| Promtail | each student `:9080` | Log shipper (Suricata, dashboard, firewall) |
| OpenPLC #1 web UI | per-Pi `:8081` | clab port-forward to plc-1-virt |
| OpenPLC #2 web UI | per-Pi `:8082` | clab port-forward to plc-2-virt |
| Cockpit *(optional)* | per-Pi `:9090` | Linux server admin |
| Portainer CE *(optional)* | per-Pi `:9443` | Docker UI |
| EdgeShark *(optional)* | per-Pi `:5001` | In-browser packet capture |

## Version history

| Version | Highlights |
|---|---|
| V1.0 | Single-Pi virtualization fabric (firewall, dual OpenPLC, sensor-sim, dashboard) |
| V2.0 | Modbus master container + Suricata IDS + macvlan for physical Pi integration |
| V3.0 | Authentik (IdP), Ignition SCADA, Guacamole, CODESYS |
| V4.0 | **Classroom rollout**: teacher panel + asymmetric SSH + interactive installer + reset script + Loki SIEM + MikroTik router config + Cisco switch config + Cruiser Keel 4-port hardware + per-student subnets |
| V4.1 *(planned)* | L4 Enterprise zone (corp AD, file server, jump host) |
| V4.2 *(planned)* | Authentik + Guacamole for instructor SSO |
| V4.3 *(planned)* | VyOS firewall (replaces fw-dmz-pcn for richer policy) |
| V4.4 *(planned)* | CODESYS Control SL + Web HMI |

## See also

- **Classroom Network Map** — the cross-Pi network design
- **Single-Pi Lab — Setup** — building one student kit
- **Hardware Kit — Cruiser Keel + Cisco** — production hardware spec
- [`docs/network-architecture.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/network-architecture.md) — full per-Pi reference with Mermaid diagram
