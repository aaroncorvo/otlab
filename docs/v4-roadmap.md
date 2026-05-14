# OTLab V4 Roadmap — aligning to the future-state architecture

The V4 roadmap takes the current shipped lab (V3.0+) and evolves it toward
the future-state architecture (3-zone Purdue model + SSO + jump server +
VyOS firewall + CODESYS) **without re-IP'ing anything** that currently
works.

> **What we already built wins.** The future-state diagram uses different
> subnets (172.16.64.0/24 DMZ, 192.168.64.0/24 PCN, 10.0.64.0/24 ENT,
> 192.168.10.0/24 inside Docker, etc.). We keep our current IP plan.
> The diagram is shape/intent only. See
> [`docs/network-architecture.md`](network-architecture.md) for the
> full diagram-vs-reality crosswalk.

> **TL;DR**: keep `192.168.75.0/24` for DMZ and `10.20.30.0/24` for PCN.
> Add `192.168.50.0/24` for L4 Enterprise. Ship five chunks (V4.0–V4.4)
> that are independently useful and independently mergeable.

---

## Target architecture (current-IP scheme)

```
                                      Internet
                                          │
                       ┌──────────────────┴──────────────────┐
                       │       Upstream WAN gateway          │
                       └──────────────────┬──────────────────┘
                                          │ WAN
                       ╔══════════════════╧═══════════════════════════════╗
                       ║   l3-mon-01 (Pi 5 / future: CM5 carrier)         ║
                       ║                                                   ║
   eth0 ───┐           ║   ┌─── L4 Enterprise · ent-br0 ──────────────┐   ║
           ▼           ║   │   192.168.50.0/24 · Untrusted             │   ║
        ent-br0  ◄─────╫───┤   .1   firewall (fw-ent-dmz)              │   ║
                       ║   │   .2   dhcp-ent                            │   ║
                       ║   │   .10  corp-ad         (faux AD/LDAP)      │   ║
                       ║   │   .20  corp-file       (faux SMB share)    │   ║
                       ║   │   .40  operator-ws     (engineering laptop)│   ║
                       ║   └──────────────────┬─────────────────────────┘   ║
                       ║                      │ fw conduit                  ║
                       ║   ┌─── L3.5 DMZ · dmz-br0 ──────────────────────┐ ║
   eth1 ───┐           ║   │   192.168.75.0/24 · Operations zone          │ ║
           ▼           ║   │   .1   firewall   (fw-dmz-pcn)               │ ║
        dmz-br0 ◄──────╫───┤   .2   dhcp-dmz                              │ ║
                       ║   │   .10  authentik-server    [V4.2]            │ ║
                       ║   │   .11  authentik-postgres  [V4.2]            │ ║
                       ║   │   .12  authentik-redis     [V4.2]            │ ║
                       ║   │   .20  ignition-scada      [V4.5 future]     │ ║
                       ║   │   .30  guacamole           [V4.2]            │ ║
                       ║   │   .40  dashboard           ✓                 │ ║
                       ║   └──────────────────┬─────────────────────────┘   ║
                       ║                      │ fw conduit                  ║
                       ║   ┌─── L1/L2 PCN · pcn-br0 ─────────────────────┐ ║
   eth2 ───┐           ║   │   10.20.30.0/24 · Process Control            │ ║
           ▼           ║   │   .1   firewall (shared with DMZ side)       │ ║
        pcn-br0 ◄──────╫───┤   .2   dhcp-pcn                              │ ║
                       ║   │   .43  modbus-master            ✓            │ ║
                       ║   │   .50  conpot-siemens      [V4.0]            │ ║
                       ║   │   .51  conpot-schneider    [V4.0]            │ ║
                       ║   │   .52  conpot-rockwell     [V4.0]            │ ║
                       ║   │   .60  plc-1-virt (OpenPLC)     ✓            │ ║
                       ║   │   .61  plc-2-virt (OpenPLC)     ✓            │ ║
                       ║   │   .70  sensor-sim               ✓            │ ║
                       ║   │   .71  dnp3-outstation          ✓            │ ║
                       ║   │   .80  codesys-plc         [V4.4]            │ ║
                       ║   │   .81  codesys-hmi         [V4.4]            │ ║
                       ║   └──────────────────────────────────────────────┘ ║
                       ║                                                     ║
                       ║   Host services:                                    ║
                       ║     wlan0 → operator wifi / mgmt / tailscale        ║
                       ║     Suricata IDS (sniffs pcn-br0 + ent-br0)         ║
                       ║     Cockpit / Portainer / EdgeShark admin UIs       ║
                       ╚═════════════════════════════════════════════════════╝
```

### Key design decisions vs. the diagram

| Choice | Rationale |
|---|---|
| **Keep DMZ at `.75/24`, PCN at `.30/24`** | Current docs, screenshots, Suricata rules, DHCP reservations all reference these — re-IP'ing would invalidate ~30 commits of work |
| **L4 Enterprise: `192.168.50.0/24`** | Easy to remember alongside DMZ's `.75` and operator wifi's `.120`. No overlap with anything we have. |
| **One firewall container with 3 interfaces (V4.0)** | Simpler than two separate firewalls. iptables policy stays in one place. We can swap to two-VyOS-containers in V4.3 if we want full diagram fidelity. |
| **Conpot as containers, not physical** | Removes the "you need three Pis" hardware barrier. Conpot's official Docker image exists. Physical l1-hp-01 remains supported as Stage 2+ expansion. |
| **Authentik, Guacamole stay clab nodes** | Diagram shows them in a "Docker" pane separate from "ContainerLab" — same outcome, simpler tooling if they're all in the clab topology |

---

## V4 chunks

Five independent shippable commits. Each delivers value on its own; you
can pause anywhere and the lab is still useful.

### V4.0 — Virtual Conpot (highest ROI, lowest risk)

**Goal**: ship the honeypot fabric on a single Pi. Current Conpot
deployment requires `l1-hp-01` (Pi 3 B+) — most users won't have that.

**What ships**:
- 3 new clab nodes: `conpot-siemens`, `conpot-schneider`, `conpot-rockwell`
- Each pulls upstream `honeynet/conpot:latest`, gets a static IP on `pcn-br0` (.50/.51/.52)
- DHCP reservations updated to keep the existing MACs/IPs
- Dashboard's Honeypot Fabric row works in single-Pi mode (it already shows the cards; they'll just turn green now)
- Conpot logs bind-mounted to a host path so the dashboard's honeypot-intel panel reads them
- Optional gate: `OTLAB_CONPOT_VIRTUAL=1` env in topology (default on); set to `0` to keep using physical `l1-hp-01`

**Effort**: ~200 LOC YAML + topology + 1 new doc paragraph

**Outcome**: 13/13 → 16/16 cards up by default. Conpot intel populates. No
breaking changes — physical Pi expansion still works.

---

### V4.1 — L4 Enterprise zone

**Goal**: add the third Purdue level so the DMZ pattern is properly
contextualized. Real Purdue teaching needs an "outside" that's separate
from PCN.

**What ships**:
- New bridge `ent-br0` on `192.168.50.0/24`
- New container `dhcp-ent` (third DHCP server, same image, env-driven)
- Firewall container gets a third interface (`eth3`) and IP `.1` on the new bridge
- iptables policy: ENT ↔ DMZ allowed for SSO/Guacamole reaches; ENT ↔ PCN blocked
- A few placeholder containers as the "untrusted enterprise" persona:
  - `corp-ad`: a tiny FreeIPA or just a stub (LDAP on :389)
  - `corp-file`: a Samba container at `.20`
  - `operator-ws`: a desktop-like container with a browser, for "engineering laptop" demos
- New bridge-attach config: `eth2=ent-br0` (when CM5 + multi-NIC carrier lands)
- Dashboard updates: new "Enterprise" row on Overview tab; topology SVG shows three zones

**Effort**: ~400 LOC topology + firewall script + dashboard

**Outcome**: full L4→L3→L2 segmentation. Suricata watches the ENT↔DMZ
boundary too. Address plan extends cleanly.

---

### V4.2 — Authentik IdP + Guacamole jump server

**Goal**: federated SSO across DMZ services. Jump-server pattern for
controlled access to PCN devices.

**What ships**:
- 4 new clab nodes:
  - `authentik-server` (`.10`)
  - `authentik-worker`
  - `authentik-postgres` (`.11`)
  - `authentik-redis` (`.12`)
- 1 new clab node: `guacamole` (`.30`) — Apache Guacamole with native protocol support
- Dashboard integrates: "Login with Authentik" optional, replaces basic auth eventually
- Guacamole's web UI proxies SSH/RDP/VNC to PCN devices (OpenPLC web UIs, physical Pi shells)
- Topology env flag `OTLAB_AUTHENTIK=1` to gate (memory-heavy; Pi 5 8GB users may want it off)

**Effort**: ~500 LOC. Authentik is the heaviest single thing in the
roadmap (4 containers, ~1 GB working set).

**Outcome**: real SSO experience. Jump-server pattern teaches "operators
don't SSH directly to PLCs."

---

### V4.3 — VyOS firewall migration (optional)

**Goal**: replace the iptables container with VyOS for diagram-accurate
authenticity.

**What ships**:
- 2 new clab nodes using VyOS image (clab has a `vyos` kind):
  - `fw-ent-dmz` (Enterprise ↔ DMZ)
  - `fw-dmz-pcn` (DMZ ↔ PCN) — replaces current iptables container
- VyOS config files translating the current iptables rules (allow Modbus/HTTP, deny PCN→DMZ initiation, SNAT, etc.)
- Suricata still sniffs `pcn-br0` (unchanged)
- Dashboard Firewall tab learns to read VyOS config dumps (slightly different format than `iptables -nvL`)
- Topology env flag `OTLAB_FIREWALL=vyos|iptables` so users can pick

**Effort**: ~600 LOC + significant testing. Riskiest chunk because the
firewall is currently rock-solid.

**Recommendation**: ship this LAST, after the rest of V4 is stable. Keep
iptables as the default; VyOS is opt-in.

---

### V4.4 — CODESYS Control SL

**Goal**: vendor PLC runtime alongside OpenPLC. Demonstrates CODESYS is
what Festo/Wago/ABB ship.

**What ships**:
- `codesys-plc` clab node (`.80`) running CODESYS Control SL ARM64
  - The hard part: CODESYS Control SL's ARM64 image isn't on Docker Hub.
    Need to build from CODESYS' .deb or use their Raspberry Pi runtime
  - Fallback: package it from the CODESYS Control for Raspberry Pi SL package
- `codesys-hmi` clab node (`.81`) for the CODESYS Web HMI
- OpenPLC vs CODESYS comparison lesson in the curriculum

**Effort**: ~300 LOC topology + the Dockerfile is where the time goes

**Outcome**: students compare open-source vs vendor PLC runtimes
side-by-side. OPC-UA scenarios become possible (CODESYS has native
OPC-UA server).

---

## Execution order + dependencies

```
   V4.0 ─────► V4.1 ─────► V4.2 ─────► V4.3
   Conpot      L4 zone     Authentik   VyOS
   (no deps)   (no deps)   + Guacamole (depends on V4.1)
                            (depends    
                             on V4.1)

                                       V4.4
                                       CODESYS
                                       (independent)
```

V4.0 and V4.1 are independent — could ship in either order. V4.2 depends
on V4.1 (Authentik wants the ENT zone for the "external identity
provider" teaching narrative). V4.3 depends on V4.1+V4.2 (more bridges =
more firewall complexity). V4.4 is independent.

---

## What stays the same (no breaking changes)

| Existing artifact | V4 impact |
|---|---|
| DMZ subnet `192.168.75.0/24` | unchanged |
| PCN subnet `10.20.30.0/24` | unchanged |
| All existing container IPs (`.40`, `.43`, `.60-.61`, `.70-.71`) | unchanged |
| Existing DHCP reservations | unchanged (add new ones for ENT) |
| Suricata rules | unchanged; add a few for the ENT zone |
| Dashboard URLs + creds | unchanged |
| `bridge-attach.conf` | unchanged for current NICs; new line for `eth2=ent-br0` |
| `install-virtual-lab.sh` from-scratch flow | unchanged (just gains more image builds) |
| Physical Pi expansion (Stage 2+) | unchanged; physical l1-hp-01 stays supported via env flag |

A user running V3.0 today can `git pull` into V4.0 and re-run
`install-virtual-lab.sh` — they'll get Conpot in their dashboard
automatically and nothing else changes.

---

## Hardware: Pi 5 vs CM5

The future-state diagram shows an RPI CM5 (Compute Module 5) on a multi-NIC
carrier. That's a hardware path, not a software requirement.

- **Pi 5 (current)**: 1× onboard NIC + 1× USB Ethernet adapter = 2 NICs total. Works for V3.0 (single physical zone bridged in). For V4.1+ (3 zones), you can either:
  - Use 1 USB adapter for L4 Enterprise (eth1 USB-NIC #1 → ent-br0)
  - Or skip physical-bridge for L4 entirely (most teaching value is in the *virtual* L4 zone, not physical Enterprise devices)
- **CM5 + carrier (future)**: 3+ onboard NICs. Cleaner, but not required. Same software.

The bridge-attach config already supports up to 4 NICs (`eth0-eth3`),
so the only hardware change is "plug more cables in."

---

## Roadmap doc + open questions

This doc lives at [`docs/v4-roadmap.md`](v4-roadmap.md). Edits welcome.

Open questions for you to decide:

1. **Start with V4.0 (Conpot) or V4.1 (L4 zone)?** I'd lean Conpot —
   pure addition, no architectural change, lights up dashboard cards
   that are currently dim.
2. **Do you want Authentik + Guacamole, or skip straight to VyOS?**
   Authentik adds memory pressure on 8GB Pis but teaches the SSO pattern.
   VyOS doesn't add memory but is more disruptive to switch to.
3. **CODESYS — pursue or defer?** It's the only chunk that depends on
   external/proprietary software. If CODESYS' ARM64 distribution is a
   pain, we can punt to V5 indefinitely.
4. **Cockpit on :443 vs :9090?** Diagram shows :443. Pros: matches "https://" muscle memory. Cons: collides with anything else wanting :443. I'd leave at :9090 and put a note in the README.

---

## Want me to start?

Tell me which chunk to tackle first and I'll start building. My
recommendation: **V4.0 (Conpot)**, because it's:
- Pure addition (zero risk to existing lab)
- Highest visible impact (3 more cards green by default)
- Smallest code change (~200 LOC)
- Unlocks honeypot teaching scenarios for single-Pi users

Once V4.0 ships, we measure the time/energy it took and decide on V4.1
vs V4.2 next.
