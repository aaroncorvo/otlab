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

Form: `<purdue-level>-<role>-<NN>`

| Hostname | Purdue level | Role | Hardware | Status |
|---|---|---|---|---|
| `l1-plc-01` | L1 | Master + sensor-sim outstation + DNP3 outstation (polyfunctional during gap) | Pi 5 | **active** |
| `l1-plc-02` | L1 | Pure outstation (sensor-sim + DNP3) — restores the master/outstation network split | future Pi | **planned (backfill)** |
| `l1-hp-01` | L1 | Conpot honeypot fabric (Siemens / Schneider / Rockwell personas) | Pi 3 B+ | **active** |
| `l3-mon-01` | L3 | Dashboard + Suricata IDS + Apache Guacamole + tailscale subnet router | Pi 5 + NVMe | **active** |
| `l2-hmi-01` | L2 | HMI surface (Ignition Maker / RealPars-style) | future Pi | optional, future |
| `l2-historian-01` | L2 | Process historian (InfluxDB / TimescaleDB) | future Pi | optional, future |
| `l3-eng-01` | L3 | Engineering workstation (full toolchain — Wireshark, OpenPLC editor, etc.) | future laptop | optional, future |

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
| `softplc-1` | `l1-plc-01` | Same physical box (Pi 5). Role unchanged. |
| `softplc-2` | `l3-mon-01` | Same physical box (Pi 5 + NVMe). **Role changed** L1 PLC → L3 monitoring. Services that lived here (sensor-sim, DNP3 outstation) moved to `l1-plc-01`. |
| `honeypot-host` | `l1-hp-01` | Same physical box (Pi 3 B+). Role unchanged. |
| `ops-host` | `l3-mon-01` | This was the planned 4th-Pi name. With softplc-2 repurposed, it is the same box as `l3-mon-01`. The 4th-Pi plan dissolved. |

---

## IPs

The Purdue level is in the **third octet block** of the address plan,
even on the transitional flat segment.

### Current (transitional) — single flat lab segment

```
10.20.30.0/24   "lab" segment (everything is here for now)
  .1            TP-Link gateway (acts as L3.5 perimeter)
  .40-.49       L1 PLCs            (l1-plc-01 = .47, l1-plc-02 = .49 future)
  .48           L1 honeypot host   (l1-hp-01)
  .49           L3 monitoring host (l3-mon-01)  ⚠ transitional — moves to .61 in L3 segment
  .50-.59       Conpot personas    (.50 siemens, .51 schneider, .52 rockwell)
  .100-.199     Operator workstations / ad-hoc / DHCP
```

### Future (after L3 segment break) — managed switch + VLANs

```
10.20.30.0/24   Lab segment (VLAN 10) — L1 only
  .40-.49       L1 PLCs
  .48           L1 honeypot host
  .50-.59       Conpot personas

10.20.40.0/24   Operations segment (VLAN 40) — L3
  .60-.69       L3 monitoring + management
  .70-.79       L3 engineering workstations
```

The renumber happens when the managed switch (port-mirror + VLAN
support) lands. Until then, all hosts share `10.20.30.0/24`.

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
- **Future** — drop `/etc/hosts` aliases once all docs/scripts/dashboards
  are confirmed clean.
