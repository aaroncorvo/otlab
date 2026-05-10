# OTLab Naming Schema

Canonical naming conventions for the Maple Ridge ICS Training Lab. Every
host, IP, service, script, and runtime artifact follows the patterns
codified here. The schema is **Purdue-aware** — the most important
architectural fact about each box (its Purdue model level) is the
first thing you read.

The schema applies retroactively as of commit *(this commit)*. Legacy
names (`softplc-1`, `softplc-2`, `honeypot-host`, `ops-host`) are
preserved as `/etc/hosts` aliases on each Pi for one transition window
(~one DEF CON cycle), then dropped.

---

## Hostnames

Form: `<purdue-level>-<role>-<NN>`. The lab runs in **dual mode**: one Pi virtualizes the core (containerlab), and physical Pis extend it for on-the-wire authenticity.

### Physical hosts

| Hostname | Purdue level | Role | Hardware | Status |
|---|---|---|---|---|
| `l3-mon-01` | L3.5 | **Virtualization host** — runs the containerlab fabric (DMZ + PCN bridges, firewall container, Ignition, Authentik, Guacamole, Suricata, dashboard, virtual OpenPLCs, sensor-sim, DNP3) + tailscale subnet router | Pi 5 16GB + NVMe | **active** |
| `l1-plc-01` | L1 | Physical OpenPLC + Phase 2 hardware (relays, AD16, LED strip, pushbutton) | Pi 5 + Freenove HAT | **active** |
| `l1-hp-01` | L1 | Physical Conpot fabric (Siemens / Schneider / Rockwell personas) | Pi 3 B+ | **active** |

### Virtual nodes (run on `l3-mon-01` as containers)

Form: `<role>-virt[-<NN>]` for virtual instances. Live in containerlab, named with `clab-otlab-` prefix at runtime.

| Logical name | Purdue level | Role | Image |
|---|---|---|---|
| `fw-dmz-pcn` | conduit | DMZ↔PCN firewall (also: DNS forwarder for both zones via dnsmasq) | `otlab/firewall:latest` |
| `dhcp-dmz` | L3.5 | DHCP server for DMZ (`192.168.75.150-.199`); advertises firewall as gateway + DNS | `otlab/dhcp:latest` |
| `dhcp-pcn` | L1/L2 | DHCP server for PCN (`10.20.30.200-.250`); advertises firewall as gateway + DNS | `otlab/dhcp:latest` |
| `dashboard` | L3 | OTLab Dashboard (lab admin + curriculum surface) | `otlab/dashboard:latest` |
| `ignition` | L3 | Ignition SCADA (Maker edition) — V2 | `inductiveautomation/ignition:8.1.x` |
| `guacamole` | L3 | Apache Guacamole (clientless RDP/SSH/VNC) — V2 | `guacamole/guacamole:1.5.x` |
| `authentik` | L3 | IdP/SSO for DMZ + OT services — V2 | `ghcr.io/goauthentik/server:latest` |
| `suricata` | L3 | Network IDS (sniffs pcn-br0) — V2 | `jasonish/suricata:latest` |
| `plc-1-virt` | L1 | Virtual OpenPLC #1 (master role) | `otlab/openplc:latest` |
| `plc-2-virt` | L1 | Virtual OpenPLC #2 (outstation role) | `otlab/openplc:latest` |
| `codesys-plc` | L1 | CODESYS Control SL (vendor PLC runtime) — V3 | `codesys/control-arm64-sl:latest` |
| `codesys-hmi` | L2 | CODESYS Web HMI — V3 | `codesys/web-hmi:latest` |
| `sensor-sim` | L1 | Modbus TCP outstation, scenario-driven | `otlab/sensor-sim:latest` |
| `dnp3-outstation` | L1 | DNP3 outstation, scenario-driven | `otlab/dnp3-outstation:latest` |

### Future / optional

| Hostname | Purdue level | Role | Status |
|---|---|---|---|
| `l3-eng-01` | L3 | Engineering workstation (full toolchain — Wireshark, OpenPLC editor) | optional, ad-hoc laptop |
| `l4-corp-01` | L4 | Corp IT emulation (Windows VM with AD, file shares, mock email) | optional, future |

### Why level-first

Every time a student types `ssh l3-mon-01`, the Purdue level reinforces
itself. It also matches what big-vendor OT platforms (Dragos, Claroty
asset views) tend to use in their training builds.

### Why zero-padded indexes

`-01` not `-1` so we sort lexically forever. If you eventually have ten
PLCs, `l1-plc-09` and `l1-plc-10` sort cleanly.

### What about the site prefix?

Skipped for a single-site lab. If you ever ship a sister lab, the
form becomes `mr-l1-plc-01` ("mr" for Maple Ridge). Don't pre-emptively
add it.

### Legacy → canonical map

| Legacy | Canonical | Notes |
|---|---|---|
| `softplc-1` | `l1-plc-01` | Same physical box (Pi 5). Physical OpenPLC + Phase 2 hardware. |
| `softplc-2` | `l3-mon-01` | Same physical box (Pi 5 16GB + NVMe). **Role changed** L1 PLC → L3.5 virtualization host. Originally hosted sensor-sim + DNP3; those moved to virtual containers (or to `l1-plc-01` during the V0 gap). |
| `honeypot-host` | `l1-hp-01` | Same physical box (Pi 3 B+). Role unchanged. |
| `ops-host` | `l3-mon-01` | This was the planned 4th-Pi name. With softplc-2 repurposed AND the lab going containerized, it is the same box as `l3-mon-01`. The 4th-Pi plan dissolved. |
| `l1-plc-02` (planned) | (dropped) | Planned outstation backfill. **Obsolete** — virtual OpenPLC #2 (`plc-2-virt`) covers this role. |

---

## IPs

The Purdue level is in the **third octet block** of the address plan,
even on the transitional flat segment.

The lab runs **two routed segments**, bridged inside `l3-mon-01`'s
container network namespace. The bridge between them is a firewall
container (the conduit). No managed switch needed — the Pi IS the
segment break.

### `192.168.75.0/24` — Lab DMZ (Level 3.5)

Operations zone. Where SCADA, IdP, jump-host, and the dashboard live.

```
192.168.75.0/24  dmz-br0  (also bridge-port'd to host eth0 — DMZ extends to physical wire)
  .1     fw-dmz-pcn          firewall, default gateway for DMZ + DNS forwarder (dnsmasq)
  .2     dhcp-dmz            DHCP server (dnsmasq, DHCP-only mode)
  .10    authentik-server    (V2)
  .11    authentik-postgres  (V2)
  .12    authentik-redis     (V2)
  .20    ignition            (V2)
  .30    guacamole           (V2)
  .40    dashboard           (V1 — primary entry point)
  .150 -- .199                DHCP scope (dynamic leases for new clients)
```

### `10.20.30.0/24` — Process Control Network (Levels 1 + 2)

Where virtual + physical PLCs, sensor-sim, DNP3 outstation, and Conpot
personas all live. After V2 macvlan integration, the host's USB NIC
(eth1) is bridged into pcn-br0, so virtual containers and physical Pis
share one broadcast domain.

```
10.20.30.0/24  pcn-br0  (virtual containers + physical Pis bridged via eth1 USB NIC)
  .1     fw-dmz-pcn          firewall (gateway) + DNS forwarder (dnsmasq)
  .2     dhcp-pcn            DHCP server (dnsmasq, DHCP-only mode)
  .43    modbus-master       virtual Python master (polls .70 every 100ms)  [V2.x: was .50, moved to avoid Conpot conflict]
  .47    l1-plc-01           physical Pi 5 (OpenPLC :502, :8080) + Phase 2 hardware
  .48    l1-hp-01            physical Pi 3 B+ (Conpot Docker host)
  .50    Conpot Siemens-PS4  physical, on l1-hp-01 macvlan
  .51    Conpot Schneider-M340  same
  .52    Conpot Rockwell-CHEM   same
  .60    plc-1-virt          virtual OpenPLC #1 (web UI on host :8081)
  .61    plc-2-virt          virtual OpenPLC #2 (web UI on host :8082)
  .70    sensor-sim          virtual Modbus :5020 + ctrl :5021
  .71    dnp3-outstation     virtual DNP3 :20000
  .80    codesys-plc         (V3 — planned)
  .81    codesys-hmi         (V3 — planned)
  .200 -- .250                DHCP scope (dynamic leases for new clients)
```

### DHCP reservations (PCN)

Static reservations hold known devices on their canonical IPs even if
their device-side static config gets clobbered or they boot clean from
an SD card. Configured in `virtual/topologies/otlab.clab.yaml` on the
`dhcp-pcn` node via the `DHCP_HOSTS` env var; rendered into dnsmasq's
`dhcp-host=` directive at container start.

| Device | IP | MAC | Notes |
|---|---|---|---|
| `l1-plc-01` | `10.20.30.47` | `2c:cf:67:4f:d3:09` | Pi 5 onboard NIC (Pegatron OUI) |
| `l1-hp-01`  | `10.20.30.48` | `b8:27:eb:78:85:77` | Pi 3 B+ onboard NIC (RPi Foundation OUI) |
| `siemens-PS4`    | `10.20.30.50` | `02:42:0a:14:1e:32` | Conpot persona on `l1-hp-01` (Docker IP-derived MAC) |
| `schneider-M340` | `10.20.30.51` | `02:42:0a:14:1e:33` | Conpot persona on `l1-hp-01` |
| `rockwell-CHEM`  | `10.20.30.52` | `02:42:0a:14:1e:34` | Conpot persona on `l1-hp-01` |

Adding a new reservation:
1. Get the device's MAC: from inside the firewall container,
   `ip neigh show <ip>` after the device has been on the wire briefly.
2. Add a line to `DHCP_HOSTS` in the topology under the right zone's
   DHCP node.
3. Update the table here with `device | ip | mac | notes`.
4. `containerlab deploy --reconfigure` (or destroy/deploy) — no image
   rebuild needed; the DHCP container reads `DHCP_HOSTS` at start.

**V2 macvlan path** (virtual → physical):
1. Virtual container's eth1 sends ARP for the physical IP
2. ARP broadcast → pcn-br0 → out via eth1 USB NIC → physical lab switch
3. Physical Pi replies; reply traverses back via the same path
4. Subsequent unicast TCP/UDP flows directly through the bridge

Suricata sniffs `pcn-br0` and sees ALL traffic — virtual-only, physical-only,
and cross-segment (which is the highest-value attack surface).

### `172.20.20.0/24` — ContainerLab management

Internal control plane for containerlab. Image pulls + clab metadata.
Not user-visible.

### Tailscale tailnet (`100.64.0.0/10`)

`l3-mon-01` advertises both `192.168.75.0/24` AND `10.20.30.0/24`
to the tailnet, so operators on tailscale can reach either segment.

### Operator management WiFi

Whatever WiFi the operator brings the lab up on (`wlan0` on `l3-mon-01`).
Used for SSH from the laptop and `apt`/image-pull access. Subnet varies.

---

## Service / systemd unit names

Prefix every otlab-shipped systemd unit with `otlab-` so it's visible
at a glance against vendor units (`openplc.service`, `suricata.service`,
`tailscaled.service`).

| Unit | Runs on | Purpose |
|---|---|---|
| `otlab-sensor-sim.service` | l1-plc-01 (l1-plc-02 future) | Modbus TCP outstation on :5020 |
| `otlab-dnp3-outstation.service` | l1-plc-01 (l1-plc-02 future) | DNP3 outstation on :20000 |
| `otlab-master.service` | l1-plc-01 | OpenPLC master polling loop (FUTURE — currently inside OpenPLC runtime) |
| `otlab-dashboard.service` | l3-mon-01 | Flask + HTTPS on :8000 |
| `otlab-honeypot.service` | l1-hp-01 | Conpot fabric (currently `compose@conpot` — will rename) |
| `suricata.service` | l3-mon-01 | (vendor unit — left as-is) |

Path on disk: `/etc/systemd/system/otlab-*.service`.

The pre-existing `sensor-sim.service` / `dnp3-outstation.service` /
`otlab-dashboard.service` units are renamed in place by the install
scripts; existing deployments will pick up the new names on next
`install-*.sh` run.

---

## Script names

| Script | Purpose | Replaces |
|---|---|---|
| `bootstrap-users.sh` | Creates `otadmin` (sudo) and `otuser` (runtime) on any Pi | — |
| `bootstrap-pi.sh` | Generic Pi-OS hardening + lab venv (any role) | — |
| `bootstrap-l1-plc-role.sh` | Configures a Pi as an L1 PLC | `bootstrap-openplc-role.sh` |
| `bootstrap-l1-hp-role.sh` | Configures a Pi as an L1 honeypot | `bootstrap-honeypot.sh` |
| `bootstrap-l3-mon-role.sh` | Configures a Pi as the L3 monitoring host | `bootstrap-ops-host.sh` |
| `install-sensor-sim.sh` | Pushes sensor-sim service to a designated L1 PLC | — |
| `install-dnp3.sh` | Pushes DNP3 outstation service to a designated L1 PLC | — |
| `install-dashboard.sh` | Pushes the dashboard to `l3-mon-01` | — |
| `install-suricata.sh` | Configures Suricata IDS on `l3-mon-01` | — |
| `install-guacamole.sh` | Deploys Apache Guacamole on `l3-mon-01` | — |
| `wipe-plc-role.sh` | Destructive — strips OpenPLC + lab services from a Pi (used to reclaim a host for a different role) | (new) |

---

## User accounts

Two accounts on every Pi:

- **`otadmin`** — `sudo` group, NOPASSWD where bootstrap scripts grant
  it. The account human operators SSH in as.
- **`otuser`** — non-privileged runtime user. Owns `/home/otuser/lab/`
  and all otlab-* services run as this user.

Lab convention is intentionally-public passwords (`P@ssw0rd!`) on a
DEF CON booth lab — *do not generalize this to non-lab environments*.
Rotate per event.

---

## File / directory layout on each Pi

```
/home/otuser/lab/
├── .venv-modern/              # py3.13 venv with pymodbus, flask, etc.
├── sensor-sim.py              # if l1-plc-NN
├── dnp3-outstation.py         # if l1-plc-NN
├── dashboard/                 # if l3-mon-NN
├── scenarios/                 # JSON scenario configs (active set on this host)
├── tests/                     # test-*.py / test-*.sh
└── captures/                  # pcaps from dashboard-initiated captures (l3-mon only)

/etc/systemd/system/
├── otlab-sensor-sim.service
├── otlab-dnp3-outstation.service
└── otlab-dashboard.service    # (only on l3-mon)

/etc/otlab-bootstrap-info       # ts + commit + last script that ran
```

---

## Decision log

- **2026-05-09** — schema introduced. Repurposed `softplc-2` (Pi 5 + NVMe)
  from L1 PLC role to L3 monitoring role and renamed it `l3-mon-01`.
  Services from softplc-2 (sensor-sim, DNP3 outstation) collapsed onto
  `softplc-1` (renamed `l1-plc-01`). Added `l1-plc-02` to the
  planned-backfill list. Old hostnames preserved as `/etc/hosts` aliases
  for one transition window.
- **2026-05-10** — V2.y: `l3-mon-01` is now the gateway, L3 manager,
  firewall, AND DHCP server for both internal networks. Added `dhcp-dmz`
  (.2 on DMZ, scope `.150-.199`) and `dhcp-pcn` (.2 on PCN, scope
  `.200-.250`) container nodes. Firewall container now also runs
  dnsmasq as a DNS forwarder bound to `192.168.75.1` + `10.20.30.1`.
  Host's `eth0` is bridge-port'd into `dmz-br0` so the DMZ extends out
  to the physical wire (Netgear switch + GL-AR150 WAN gateway). See
  `docs/network-topology.md`.
- **Future** — drop `/etc/hosts` aliases once all docs/scripts/dashboards
  are confirmed clean.
