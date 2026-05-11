# OTLab Virtualization Architecture

The OTLab is **standalone on a single Raspberry Pi** by default — the entire DMZ + Process Control fabric (firewall, DHCP, DNS, virtual PLCs, master/outstation loop, IDS, dashboard) runs as containers in ContainerLab. Optional physical-Pi expansion adds real GPIO and physical Conpot honeypots, integrated via a USB Ethernet adapter bridge-port'd into the PCN segment.

```
                              ┌─── operator browser ───┐
                              │                         │
                              ▼                         │
   ┌─── single Raspberry Pi (Pi 5 16GB) ───────────────┐│
   │                                                    ││
   │   ┌── DMZ · dmz-br0 · 192.168.75.0/24 (L3.5) ──┐ ││  ←─ https://<pi>:8000/
   │   │   firewall  dhcp-dmz  dashboard            │ ││
   │   └────────────────┬─────────────────────────────┘ ││
   │                     │ firewall conduit (iptables)   ││
   │   ┌── PCN · pcn-br0 · 10.20.30.0/24 (L1/L2) ──┐  ││
   │   │   firewall  dhcp-pcn  modbus-master         │  ││
   │   │   sensor-sim  dnp3-outstation               │  ││
   │   │   plc-1-virt  plc-2-virt (OpenPLC)          │  ││
   │   └─────────────────────────────────────────────┘  ││
   │                                                     ││
   │   + Suricata IDS sniffing pcn-br0                  ││
   │   + Cockpit / Portainer / EdgeShark admin UIs       ││
   └──────────────────────────────┬──────────────────────┘│
                                  │ wlan0                 │
                                  └───── internet ────────┘

   Optional physical expansion (Stage 2 in setup-from-scratch.md):
   ┌──────────────────────────────────────────────────────┐
   │   USB Ethernet adapter on the L3 Pi → eth1 → bridge- │
   │   ported into pcn-br0 → lab switch → physical Pis    │
   │     l1-plc-01 (Pi 5, OpenPLC + GPIO Phase 2 hw)      │
   │     l1-hp-01  (Pi 3 B+, Conpot vendor personas)      │
   └──────────────────────────────────────────────────────┘
```

**Why this architecture:**

1. **Single-Pi accessibility.** Most students will only have one Pi. The lab works fully on one Pi — everything that matters for the curriculum (segmentation, firewall, IDS, master/outstation, attack/detect) is in containers. Physical Pis are nice-to-have, not required.
2. **Industry-authentic stack.** Even on a single Pi: real iptables segmentation, real Suricata signature-based detection, real Modbus + DNP3 protocol traffic on a wire (the bridge counts).
3. **Reproducible.** Whole topology is YAML — `containerlab deploy`, `containerlab destroy`. Idempotent install scripts, no manual config snowflakes.
4. **Expandable when you want.** Physical Pis, RS485 gear, ESP32 wireless, real Conpot personas all bolt onto the same fabric without touching the core. Each stage is independent and optional.
5. **Industry-grade DMZ pattern.** DMZ (L3.5) ↔ Firewall conduit ↔ PCN (L1/L2) is exactly the Purdue-model topology every real OT shop runs. Students see the actual policy enforced live on the Firewall tab.

---

## What runs where

### `l3-mon-01` — the required Pi (single-Pi mode)

The lab's primary host. Runs **all** the DMZ + PCN services as containers. The Pi 5 16GB is the recommended platform; 8GB works for the core fabric and is tight once Suricata + admin UIs are added. Pi 4 8GB also works.

Architecture-wise, the Pi is a Linux host running:

- Docker daemon
- ContainerLab orchestrator
- Two Linux bridges (`dmz-br0`, `pcn-br0`)
- 9 containers (firewall + 2 DHCP + dashboard + modbus-master + 2 OpenPLC + sensor-sim + dnp3-outstation)
- Suricata IDS on the host (sniffs `pcn-br0` in promiscuous mode)
- Optional admin UIs: Cockpit, Portainer, EdgeShark
- Optional: Tailscale subnet router (advertises both `192.168.75.0/24` + `10.20.30.0/24` to your tailnet)

In single-Pi mode, **nothing else is required** — wlan0 provides internet for image pulls + apt updates, and the entire lab fabric is internal to the Pi's network namespace.

### Optional: `l1-plc-01` — physical OpenPLC Pi (Stage 2)

A second Pi (Pi 5 recommended; Pi 4 works) running real OpenPLC with real GPIO. Joins `pcn-br0` via macvlan when a USB Ethernet adapter is bridge-port'd into `pcn-br0` on the L3 host. Lives at `10.20.30.47`.

The "physical curriculum" lives here:
- Pushbutton input (uxcell 12 mm momentary)
- AD16 dual-color indicator (24 V)
- LED strip (12 V)
- Velocio Ace 1600 (USB-attached PLC, programmed via vBuilder on Windows)

When integrated, students see "real PLC + virtual PLC at the same master's poll loop" on the dashboard.

### Optional: `l1-hp-01` — physical Conpot Pi (Stage 3)

A Pi 3 B+ (or 4) running the Conpot vendor honeypot fabric — three personas (Siemens S7-200, Schneider M340, Allen-Bradley CompactLogix) at `10.20.30.50/51/52`. Each presents a vendor-themed HTTP admin page and speaks vendor-canonical protocols.

Joins `pcn-br0` via the same USB Ethernet adapter as `l1-plc-01`.

---

## Phasing

### V1 — Virtual fabric MVP *(this commit)*

**What ships:**
- `virtual/topologies/otlab.clab.yaml` — main topology (firewall + dual OpenPLC + sensor-sim + DNP3 + dashboard)
- `virtual/dockerfiles/{sensor-sim,dnp3-outstation,firewall,openplc,dashboard}/` — ARM64 Dockerfiles
- `scripts/install-virtual-lab.sh` — bootstrap script

**What works after V1:**
- One Pi (`l3-mon-01`) runs the entire virtual fabric
- DMZ → PCN policy enforced by firewall container
- Two virtual OpenPLC instances + sensor-sim + DNP3
- Dashboard reachable at `https://l3-mon-01:8000/`
- Physical Pis (`l1-plc-01`, `l1-hp-01`) untouched, continue running their existing services on the existing physical segment

**What V1 does NOT do:**
- Doesn't bridge virtual ↔ physical yet (separate segments)
- No Ignition / Authentik / Guacamole / Suricata yet
- No CODESYS

### V2 — DMZ services + physical integration

**V2.x (shipped):** modbus-master container (deterministic poll loop) +
Suricata IDS (host-mode sniffing `pcn-br0`) + physical Pi macvlan
integration via USB NIC bridge-port'd into `pcn-br0`. Verified
end-to-end: virtual modbus-master polls physical OpenPLC, Suricata
catches cross-segment FC6 writes from physical sources.

**V2.y (shipped):** `l3-mon-01` is now the gateway, firewall, DHCP,
and DNS server for both internal networks. Per-zone dnsmasq DHCP
containers (`dhcp-dmz` at `.2` with scope `.150-.199`; `dhcp-pcn`
at `.2` with scope `.200-.250`). Firewall container runs dnsmasq
as a DNS forwarder bound to `192.168.75.1` + `10.20.30.1`. SNAT
for DMZ → PCN traffic so physical Pis with default-route-via-wlan0
can still reply (their reply-path ignorance is bypassed by
sourcing as `.1`). NetworkManager pinned away from clab veths +
bridge-port'd NICs to prevent the host from accidentally DHCP'ing
itself off the lab fabric.

**V2.y.2 (shipped):** DHCP_HOSTS env-driven static reservations.
Five reservations baked in for the canonical physical devices
(l1-plc-01 .47, l1-hp-01 .48, three Conpot personas at .50/.51/.52).
Adding a new reservation is a one-line YAML edit + `containerlab
deploy --reconfigure` — no image rebuild.

**V2.y.3 (shipped):** Dashboard refresh — extended HOSTS + probe
loop to render cards for every container/host. New PCN Services and
Lab Infrastructure rows alongside the legacy PLC + Honeypot rows.
Modbus-master writes a structured tick state file to a shared volume
the dashboard reads; replaces the old tcpdump-based poll-rate sniff
that was broken for the dashboard's network namespace.

**V2.y.4 (shipped):** `/etc/otlab/bridge-attach.conf` makes physical-NIC
bridging opt-in (per-NIC `<nic>=<bridge>` lines, idempotent). Default
config has DMZ on, PCN off — matches the "shared lab switch with no
VLANs, don't want DHCP cross-talk" reality. Synoptic data-source
priority refactored to prefer `modbus-master.master_state` over the
legacy `l1-plc-01` mirror path.

**V2.y.5 (shipped):** Three new dashboard tabs — IDS, Firewall, DHCP.
Firewall container exports iptables + conntrack + DNS log to a shared
state volume (`/var/lib/otlab/fw-state/`); dashboard mounts it RO.
DHCP servers expose leases + reservations + transactions same way.
IDS tab computes counts, top-N, hourly timeline by stream-reading
the EVE log so it scales past 50 MB+ files.

**V2.z (next):**
- Authentik (IdP/SSO) added to the topology (authentik-server + authentik-worker + postgres + redis containers)
- Ignition Gateway (Maker edition, free) — full SCADA on the DMZ
- Apache Guacamole — clientless RDP/SSH/VNC gateway
- Suricata in IPS mode for protocol-aware FC5/6 blocking
- Configure-DHCP write path (browser edits a reservation → dashboard writes a runtime hostsfile + SIGHUP dnsmasq)
- Sidecar Modbus sniffer container on pcn-br0 to fix the wire feed (current dashboard sniff sees no PCN traffic)

**What works after V2:**
- Federated SSO across all OT services via Authentik OIDC
- Real SCADA experience (Ignition Designer, tag browser, alarm pipelines, historian)
- Browser-based jump-host pattern for SSH-into-PLCs (Guacamole)
- Live IDS alerts on the PCN
- Physical PLCs join the virtual fabric — students see "real PLC + virtual PLC at the same Modbus master's poll loop"

### V3 — CODESYS + curriculum scenarios

**What ships:**
- CODESYS Control SL container (vendor runtime — Festo/Wago/ABB use this)
- CODESYS Web HMI container
- Curriculum modules contrasting OpenPLC (open-source IEC 61131-3) vs CODESYS (vendor runtime)
- OPC-UA scenarios (CODESYS exposes a real OPC-UA server natively)

### V4 — Optional

- AI HAT integration (process anomaly detection — see decision log)
- Take-home topologies (containerlab YAML for student laptops)
- Vendor coverage expansion (Honeywell, Yokogawa, GE Mark VIe sims)

---

## Network plan

| Segment | CIDR | Purpose | Bridge |
|---|---|---|---|
| Lab DMZ (L3.5) | `192.168.75.0/24` | Operations zone — Ignition, Guacamole, Authentik, Dashboard | `dmz-br0` |
| Lab PCN (L1/L2) | `10.20.30.0/24` | Process Control — virtual + physical PLCs, sensor-sim, Conpot | `pcn-br0` |
| ContainerLab mgmt | `172.20.20.0/24` | clab internal — image pulls, control-plane | `clab-otlab-mgmt` |
| Tailscale tailnet | `100.64.0.0/10` | Operator overlay — laptop-to-lab from anywhere | (host wlan0/eth0) |
| Operator mgmt WiFi | varies | SSH from operator's laptop on home WiFi | (host wlan0) |

### IP allocation within the virtual fabric

```
192.168.75.0/24  (DMZ — L3.5; bridge-port'd to host eth0 → physical wire)
  .1    fw-dmz-pcn         firewall, gateway for DMZ + DNS forwarder (dnsmasq)
  .2    dhcp-dmz           DHCP server (dnsmasq, DHCP-only mode)
  .10   authentik-server   (V2)
  .11   authentik-postgres (V2)
  .12   authentik-redis    (V2)
  .20   ignition           (V2)
  .30   guacamole          (V2)
  .40   dashboard          (V1 — primary entry point for booth visitors)
  .150 -- .199              DHCP scope (dynamic leases for new clients)

10.20.30.0/24  (PCN — L1/L2; bridge-port'd to host eth1 USB NIC → physical wire)
  .1    fw-dmz-pcn         firewall, gateway for PCN + DNS forwarder (dnsmasq)
  .2    dhcp-pcn           DHCP server (dnsmasq, DHCP-only mode)
  .47   l1-plc-01          (physical, joins via macvlan in V2)
  .48   l1-hp-01           (physical, joins via macvlan in V2)
  .50-.52  Conpot personas (physical, on l1-hp-01)
  .60   plc-1-virt         virtual OpenPLC #1 (master role)
  .61   plc-2-virt         virtual OpenPLC #2 (outstation role)
  .70   sensor-sim         virtual Modbus outstation
  .71   dnp3-outstation    virtual DNP3 outstation
  .80   codesys-plc        (V3)
  .81   codesys-hmi        (V3)
  .90   suricata           (V2 — sniffs the bridge, no L3 IP needed but listed for clarity)
  .200 -- .250              DHCP scope (dynamic leases for new clients)
```

Static reservations live BELOW the DHCP scope; the scope itself is for
ad-hoc clients (operator laptop, demo devices, future PLCs that haven't
been pinned yet). To make a new device sticky, add a `dhcp-host=` entry
inside `virtual/dockerfiles/dhcp/entrypoint.sh` (commented examples are
in the file) and rebuild the dhcp image.

---

## Build + deploy

### One-shot install (V1)

```bash
# From the repo root, on your laptop:
./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
```

The script:
1. Stages the `virtual/`, `plc/`, and `dashboard/` trees onto `l3-mon-01`
2. Installs ContainerLab via the official one-liner (apt-pinned version)
3. Builds the OTLab Docker images (~20-30 min first run; cached afterward)
4. Deploys the topology
5. Verifies bridges + container connectivity + firewall policy

### Common operations

```bash
# Inspect topology
sudo containerlab inspect -t ~/lab/virtual/topologies/otlab.clab.yaml --format table

# Tear down + redeploy (clean state)
sudo containerlab destroy -t ~/lab/virtual/topologies/otlab.clab.yaml --cleanup
sudo containerlab deploy  -t ~/lab/virtual/topologies/otlab.clab.yaml

# Container shell
sudo docker exec -it clab-otlab-sensor-sim bash
sudo docker exec -it clab-otlab-fw-dmz-pcn bash

# Firewall stats (live packet/byte counters per rule)
sudo docker exec clab-otlab-fw-dmz-pcn iptables -nvL FORWARD --line-numbers

# Logs
sudo docker logs clab-otlab-dashboard
sudo docker logs clab-otlab-fw-dmz-pcn
```

### Updating a single service

```bash
# Edit code in plc/sensor-sim.py, rebuild + redeploy that container only:
cd ~/lab
sudo docker build -t otlab/sensor-sim:latest -f virtual/dockerfiles/sensor-sim/Dockerfile .
sudo containerlab deploy -t virtual/topologies/otlab.clab.yaml --reconfigure
```

---

## Integrating physical Pis (V2)

When the L1 USB-NIC + Authentik/Ignition lands, physical Pis bridge in via macvlan:

```yaml
# Excerpt of the V2 topology — adds a host-link from pcn-br0 to the
# physical USB ethernet interface on l3-mon-01.
topology:
  nodes:
    pcn-physical-uplink:
      kind: host
      # Containerlab's `host` kind connects a node interface to a real
      # interface on the Linux host running clab. The macvlan is created
      # such that physical traffic on eth1 appears on pcn-br0.

  links:
    - endpoints: ["pcn-br0:any", "pcn-physical-uplink:eth1"]
```

After V2 macvlan integration:
- Physical `l1-plc-01` (`10.20.30.47`) appears on `pcn-br0` alongside virtual PLCs (`10.20.30.60`, `.61`)
- Physical `l1-hp-01` (`10.20.30.48`) appears with Conpot personas (`.50/.51/.52`)
- Suricata sniffs the unified `pcn-br0`; sees both virtual + physical traffic
- The DMZ firewall enforces policy uniformly for both

---

## When to use virtual vs physical

| Use case | Where |
|---|---|
| **DEF CON booth tactile demo** — student touches a button, watches a relay click | Physical (`l1-plc-01` Phase 2 hardware) |
| **Modbus/DNP3 protocol teaching** | Either — virtual is more reproducible |
| **Multi-PLC + multi-zone scenarios** | Virtual (fan-out without more hardware) |
| **CTF-scale scenarios (10+ PLCs)** | Virtual |
| **Real PLC vendor runtime (CODESYS)** | Virtual (V3) |
| **SCADA / HMI / historian / alarming experience** | Virtual (Ignition) |
| **SOC / detection / Suricata-fed dashboards** | Virtual (centralized sniff point on `pcn-br0`) |
| **CI / regression testing** | Virtual (containerlab on GitHub Actions runners) |
| **Take-home for students** | Virtual (clone repo, deploy on laptop) |
| **Reproducibility after disaster** | Virtual (image rebuild from Dockerfiles) |

The physical side keeps the **on-the-wire authenticity** narrative — packets between two real devices over real ethernet, with real timing/jitter — that the booth visitor leans on. The virtual side keeps the **scale and reproducibility** narrative.

---

## Resource budget — Pi 5 16GB

V1 baseline (~3 GB used):
- Pi OS + Docker + ContainerLab orchestrator: ~700 MB
- Firewall container (incl. dnsmasq DNS forwarder, V2.y): 35 MB
- Two OpenPLC containers: 400 MB
- sensor-sim + DNP3 outstation: 60 MB
- modbus-master container (V2.x): 50 MB
- DHCP containers (dhcp-dmz + dhcp-pcn, V2.y): 30 MB
- Dashboard: 80 MB
- Suricata (V2): 300 MB
- Buffer: ~1.5 GB

V2 layer (~6.5 GB total):
- Authentik (4 containers): 1 GB
- Ignition: 2 GB
- Guacamole: 600 MB

V3 layer (~7.5 GB total):
- CODESYS Control SL: 500 MB
- CODESYS Web HMI: 300 MB

Comfortable on Pi 5 16GB. Tight but workable on Pi 5 8GB (V1 only).

---

## Failure modes + recovery

**Container OOM-killed.** Pi 5 16GB has plenty of headroom; if it happens, check `dmesg` for the offender and re-deploy. If it's a recurring issue, drop to a smaller stack (e.g., skip CODESYS Web HMI in V3, run just the runtime).

**Image build fails on ARM64.** The OpenPLC build is the longest pole (~15-20 min). If it fails, check `sudo docker build --no-cache -t otlab/openplc:latest -f virtual/dockerfiles/openplc/Dockerfile .` for the actual error. matiec compile errors are typically stale apt deps — the bookworm-slim base should have them all, but if not, add the missing dev package to the Dockerfile.

**Containerlab can't create bridges.** `sudo modprobe br_netfilter`. Pi OS Lite doesn't load it by default. If still failing, ensure `iptables` (not `nftables`) is the default — `sudo update-alternatives --set iptables /usr/sbin/iptables-legacy` (containerlab + Docker iptables-mode often want legacy).

**Bridge isolation failure (DMZ traffic leaking to PCN).** The bridge `iptables` rules need `net.bridge.bridge-nf-call-iptables=1` for L3 filtering on bridged traffic. Set in `/etc/sysctl.d/99-otlab-bridge.conf` (the install script does this).

**Lost dashboard reachability.** Restart the container: `sudo docker restart clab-otlab-dashboard`. State (audit log + pcap captures) lives in volumes, persists across restarts.

**Whole topology corrupt.** Nuclear option:
```bash
sudo containerlab destroy -t ~/lab/virtual/topologies/otlab.clab.yaml --cleanup
sudo docker system prune -af   # only if you want to rebuild images too
sudo bash ~/lab/scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
```

---

## Admin + observability UIs

Three companion web UIs run alongside the ContainerLab fabric on `l3-mon-01` for operator convenience:

| URL | What it does |
|---|---|
| `https://l3-mon-01:9090/` | **Cockpit** — Linux server admin (services, networking, storage, journal, terminal). Useful when SSH isn't convenient. Login: `otadmin` / `P@ssw0rd!`. |
| `https://l3-mon-01:9443/` | **Portainer CE** — full-featured Docker UI. Click any container (clab-otlab-*, edgeshark-*, portainer itself) for live logs, exec shell, restart, inspect resource usage. First visit asks you to create an admin account (12+ char password — Portainer enforces this). |
| `http://l3-mon-01:5001/`   | **EdgeShark** — live packet capture in the browser, by Siemens. Topology-aware view of every netns + container interface. Click an interface → live tcpdump streams in browser; click "Wireshark" → opens the live stream in local Wireshark via `cshargextcap`. The curriculum-front-of-house tool. |
| `containerlab graph -t topologies/otlab.clab.yaml` | Built-in topology visualizer (port 50080). Lightweight, just shows node states. |

**Why three UIs instead of one?** No single tool does all three jobs well today. Cockpit owns Linux admin; Portainer owns Docker admin; EdgeShark owns live packet capture. There's no first-class Cockpit plugin for ContainerLab (`srl-labs/cockpit-containerlab` doesn't exist; `clab-ui` is at v0.x with 1 star). For day-to-day topology work, the CLI (`containerlab inspect/deploy/destroy`) plus a terminal in any of the above UIs is still the workflow.

Install scripts:
- [`scripts/install-cockpit.sh`](../scripts/install-cockpit.sh)
- [`scripts/install-portainer.sh`](../scripts/install-portainer.sh)
- [`scripts/install-edgeshark.sh`](../scripts/install-edgeshark.sh)

---

## Cross-references

- [`naming-schema.md`](naming-schema.md) — canonical hostnames, IPs, services
- [`network-topology.md`](network-topology.md) — physical NIC ↔ virtual fabric mapping (current + future-state)
- [`lab-architecture.md`](lab-architecture.md) — overall system architecture
- [`architecture-evolution.md`](architecture-evolution.md) — phase plan + segmentation history
- [`curriculum.md`](curriculum.md) — teaching modules + scenario walkthroughs
- [`../virtual/topologies/otlab.clab.yaml`](../virtual/topologies/otlab.clab.yaml) — the topology itself
