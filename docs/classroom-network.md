# OTLab — Full Classroom Network Map

How the lab actually sits on the wire when you run it as a classroom
(N students, 1 instructor, optional FortiGate). Three layers. This
doc maps them all so you can support, debug, and explain it.

> **Sibling doc**: [`network-architecture.md`](network-architecture.md)
> covers what's inside one student's Pi (the OTLab fabric). This doc
> covers everything *between* the Pis.

---

## TL;DR — three layers

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                       │
│   L3 OPERATOR PLANE     (instructor laptop, tailscale, internet)      │
│                                                                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────────┐
│                                                                       │
│   L2 CLASSROOM SEGMENT     (one subnet shared by teacher + students)  │
│   Example: 192.168.10.0/24                                            │
│                                                                       │
│      .1   gateway / FortiGate / venue router (whatever you use)       │
│      .10  instructor laptop / teacher panel host (DHCP reservation)   │
│      .100–.199  student Pis (dynamic DHCP scope)                      │
│                                                                       │
└────────┬──────────┬──────────┬──────────┬──────────┬─────────────────┘
         │          │          │          │          │
   ┌─────▼───┐ ┌────▼────┐ ┌───▼─────┐ ┌──▼──────┐ ┌▼─────────┐
   │ student │ │ student │ │ student │ │ student │ │   …      │
   │   Pi    │ │   Pi    │ │   Pi    │ │   Pi    │ │          │
   │  #1     │ │  #2     │ │  #3     │ │  #N     │ │          │
   └─────────┘ └─────────┘ └─────────┘ └─────────┘ └──────────┘

   Each student Pi internally runs the OTLab fabric:
     L1 LAB FABRIC (per Pi — UNIQUE PER STUDENT for SIEM correlation)
        Student N gets:
          dmz-br0  10.75.N.0/24    (operator surface, dashboard)
          pcn-br0  10.30.N.0/24    (PLCs, sensors, IDS)
          ent-br0  10.50.N.0/24    (planned V4.1)
     Routed (not NAT'd) for traffic to teacher SIEM, so logs show
     real source IPs. Internet egress still NAT'd at upstream router.
```

> **Per-student subnet plan**: see [`classroom-installer.md`](classroom-installer.md)
> for the full address table and the per-Pi `/etc/otlab/student.env`
> that drives it.

The three layers stack but **don't share IPs**:

| Layer | Subnet | Scope | Who's on it |
|---|---|---|---|
| **L1 — Lab fabric** | `10.75.N.0/24` + `10.30.N.0/24` (+ `10.50.N.0/24` V4.1) — **unique per student N** | Internal to each Pi, but routable from teacher | Containers inside one student's Pi |
| **L2 — Classroom segment** | `192.168.10.0/24` (default) | Across all Pis + teacher | Instructor laptop, student Pis (eth0), MikroTik or FortiGate gateway |
| **L3 — Operator plane** | Venue WAN + tailscale | Outside the classroom | Instructor's internet uplink, remote ops |

---

## Layer 1 — Lab fabric (inside each student's Pi)

This is what V3.0+ has been building. Full reference:
[`network-architecture.md`](network-architecture.md).

Each student Pi runs the ContainerLab fabric as Docker containers in
the Pi's own network namespace. The fabric is **completely internal
to that one Pi** — other students can't see another student's
`dmz-br0` or `pcn-br0` even though they all use the same subnet.

| Zone | Bridge | Subnet | Containers | Visibility |
|---|---|---|---|---|
| DMZ | `dmz-br0` | `192.168.75.0/24` | firewall .1, dhcp-dmz .2, dashboard .40 | Only the Pi's host kernel |
| PCN | `pcn-br0` | `10.20.30.0/24` | firewall .1, dhcp-pcn .2, modbus-master .43, sensor-sim .70, dnp3 .71, plc-1/2-virt .60/.61, Conpot .50/.51/.52 | Only the Pi's host kernel |
| Enterprise *(V4.1)* | `ent-br0` | `192.168.50.0/24` | firewall .1, dhcp-ent .2, corp-ad .10, etc. | Only the Pi's host kernel |

**Egress from the fabric to the classroom**: the firewall container
NATs (MASQUERADE) outbound traffic via the Pi's wlan0/eth0 onto the
classroom segment. So a sensor-sim container at `10.20.30.70`
reaching `1.1.1.1` appears on the classroom wire as
`192.168.10.<pi-host-ip>` → `1.1.1.1` (source-NAT'd).

**Why this matters operationally**: 30 students all running OTLab with
the same `10.20.30.0/24` PCN is **fine** — those subnets are private
to each Pi. There's no IP collision across students because nothing
crosses the Pi's host boundary unless it's NAT'd first.

---

## Layer 2 — Classroom segment

This is the **physical wire** (or wifi) all the Pis sit on. One
broadcast domain, one subnet, one DHCP server. Everything in the
classroom layer is visible to every other device on it.

### Recommended subnet

`192.168.10.0/24` — easy to remember, doesn't collide with the lab
fabric subnets (75, 30, 50), doesn't conflict with most home/venue
networks (which usually run `192.168.1.0/24` or `192.168.0.0/24`).

You can use any subnet your venue gives you; the only requirements:
- Not `192.168.75.0/24` (DMZ fabric)
- Not `10.20.30.0/24` (PCN fabric)
- Not `192.168.50.0/24` (planned ENT fabric)
- Big enough for teacher + N students + headroom (`/24` = up to 254 hosts)

### Address allocation (suggested)

| Range | Use | How assigned |
|---|---|---|
| `192.168.10.1` | Gateway / FortiGate / venue router | Static on the gateway device |
| `192.168.10.2`–`.9` | Reserved for lab infrastructure (extra teacher hosts, demo gear) | DHCP reservation |
| `192.168.10.10` | **Instructor laptop / teacher panel host** | DHCP reservation (or static on laptop) |
| `192.168.10.20`–`.49` | Reserved (FortiGate AP, switches, future gear) | DHCP reservation |
| `192.168.10.100`–`.199` | **Student Pis** | Dynamic DHCP scope — this is what teacher panel scans |
| `192.168.10.200`–`.250` | Spillover / guest devices | Dynamic |

Then your teacher panel config matches:
```
SCAN_BASE=192.168.10
SCAN_START=100
SCAN_END=199
```

### DHCP

Where does DHCP come from? Three options, pick one:

| Option | What | When to use |
|---|---|---|
| **Venue router's DHCP** | Whatever AP / router you connect to hands out leases | Cheapest. Works if the venue gives you a usable subnet. |
| **FortiGate's DHCP** | Configure DHCP server on the FortiGate's LAN interface | Best when you have the FortiGate anyway. Adds visibility + per-MAC reservations. |
| **Dedicated lab router** | Travel router (GL.iNet, TP-Link) with custom subnet | Most predictable. Carry it in your kit, plug into venue uplink, students see your subnet not theirs. |

For a dedicated lab router with `192.168.10.0/24`:
- WAN port → venue uplink (DHCP from venue)
- LAN side → `192.168.10.1/24` with DHCP enabled
- All student Pis + teacher laptop plug into LAN side

### Switch

A single dumb 8-port (or larger) Gigabit Ethernet switch is fine for
classrooms up to ~15 Pis. Managed switching adds value only if you
want per-port VLAN isolation (Layer-2 student-to-student blocking).

| Class size | Recommendation |
|---|---|
| 1–5 students | Built-in WiFi on the venue router |
| 6–15 students | Single unmanaged 8-port Gigabit switch |
| 16+ students | Managed switch (Netgear GS308E) — supports per-port VLANs for student isolation |

### WiFi vs wired

| | Wired (Ethernet) | WiFi |
|---|---|---|
| Reliability | High | Variable (venue interference) |
| Bandwidth | Plenty (Gigabit) | Often shared, can be slow |
| Setup time | Cabling = time | Plug in router, students join SSID |
| Recommended for events | ✅ | only if logistics force it |
| Recommended for take-home | n/a | ✅ student's home WiFi works fine |

For ICS Village events: wired. For workshops where students bring their
own laptops: WiFi off the venue router is fine; the teacher panel just
needs the venue subnet info.

---

## Layer 3 — Operator plane

What's *outside* the classroom that the instructor needs:

| Resource | What | Reach |
|---|---|---|
| **Venue WAN** | Internet uplink (for apt updates, Docker pulls if anything needs rebuilding mid-event) | Gateway router |
| **Tailscale tailnet** *(optional)* | Reach into students' Pis from anywhere | Instructor's laptop + each Pi advertises its OTLab subnets |
| **Instructor's laptop** | Where the teacher runs the panel from, where SSH originates | On the classroom segment + tailscale |

**Tailscale is optional but useful**: if you set it up on each student
Pi during bootstrap, you can SSH into any of them from anywhere
(coffee shop, hotel) for prep/debug between events. The instructor's
laptop joins the same tailnet and gets routable access to all student
fabrics via subnet routing.

---

## The complete picture (one diagram, all layers)

```mermaid
flowchart TB
    classDef wan    fill:#fff,stroke:#999,color:#000
    classDef class  fill:#e3f0ff,stroke:#1f6feb,color:#000
    classDef stud   fill:#e6f7ec,stroke:#2c8a3f,color:#000
    classDef teacher fill:#fff4e6,stroke:#cc7a00,color:#000
    classDef fab    fill:#f5f5f5,stroke:#666,color:#000,stroke-dasharray:3 3

    wan([Venue WAN / Internet]):::wan

    subgraph CR["Classroom segment · 192.168.10.0/24"]
        gw[".1 gateway / FortiGate / router"]:::class
        teacher_host[".10 instructor laptop<br/>teacher panel container :8080"]:::teacher
        pi1[".101 student Pi #1"]:::stud
        pi2[".102 student Pi #2"]:::stud
        piN[".1NN student Pi #N"]:::stud
    end

    subgraph FAB1["Pi #1 lab fabric (internal)"]
        f1_dmz["dmz-br0 192.168.75.0/24"]:::fab
        f1_pcn["pcn-br0 10.20.30.0/24"]:::fab
    end

    subgraph FAB2["Pi #2 lab fabric (internal)"]
        f2_dmz["dmz-br0 192.168.75.0/24"]:::fab
        f2_pcn["pcn-br0 10.20.30.0/24"]:::fab
    end

    wan --> gw
    gw --- teacher_host
    gw --- pi1
    gw --- pi2
    gw --- piN

    pi1 -. NAT .-> f1_dmz
    pi1 -. NAT .-> f1_pcn
    pi2 -. NAT .-> f2_dmz
    pi2 -. NAT .-> f2_pcn

    teacher_host -- SSH key auth .--> pi1
    teacher_host -- SSH key auth .--> pi2
    teacher_host -- SSH key auth .--> piN
```

Same subnet (`75.0/24`) used inside Pi #1 and Pi #2 — they're isolated
network namespaces, so there's no collision. The classroom segment is
the only place all the Pis converge.

---

## Trust boundaries

Who can talk to whom:

| Source → Destination | Allowed? | How enforced |
|---|---|---|
| Teacher → any student (SSH/22) | ✅ | Teacher's ed25519 pubkey in each student's authorized_keys |
| Teacher → any student (HTTP/8000 for OTLab dashboard) | ✅ | Open by default, no auth at network layer (each Pi has its own basic-auth) |
| Student → teacher (any port) | ⚠️ Allowed at L2, **blocked at the teacher's box** | Teacher's box runs its own firewall (macOS/Linux); no service listens for inbound from students except SSH which only key auth opens |
| Student → another student | ⚠️ Allowed at L2 by default | Block at L3 with FortiGate ACLs OR managed switch VLANs OR per-Pi iptables. Currently students hold no credentials so this is application-layer-safe but not network-layer-isolated. |
| Student lab fabric (internal `.75`/`.30`) → another student | ❌ | Lab fabric is NAT'd; outbound looks like the Pi's classroom IP; inbound to the fabric is blocked by each Pi's own iptables |
| Student → venue WAN | ✅ (NAT'd) | For apt, image pulls, etc. |

### What "students hold no tokens" really means

- No SSH keys anywhere on the student Pi
- No tailscale auth (unless explicitly granted per cohort)
- No cloud credentials (AWS, GCP, etc.)
- No personal data — fresh Pi OS image, lab-only `otadmin/P@ssw0rd!` (then password disabled after teacher lockdown)

A student walking out of class with their Pi (or a copy of its SD card)
gets nothing useful for attacking the wider world. The lab-only
credentials don't work anywhere outside the lab.

---

## Recommended hardware kit (20-student classroom — current production spec)

### Per student (~$300/each)
- 1× **Exaviz Cruiser Carrier Board v1.0** ([product](https://www.exaviz.com/product-page/cruiser-carrier-board-v1-0)) — CM4 carrier with **4× Gigabit Ethernet**, NVMe slot, fanless
- 1× Raspberry Pi Compute Module 4 (8 GB RAM, 32 GB eMMC, WiFi optional)
- 1× NVMe SSD (256 GB) — fits the Cruiser's M.2 slot
- 1× 12V DC power supply (5A, barrel jack — Cruiser's input)
- 1× Cat6 patch cable (1 m, for `otlab-mgmt` port → Cisco switch)
- *Future*: 1× additional Cat6 patch cable (1 m, for `otlab-otext` port → second OT-shared switch)

### Per classroom (instructor's kit)
- 1× Instructor host (laptop or Pi — runs teacher panel + Loki/Grafana SIEM)
- 1× **Cisco Catalyst 2960 24-port** (managed L2 — handles classroom VLAN 10 for all 20 student `otlab-mgmt` ports + teacher + MikroTik trunk uplink)
- 1× **MikroTik router** (RB5009 or similar — handles DHCP, routing, ACLs; see [`reference/router-configs/mikrotik/`](../reference/router-configs/mikrotik/))
- *Future*: 1× any 24-port unmanaged Gigabit switch for the OT-shared segment (VLAN 200 — connects all 20 student `otlab-otext` ports)
- Spare patch cables (24+ for primary, 24+ when OT switch lands)
- 4× power strips (5 Pis each)

### Per-Pi port assignments (Cruiser Keel = 4 ports)

| Port name | Default Linux name | Role | Connected today? |
|---|---|---|---|
| `otlab-mgmt` | `eth0` (onboard) | Pi management / classroom segment — DHCP from MikroTik, SSH, teacher panel | ✅ |
| `otlab-otext` | `eth1` (PCIe NIC #1) | OT lab extension — bridges into `pcn-br0` so real OT gear can attach | 🟡 wired but inactive until 2nd switch arrives |
| `otlab-mirror` | `eth2` (PCIe NIC #2) | SPAN / mirror destination — future out-of-band Suricata feed | ❌ reserved |
| `otlab-spare` | `eth3` (PCIe NIC #3) | Reserved (IPMI-style mgmt, second uplink, etc.) | ❌ reserved |

Port naming is pinned by `scripts/configure-4port-pi.sh` using systemd `.link` files (matches by MAC). Survives reboots, PCIe enumeration changes, and CM4 swaps.

### Estimated total (20-student classroom)

| | $ |
|---|---:|
| 20× student kit ($300 each) | $6,000 |
| Cisco 2960 24-port (used market) | $200 |
| MikroTik RB5009 | $250 |
| Patch cables (40) + power strips (4) | $200 |
| **Subtotal** | **$6,650** |
| Future: 2nd 24-port unmanaged switch | +$80 |
| Future: out-of-band Suricata sensor (Pi 5 + 1TB NVMe) | +$200 |
| **Full-build total** | **~$6,930** |

Per-student cost scales linearly. A 5-student kit-bag (for travel demos) is ~$1,700.

---

## Support runbook

Common issues + first place to look. Grouped by where the symptom shows up.

### Symptom: a student's Pi isn't reachable from the teacher panel

| First check | If that's not it |
|---|---|
| Pi has power? Activity LED solid? | `ping <student-ip>` from the teacher host |
| Student is on the right subnet? `ip addr` on the Pi → wlan0/eth0 in `192.168.10.x` | Check the venue router's connected-devices page |
| sshd running on the Pi? `sudo systemctl status ssh` | Try password SSH (works only before lockdown) |
| Teacher pubkey actually in `~otadmin/.ssh/authorized_keys`? | Re-run `bootstrap-students.sh` for just that IP |

### Symptom: teacher panel shows the Pi as "offline" intermittently

| Likely cause | Fix |
|---|---|
| Pi WiFi flapping on a saturated venue network | Move to wired |
| Pi voltage warning (`vcgencmd get_throttled` != `0x0`) | Better PSU (official Pi 5 27 W) |
| Health probe timing out at default 5s | Bump `SSH_CONNECT_TIMEOUT` in `teacher/teacher.py` |

### Symptom: student can't reach the internet from inside their OTLab fabric

| First check | Fix |
|---|---|
| Pi itself has internet? `ping 1.1.1.1` from Pi host | Fix the Pi's wifi/Ethernet first |
| OTLab firewall is up? `sudo docker ps \| grep clab-otlab-fw` | Redeploy fabric: `containerlab destroy + deploy` |
| Firewall MASQUERADE rules present? `sudo docker exec clab-otlab-fw-dmz-pcn iptables -t nat -L POSTROUTING` | Re-run `start-firewall.sh` exec hook |

### Symptom: students can SSH into each other (shouldn't happen post-lockdown)

| First check | Fix |
|---|---|
| Was `bootstrap-students.sh` actually run? | Re-run for all affected IPs |
| `PasswordAuthentication no` in `/etc/ssh/sshd_config.d/99-teacher-key-only.conf` on the student? | Re-run for that IP |
| Student smuggled in their own SSH key somehow? | Re-image the Pi (only way to be sure) + investigate |
| Student-to-student blocked at L3? | Add FortiGate ACL OR switch to managed switch with per-port VLANs |

### Symptom: classroom WiFi keeps dropping students

| Cause | Mitigation |
|---|---|
| Venue WiFi saturated (other classes, conference attendees) | Switch to wired with the dedicated lab router |
| 2.4 GHz / 5 GHz channel contention | Use 5 GHz only; pick a clean channel |
| Cheap travel router can't handle 15+ clients | Upgrade to a real AP, or use wired |

### Symptom: nothing works post-event-reset

| Cause | Fix |
|---|---|
| Teacher panel volume lost (forgot `-v classroom-state:/var/lib/teacher`) | Re-bootstrap students. Roster + layout will be lost. |
| Student Pis re-imaged but teacher hasn't re-bootstrapped them | Re-run `bootstrap-students.sh` for each |
| FortiGate config drift | Restore from backup; document classroom config in the event-prep checklist |

---

## Pre-event network checklist

Before the cohort arrives:

- [ ] Classroom segment subnet is decided (default suggestion: `192.168.10.0/24`)
- [ ] Gateway / DHCP source is decided (venue / FortiGate / travel router)
- [ ] Switch is plugged in, all student desks have a cable run
- [ ] Instructor laptop joins the segment and gets a stable IP (.10 reservation if possible)
- [ ] Teacher panel container is built locally and tested in demo mode
- [ ] First student Pi is plugged in and `ping <pi-ip>` succeeds from the instructor laptop
- [ ] `bootstrap-students.sh --range <classroom-subnet>.100-199 --dry-run` shows the right targets
- [ ] One full smoke test from `teacher/TESTING.md` passes against a single Pi

If all 7 of those pass, the network is ready for cohort.

---

## See also

- [`classroom-installer.md`](classroom-installer.md) — **install + reset walkthrough** for a 20-student rollout
- [`network-architecture.md`](network-architecture.md) — what's inside one student's Pi
- [`teacher/README.md`](../teacher/README.md) — teacher panel reference + trust model
- [`teacher/TESTING.md`](../teacher/TESTING.md) — 12-case smoke test for 2 student Pis
- [`teacher/siem/README.md`](../teacher/siem/README.md) — Loki + Grafana + Promtail SIEM stack
- [`reference/router-configs/mikrotik/`](../reference/router-configs/mikrotik/) — MikroTik RouterOS config + paste instructions
- [`reference/router-configs/cisco/`](../reference/router-configs/cisco/) — Catalyst 2960 classroom switch config (VLAN 10 + future VLAN 200)
- [`reference/diagrams/`](../reference/diagrams/) — visual diagrams (Mermaid + drawio)
