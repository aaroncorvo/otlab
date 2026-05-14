# OTLab — Network, Assets & Architecture

Team-review document. Sole purpose: a single page that captures the
network plan, the asset inventory, and the architecture so docs can be
brought into alignment.

> **Audience**: anyone updating OTLab documentation, slides, or training
> materials. Not a build doc — see [`setup-from-scratch.md`](setup-from-scratch.md)
> for that.

---

## 1. Network zones

Three Purdue zones live on a single Raspberry Pi host (`l3-mon-01`).
Each zone is a Linux bridge inside the Pi's network namespace, with a
containerized firewall as the conduit between adjacent zones.

| Purdue level | Name | Subnet | Bridge | Status |
|---|---|---|---|---|
| **L4 Enterprise — Untrusted** | Enterprise | `192.168.50.0/24` | `ent-br0` | **planned (V4.1)** |
| **L3.5 Industrial DMZ** | Operations | `192.168.75.0/24` | `dmz-br0` | shipped |
| **L1/L2 Process Control** | Plant floor | `10.20.30.0/24` | `pcn-br0` | shipped |

**Out-of-band networks** (not enforced by lab firewall):

| Purpose | Subnet | Notes |
|---|---|---|
| Operator management | `192.168.120.0/24` | host wlan0; SSH + admin access; varies per operator wifi |
| Tailscale tailnet | `100.64.0.0/10` | `l3-mon-01` advertises both lab subnets to the tailnet |
| ContainerLab mgmt | `172.20.20.0/24` | internal clab control plane, not user-visible |

**DHCP scopes & gateways** (per zone):

| Zone | Gateway | DHCP server | Scope | DNS forwarder |
|---|---|---|---|---|
| Enterprise | `192.168.50.1` (firewall) | `192.168.50.2` (`dhcp-ent`) | `.100`–`.199` | `192.168.50.1` |
| DMZ | `192.168.75.1` (firewall) | `192.168.75.2` (`dhcp-dmz`) | `.150`–`.199` | `192.168.75.1` |
| PCN | `10.20.30.1` (firewall) | `10.20.30.2` (`dhcp-pcn`) | `.200`–`.250` | `10.20.30.1` |

The firewall container runs `dnsmasq` as a DNS forwarder bound to `.1` on every zone. All internal name resolution lands at the firewall, where every query is logged (DNS-exfil teaching artifact).

---

## 2. Asset inventory

### L4 Enterprise — `192.168.50.0/24` *(planned)*

| IP | Asset | Role | Type |
|---|---|---|---|
| `.1` | `fw-ent-dmz` | Firewall conduit (ENT ↔ DMZ) | container |
| `.2` | `dhcp-ent` | DHCP + DNS forwarder | container |
| `.10` | `corp-ad` | Faux Active Directory / LDAP | container *(planned)* |
| `.20` | `corp-file` | Faux SMB file share | container *(planned)* |
| `.40` | `operator-ws` | Engineering laptop persona | container *(planned)* |
| `.100`–`.199` | dynamic | DHCP pool for ad-hoc enterprise devices | — |

### L3.5 DMZ — `192.168.75.0/24`

| IP | Asset | Role | Type | Status |
|---|---|---|---|---|
| `.1` | `fw-dmz-pcn` | Firewall conduit (DMZ ↔ PCN) + DNS forwarder | container | shipped |
| `.2` | `dhcp-dmz` | DHCP server | container | shipped |
| `.10` | `authentik-server` | IdP / SSO | container *(planned V4.2)* | planned |
| `.11` | `authentik-postgres` | Authentik database | container *(planned V4.2)* | planned |
| `.12` | `authentik-redis` | Authentik cache | container *(planned V4.2)* | planned |
| `.20` | `ignition-scada` | Ignition SCADA Gateway (Maker ed.) | container *(future)* | future |
| `.30` | `guacamole` | Clientless RDP/SSH/VNC jump server | container *(planned V4.2)* | planned |
| `.40` | `dashboard` | OTLab Dashboard (Flask + JS) | container | shipped |
| `.150`–`.199` | dynamic | DHCP pool for operator devices | — | shipped |

### L1/L2 PCN — `10.20.30.0/24`

| IP | Asset | Role | Type | Status |
|---|---|---|---|---|
| `.1` | `fw-dmz-pcn` | Firewall (PCN side) | container | shipped |
| `.2` | `dhcp-pcn` | DHCP server | container | shipped |
| `.43` | `modbus-master` | Master polling sensor-sim @10 Hz | container | shipped |
| `.47` | `l1-plc-01` | Physical Pi w/ OpenPLC + Phase 2 hw | physical *(optional)* | shipped, opt-in |
| `.48` | `l1-hp-01` | Physical Pi w/ Conpot host | physical *(optional)* | shipped, opt-in |
| `.50` | `conpot-siemens` | Honeypot persona — Siemens S7-200 (PS4-CPU01) | container | **V4.0** (was physical) |
| `.51` | `conpot-schneider` | Honeypot persona — Schneider M340 (HVAC-M340) | container | **V4.0** (was physical) |
| `.52` | `conpot-rockwell` | Honeypot persona — Allen-Bradley CompactLogix (CHEM-LGX01) | container | **V4.0** (was physical) |
| `.55` | `waveshare-gw` | RS485-to-Ethernet Modbus gateway | physical *(optional)* | future |
| `.60` | `plc-1-virt` | Virtual OpenPLC #1 | container | shipped |
| `.61` | `plc-2-virt` | Virtual OpenPLC #2 | container | shipped |
| `.70` | `sensor-sim` | Virtual Modbus TCP outstation (water-treatment scenario) | container | shipped |
| `.71` | `dnp3-outstation` | Virtual DNP3 outstation | container | shipped |
| `.80` | `codesys-plc` | CODESYS Control SL vendor PLC runtime | container *(planned V4.4)* | planned |
| `.81` | `codesys-hmi` | CODESYS Web HMI | container *(planned V4.4)* | planned |
| `.200`–`.250` | dynamic | DHCP pool for ad-hoc PCN devices | — | shipped |

### Host services (run on `l3-mon-01` directly, outside ContainerLab)

| Service | URL | Role | Status |
|---|---|---|---|
| Cockpit | `https://l3-mon-01:9090/` | Linux server admin | shipped |
| Portainer CE | `https://l3-mon-01:9443/` | Docker UI | shipped |
| EdgeShark | `http://l3-mon-01:5001/` | Live packet capture in browser | shipped |
| Suricata IDS | `/var/log/suricata/eve.json` | Network IDS sniffing `pcn-br0` (and `ent-br0` post V4.1) | shipped |
| Tailscale | tailnet route advertiser | Operator overlay reach | shipped |

---

## 3. Architecture diagram (current-IP scheme)

```
                                Internet / Operator wifi
                                          │
                                          ▼
            ╔══════════════════════════════════════════════════════════╗
            ║          l3-mon-01  (Pi 5 16GB / future: CM5 carrier)    ║
            ║          ContainerLab fabric + host services             ║
            ║                                                           ║
   eth0 ───▶║   ┌── L4 Enterprise · ent-br0 · 192.168.50.0/24 ──┐     ║
            ║   │   .1  firewall (fw-ent-dmz)                    │     ║
            ║   │   .2  dhcp-ent                                  │     ║
            ║   │   .10 corp-ad        .20 corp-file               │     ║
            ║   │   .40 operator-ws                                │     ║
            ║   └────────────────┬─────────────────────────────────┘     ║
            ║                    │ firewall conduit                       ║
            ║   ┌── L3.5 DMZ · dmz-br0 · 192.168.75.0/24 ─────┐         ║
   eth1 ───▶║   │   .1  firewall (fw-dmz-pcn)                  │         ║
            ║   │   .2  dhcp-dmz                               │         ║
            ║   │   .10 authentik    .30 guacamole              │         ║
            ║   │   .40 dashboard                                │         ║
            ║   └────────────────┬───────────────────────────────┘         ║
            ║                    │ firewall conduit                       ║
            ║   ┌── L1/L2 PCN · pcn-br0 · 10.20.30.0/24 ─────┐           ║
   eth2 ───▶║   │   .1  firewall      .2  dhcp-pcn            │           ║
            ║   │   .43 modbus-master                          │           ║
            ║   │   .50 conpot-siemens   .51 -schneider .52 -rockwell │   ║
            ║   │   .60 plc-1-virt    .61 plc-2-virt           │           ║
            ║   │   .70 sensor-sim    .71 dnp3-outstation      │           ║
            ║   │   .80 codesys-plc   .81 codesys-hmi          │           ║
            ║   └──────────────────────────────────────────────┘           ║
            ║                                                              ║
            ║   Host services (outside ContainerLab):                      ║
            ║     ▸ Suricata IDS — sniffs ent-br0 + pcn-br0                ║
            ║     ▸ Cockpit (:9090)  Portainer (:9443)  EdgeShark (:5001)  ║
            ║     ▸ Tailscale — advertises 50.0/24 + 75.0/24 + 30.0/24     ║
            ║                                                              ║
            ║   wlan0 → operator wifi / internet uplink                    ║
            ╚══════════════════════════════════════════════════════════════╝

      Optional physical expansion (when eth1/eth2 USB-NIC are plugged):
            l1-plc-01 (.47) — physical Pi with OpenPLC + Phase 2 hardware
            l1-hp-01  (.48) — physical Pi running Conpot (alt. to virtual)
            waveshare-gw (.55) — RS485-to-Modbus-TCP gateway for real RTU
```

### Traffic flow rules (firewall policy)

| Source zone | Destination zone | Policy | Notes |
|---|---|---|---|
| Enterprise → DMZ | partial allow | `.50.40` (operator-ws) → `.75.30` (guacamole) only | Jump-server pattern |
| Enterprise → PCN | **deny** | — | Enterprise never touches PCN directly |
| DMZ → Enterprise | response only | ESTABLISHED, RELATED | |
| DMZ → PCN | port-allowlist | `22, 80, 502, 5020, 8080, 20000` (SSH, HTTP, Modbus, sensor-sim, OpenPLC UI, DNP3) | SNAT'd to `.30.1` |
| PCN → DMZ | response only | ESTABLISHED, RELATED | PLCs never initiate to DMZ |
| PCN → Enterprise | **deny** | — | |
| Any → WAN | NAT via wlan0 | MASQUERADE | Internet egress for apt + image pulls |
| WAN → any | **deny** | except ESTABLISHED | |

All DNS queries from any internal zone resolve through the firewall's `dnsmasq` forwarder. The firewall logs every query.

---

## 4. Network adjustments — what's changing

This is the delta from the current shipped lab to the target architecture:

| Change | What | Why |
|---|---|---|
| **+ New L4 Enterprise zone** | `ent-br0` on `192.168.50.0/24`, 3rd firewall interface | Completes the Purdue model. DMZ pattern needs an "outside" to be meaningful. |
| **+ Conpot personas as containers** | `.50/.51/.52` move from physical Pi → virtual containers (image: `honeynet/conpot`) | Single-Pi users get the honeypot fabric without buying a second Pi. Physical Pi expansion still supported. |
| **+ Authentik IdP** | 4 new DMZ containers at `.10/.11/.12` (+ worker) | Federated SSO across DMZ services |
| **+ Guacamole jump server** | 1 new DMZ container at `.30` | Browser-based RDP/SSH/VNC to PCN — the canonical "operators don't SSH directly to PLCs" pattern |
| **+ CODESYS Control SL** | New PCN containers at `.80/.81` | Vendor PLC runtime alongside the open-source OpenPLC |
| **~ Firewall topology** | Currently 1 container with 2 interfaces. Future: 1 container with 3 interfaces (V4.0–V4.2), optionally 2 VyOS containers (V4.3) | New ENT zone needs a 3rd firewall iface |
| **~ Suricata coverage** | Adds `ent-br0` to the sniff list | IDS watches the ENT↔DMZ boundary too |
| **~ Dashboard topology view** | Renders 3 zones instead of 2 | Reflects the new architecture |
| **= Subnets** | DMZ and PCN subnets stay at `.75/24` and `.30/24` | **No re-IP** of existing assets |
| **= Address plan** | All current container IPs unchanged | No reservation rewrite, no doc rewrite |
| **= Hardware** | Pi 5 + USB Ethernet still works; CM5 carrier with 3 onboard NICs is the future path | Software is hardware-agnostic; multi-NIC config already supported |

---

## 5. What docs need to be updated

If you're picking up this doc to do a documentation pass, here's the list:

| Doc | Update needed |
|---|---|
| [`README.md`](../README.md) | Add L4 Enterprise to the ASCII diagram once V4.1 ships |
| [`docs/setup-from-scratch.md`](setup-from-scratch.md) | Add a Stage for L4 Enterprise; mention virtual Conpot is default once V4.0 ships |
| [`docs/virtualization.md`](virtualization.md) | Update phasing table with V4.0–V4.4 status as each chunk lands |
| [`docs/naming-schema.md`](naming-schema.md) | Add ENT zone section, add Authentik/Guacamole/CODESYS rows |
| [`docs/network-topology.md`](network-topology.md) | Update for 3-NIC layout (eth0=ENT, eth1=DMZ, eth2=PCN) — currently 2-NIC |
| [`docs/lab-architecture.md`](lab-architecture.md) | Major refresh — extend Purdue model section to L4 |
| [`docs/dashboard-tour.md`](dashboard-tour.md) | New ENT row on Overview tab; Authentik tab if it gets one |
| [`docs/v4-roadmap.md`](v4-roadmap.md) | Status field per chunk as they land (V4.0 shipped, V4.1 in progress, etc.) |

This doc itself ([`docs/network-architecture.md`](network-architecture.md))
should be the **single source of truth** for the architecture picture
during the V4 transition. All other docs cite back to it; when this
doc changes, the others get updated to match.

---

## 6. Questions / decisions still open

For team to discuss before implementation kicks off:

1. **Cockpit on `:443` (diagram) vs `:9090` (current)?** Pros and cons in [`v4-roadmap.md`](v4-roadmap.md) decisions section. Leaning :9090.
2. **One firewall with 3 interfaces, or two firewalls (ENT↔DMZ + DMZ↔PCN)?** Diagram shows two. Simpler to ship one. Decision drives V4.1 topology shape.
3. **VyOS migration — V4.3 timeline?** iptables works today. VyOS is more authentic but riskier swap. Defer until V4.0–V4.2 are stable.
4. **CODESYS — go / no-go?** ARM64 image isn't trivial. May punt to V5 or use OpenPLC as a stand-in.
5. **Faux corp-AD / corp-file / operator-ws in L4 — how realistic?** FreeIPA + Samba give you real protocols. Or stubs that just respond on the right ports for visibility. Trade-off: realism vs memory footprint.

---

*Doc maintained by the OTLab core team. Last revised: this commit.
File a PR or issue if you spot a discrepancy with the running lab.*
