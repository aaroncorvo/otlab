# Classroom Installer & Reset

How to bring up a **20-student + 1-teacher** classroom from scratch,
and how to reset it between lab steps or after class. Pi-side install
is fully scripted; the upstream router (MikroTik) is a one-time paste
from a generated config.

> **Repo source of truth**: [`docs/classroom-installer.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-installer.md)

## Roles

| Role | Count | What it runs | Hostname |
|---|---|---|---|
| **Teacher** | 1 | teacher admin panel + Loki/Grafana SIEM | `otlab-teacher` |
| **Student** | 20 | OTLab fabric + dashboard + Suricata + Promtail | `otlab-student-01` … `otlab-student-20` |
| **Router** | 1 | MikroTik RB5009 — DHCP, L3 routing, ACLs | n/a |
| **Switch** | 1 | Cisco Catalyst 2960 24-port — L2 only | n/a |

## Per-student address plan

Every student gets a **unique /24 in each fabric layer** so the SIEM can identify which student an alert came from by source IP.

| Layer | Pattern | Student 1 | Student 5 | Student 20 |
|---|---|---|---|---|
| DMZ | `10.75.N.0/24` | 10.75.1.0/24 | 10.75.5.0/24 | 10.75.20.0/24 |
| PCN | `10.30.N.0/24` | 10.30.1.0/24 | 10.30.5.0/24 | 10.30.20.0/24 |
| ENT (V4.1) | `10.50.N.0/24` | 10.50.1.0/24 | 10.50.5.0/24 | 10.50.20.0/24 |
| Classroom IP | `192.168.10.{100+N}` | .101 | .105 | .120 |

## `/etc/otlab/student.env` — canonical per-Pi config

Written by `otlab-install.sh`. Downstream scripts read it.

```
ROLE=student
STUDENT_ID=5
STUDENT_HOSTNAME=otlab-student-05
CLASSROOM_IP=192.168.10.105
TEACHER_IP=192.168.10.10
DMZ_NET=10.75.5
PCN_NET=10.30.5
ENT_NET=10.50.5
SIEM_LOKI_URL=http://192.168.10.10:3100
```

## Install walkthrough

### Pre-flight

1. Image all 21 Pis (Pi OS Lite Bookworm 64-bit; user `otadmin`/`P@ssw0rd!`; hostnames `otlab-teacher` + `otlab-student-01`…`20`)
2. Connect MikroTik + Cisco + all Pis on the classroom segment
3. Paste `reference/router-configs/mikrotik/20-student-classroom.rsc` into MikroTik (capture MACs first)
4. Paste `reference/router-configs/cisco/24-port-classroom.ios` into Cisco
5. Verify each Pi gets its assigned `.101`–`.120` lease

### Per-Pi install

From your laptop:

```
./scripts/otlab-install.sh otadmin@otlab-student-05.local
```

Script prompts:

```
==> OTLab classroom installer
What role is this Pi?
  1) teacher
  2) student
> 2
Which student number? (1-20)
> 5

Confirm:
  Role:         student
  Hostname:     otlab-student-05
  Classroom IP: 192.168.10.105
  DMZ fabric:   10.75.5.0/24
  PCN fabric:   10.30.5.0/24
  SIEM target:  http://192.168.10.10:3100
Proceed? [y/N] y

==> running student bootstrap chain
    [1/5] bootstrap-pi.sh                ~15 min (OpenPLC compile)
    [2/5] bootstrap-l3-mon-role.sh       ~5 min  (Docker + Suricata)
    [3/5] configure-4port-pi.sh          ~30s    (Cruiser Keel port naming)
    [4/5] install-virtual-lab.sh         ~10 min (build OTLab images)
    [5/5] install-student-promtail.sh    ~1 min  (log shipper → teacher SIEM)
==> all done
```

### Teacher install

```
./scripts/otlab-install.sh otadmin@otlab-teacher.local
```

Role = teacher. Builds teacher panel + SIEM stack. After all students are imaged:

```
./teacher/bootstrap-students.sh --range 192.168.10.101-120
```

to push SSH pubkey + lock down password auth on all students.

## Lab reset — two modes

### `otlab-reset.sh --step` — between lab steps (~30s)

Wipes:
- PLC program state (re-loads canonical `.st`)
- Sensor-sim history
- iptables counters + conntrack table
- Suricata EVE JSON buffer
- Dashboard captures

Keeps:
- `/etc/otlab/student.env` (still student #N)
- Teacher's authorized_keys
- Classroom IP lease
- Promtail position file

```
./scripts/otlab-reset.sh --step otadmin@otlab-student-05.local
```

### `otlab-reset.sh --full` — end of class (~3 min)

Wipes everything above PLUS:
- Destroys + redeploys clab fabric
- Dashboard SQLite
- Teacher's authorized_keys (revoked)
- `/var/log/*` buffers

Keeps:
- `/etc/otlab/student.env`
- Pi OS itself
- otadmin/otuser accounts

```
./scripts/otlab-reset.sh --full otadmin@otlab-student-05.local
```

After `--full`, re-bootstrap from teacher to push the SSH key again.

### Teacher panel reset buttons (planned)

The teacher panel will gain per-Pi "Reset Step" / "Reset Full" buttons that SSH-trigger the reset script. Tracked as a follow-up.

## What changes in existing single-Pi scripts

The classroom rollout layers on top of the existing single-Pi scripts. The bootstrap chain is unchanged — `otlab-install.sh` orchestrates the existing scripts with `/etc/otlab/student.env` providing per-Pi parameterization.

| Script | Change |
|---|---|
| `scripts/bootstrap-pi.sh` | No change |
| `scripts/bootstrap-l3-mon-role.sh` | Reads `/etc/otlab/student.env` and stamps `STUDENT_ID` into `/etc/otlab-bootstrap-info` |
| `scripts/install-virtual-lab.sh` | Renders `otlab.clab.yaml` from `.tmpl` (via `scripts/render-topology.sh`) using `/etc/otlab/student.env` so each student's clab uses unique per-student subnets. Backward-compat: if student.env doesn't exist, falls back to single-Pi defaults |
| `scripts/render-topology.sh` | **New.** Substitutes DMZ/PCN subnet octets in topology yaml. `--check` mode validates without writing |
| `scripts/configure-4port-pi.sh` | **New.** Pins the 4 NICs on a Cruiser Keel to systemd `.link` names: `otlab-mgmt`, `otlab-otext`, `otlab-mirror`, `otlab-spare` |
| `scripts/install-dashboard.sh` | No change |
| `teacher/bootstrap-students.sh` | No change |

## Failure modes & recovery

| Symptom | Cause | Fix |
|---|---|---|
| "Pi got the wrong IP" | MikroTik DHCP reservation MAC mismatch | Re-capture MAC, update router config |
| "/etc/otlab/student.env already exists" | Re-running on installed Pi | `--reinstall` flag or `otlab-reset.sh --full` first |
| Pi installs but missing from teacher panel | Teacher panel `SCAN_BASE` wrong | `-e SCAN_BASE=192.168.10 -e SCAN_START=100 -e SCAN_END=120` |
| Logs missing from Grafana | Promtail can't reach Loki | Check `journalctl -u promtail-otlab` on student |
| Inter-student traffic works | MikroTik ACL not loaded | Re-paste firewall section from `.rsc` |

## Roadmap

Shipped:
- ✅ Per-student address plan
- ✅ Interactive install script (role + student-N)
- ✅ Reset script (--step + --full)
- ✅ Loki + Grafana + Promtail SIEM
- ✅ Promtail student agent
- ✅ MikroTik 20-student classroom config
- ✅ Cisco 2960 classroom switch config
- ✅ Templated clab topology (per-student subnets)
- ✅ Cruiser Keel 4-port NIC naming

Follow-up:
- FortiGate equivalent of MikroTik .rsc
- Per-student firewall NAT/route surgery (activates when `otlab-otext` goes live)
- Teacher panel reset buttons
- Grafana alerting rules over Loki ruler

## See also

- **Classroom Network Map** — network architecture
- **Classroom SIEM** — log aggregation
- **Pre-Event Checklist** — event-day playbook
- [`docs/classroom-installer.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-installer.md) — full repo doc
