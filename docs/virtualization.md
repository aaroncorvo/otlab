# OTLab Virtualization Architecture

The lab runs in **dual mode**: a virtualized core on a single Pi (`l3-mon-01`) plus physical Pi extensions for on-the-wire authenticity. ContainerLab orchestrates the virtual side; the physical Pis integrate via macvlan on a USB-NIC into the same Process Control bridge.

This is the architecture from the team's whiteboard diagram:

```
  Industrial DMZ — Level 3.5         (dmz-br0,  192.168.75.0/24)
    Ignition SCADA  ─  Apache Guacamole  ─  Authentik  ─  Dashboard
                                │
                          ┌─────┴─────┐
                          │ Firewall  │   ◄── Conduit: DMZ ↔ PCN
                          │ container │
                          └─────┬─────┘
                                │
  Process Control Network        (pcn-br0,  10.20.30.0/24)
    CODESYS Web HMI  ─  CODESYS PLC  ─  OpenPLC  ─  sensor-sim  ─  DNP3
                                │
                                ▼ macvlan to USB NIC (eth1)
                                │
                  ┌─────────────┴─────────────┐
                  │                           │
         physical l1-plc-01           physical l1-hp-01
         (Pi 5 + Phase 2 hw)          (Pi 3 B+ Conpot fabric)
```

**Why this architecture:**

1. **Industry-authentic stack.** Ignition + CODESYS + Authentik + DMZ pattern is exactly what a real OT shop runs.
2. **Physical hardware investment preserved.** Phase 2 hardware (relays, AD16, LED strip, pushbutton) lives on the physical `l1-plc-01` — that's the on-the-wire teaching artifact.
3. **L3 segment break without a managed switch.** Bridge-based virtual segmentation + a containerized firewall achieves real Purdue-aligned policy enforcement without buying VLAN-capable hardware.
4. **Reproducible.** Whole topology is YAML — `containerlab deploy`, `containerlab destroy`. Students can clone the repo and run the lab on their laptop.
5. **L1 backfill obsolete.** Multi-PLC scenarios come from virtual fan-out instead of buying more Pis.

---

## What runs where

### `l3-mon-01` — Pi 5 16GB + NVMe (virtualization host)

The virt host runs **all** the DMZ + PCN services as containers. Architecture-wise, the Pi is a Linux host running:

- Docker daemon
- ContainerLab orchestrator
- Two Linux bridges (`dmz-br0`, `pcn-br0`)
- 8-12 containers, depending on phase (V1: ~6, V2: ~10, V3: ~12)
- Tailscale on the host (advertises both subnets to the tailnet)
- The physical USB NIC bridges `pcn-br0` to the physical control segment (V2+)

### `l1-plc-01` — Pi 5 + Freenove HAT + Phase 2 hardware (physical PLC)

Real OpenPLC, real GPIO, real wires. This is where the **physical curriculum** lives:
- Pushbutton input (uxcell 12 mm momentary)
- AD16 dual-color indicator (24 V)
- LED strip (12 V)
- Future: real Velocio Ace 1600 wired in

When V2 macvlan-integrates this Pi onto `pcn-br0`, it shows up alongside the virtual PLCs at `10.20.30.47` — a real PLC sitting next to virtual PLCs, both speaking the same Modbus traffic.

### `l1-hp-01` — Pi 3 B+ (physical Conpot)

Unchanged. Conpot fabric continues running as docker-compose on the Pi 3. Joins `pcn-br0` (or its own deception-segment bridge) in V2.

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
integration via USB NIC bridge-port'd into `pcn-br0`.

**V2.y (shipped):** `l3-mon-01` is now the gateway, L3 manager,
firewall, and DHCP server for both internal networks. Two new
containers — `dhcp-dmz` (.2 on DMZ) and `dhcp-pcn` (.2 on PCN) —
hand out leases from `.150-.199` and `.200-.250` respectively. The
firewall container also runs dnsmasq as a DNS forwarder bound to
`192.168.75.1` + `10.20.30.1`. Host's `eth0` is bridge-port'd into
`dmz-br0`, extending the DMZ to the physical wire (Netgear switch
+ GL-AR150 WAN gateway). See [`docs/network-topology.md`](network-topology.md).

**V2.z (next):**
- Authentik (IdP/SSO) added to the topology (authentik-server + authentik-worker + postgres + redis containers)
- Ignition Gateway (Maker edition, free) — full SCADA on the DMZ
- Apache Guacamole — clientless RDP/SSH/VNC gateway
- Suricata in IPS mode for protocol-aware FC5/6 blocking
- Conpot personas re-deployed on `l1-hp-01` with a macvlan that
  puts them on the same physical segment as eth1's switch

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
