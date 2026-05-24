# OTLab — Classroom Installer & Lab-Reset Walkthrough

How to bring up a **20-student + 1-teacher** classroom from scratch,
and how to reset it between lab steps or after class. Pi-side install
is fully scripted; the upstream router (MikroTik or FortiGate) is a
one-time paste from a generated config.

> **Sibling docs**:
> - [`classroom-network.md`](classroom-network.md) — the network map (subnets, trust, hardware kit)
> - [`network-architecture.md`](network-architecture.md) — what's inside one Pi
> - [`teacher/README.md`](../teacher/README.md) — teacher panel reference
> - [`teacher/siem/README.md`](../teacher/siem/README.md) — SIEM stack (Loki + Grafana + Promtail)

---

## Roles in a classroom rollout

| Role | Count | What it runs | Hostname pattern |
|---|---|---|---|
| **Teacher** | 1 | teacher admin panel + Loki/Grafana SIEM + MikroTik admin (web/SSH) | `otlab-teacher` |
| **Student** | 20 | OTLab fabric (clab) + dashboard + Suricata + Promtail log shipper | `otlab-student-01` … `otlab-student-20` |
| **Router** | 1 | MikroTik (default) or FortiGate — DHCP, L3 routing, inter-student ACL | n/a (managed via its own UI) |

---

## Per-student address plan (the key design decision)

Every student gets a **unique /24 in each fabric layer**, so the teacher
SIEM can identify which student an alert came from by source IP alone
(no metadata required).

| Layer | Pattern | Student 1 | Student 5 | Student 20 |
|---|---|---|---|---|
| DMZ | `10.75.N.0/24` | 10.75.1.0/24 | 10.75.5.0/24 | 10.75.20.0/24 |
| PCN | `10.30.N.0/24` | 10.30.1.0/24 | 10.30.5.0/24 | 10.30.20.0/24 |
| ENT *(V4.1)* | `10.50.N.0/24` | 10.50.1.0/24 | 10.50.5.0/24 | 10.50.20.0/24 |
| Classroom IP | `192.168.10.{100+N}` | .101 | .105 | .120 |

Worked example — student #5:
- Classroom IP (eth0): `192.168.10.105`
- DMZ fabric:   `10.75.5.0/24` (firewall .1, dashboard .40)
- PCN fabric:   `10.30.5.0/24` (firewall .1, PLC .43, sensor-sim .70)
- ENT fabric *(V4.1)*: `10.50.5.0/24`

The upstream router holds:
- **20 DHCP reservations** mapping each Pi's MAC → `.101`–`.120`
- **60 static routes** (3 layers × 20 students) pointing each fabric subnet at that student's classroom IP
- **1 ACL group** denying `10.75.0.0/16 ↔ 10.30.0.0/16 ↔ 10.50.0.0/16` student-to-student crossover

The teacher box (`.10`) gets bidirectional reach to every student's fabric, no NAT in the middle, so the SIEM logs show real source IPs.

---

## `/etc/otlab/student.env` — the canonical per-Pi config

Every Pi gets a small env file written by `otlab-install.sh`. Downstream
scripts read it to render configs and identify themselves.

```sh
# /etc/otlab/student.env — written by scripts/otlab-install.sh
ROLE=student              # teacher | student
STUDENT_ID=5              # 1..20  (unset on teacher)
STUDENT_HOSTNAME=otlab-student-05
CLASSROOM_SEGMENT=192.168.10.0/24
CLASSROOM_IP=192.168.10.105
TEACHER_IP=192.168.10.10
DMZ_NET=10.75.5           # third-octet base; full subnet = ${DMZ_NET}.0/24
PCN_NET=10.30.5
ENT_NET=10.50.5
SIEM_LOKI_URL=http://192.168.10.10:3100
SIEM_PROMTAIL_PORT=9080   # local promtail listener
LAB_VERSION=v4.0
INSTALLED_AT=2026-05-23T18:00:00Z
INSTALLED_BY=otlab-install.sh
```

Teacher Pi gets `ROLE=teacher` and omits `STUDENT_ID`, `DMZ_NET`,
`PCN_NET`, `ENT_NET`.

---

## Install walkthrough

### Pre-flight (one-time per cohort)

1. Image all 21 Pis with Pi OS Lite Bookworm 64-bit (Pi Imager → Advanced: user `otadmin` / pass `P@ssw0rd!`, SSH on, set hostnames `otlab-teacher`, `otlab-student-01` … `otlab-student-20`)
2. Connect MikroTik/FortiGate, switch, all Pis on the classroom segment
3. Paste `reference/router-configs/mikrotik/20-student-classroom.rsc` into the MikroTik (or the FortiGate equivalent) — see [router config README](../reference/router-configs/mikrotik/README.md) for how to capture MAC addresses first
4. Confirm each Pi gets its assigned `.101`–`.120` lease (router's DHCP-Server → Leases page)

### Per-Pi install (interactive walk-through)

From your laptop, run the orchestrator against each Pi:

```sh
./scripts/otlab-install.sh otadmin@otlab-teacher.local
# or
./scripts/otlab-install.sh otadmin@otlab-student-05.local
```

The script prompts:

```
==> OTLab classroom installer
    Target: otadmin@otlab-student-05.local
    Reachability check ... ok

What role is this Pi?
  1) teacher
  2) student
> 2

Which student number? (1-20)
> 5

Confirm:
  Role:         student
  Hostname:     otlab-student-05
  Classroom IP: 192.168.10.105   (must match router DHCP reservation)
  DMZ fabric:   10.75.5.0/24
  PCN fabric:   10.30.5.0/24
  ENT fabric:   10.50.5.0/24 (V4.1, not deployed yet)
  SIEM target:  http://192.168.10.10:3100
Proceed? [y/N] y

==> writing /etc/otlab/student.env
==> setting hostname → otlab-student-05
==> running bootstrap chain
    [1/4] bootstrap-pi.sh                (~15 min, OpenPLC compile)
    [2/4] bootstrap-l3-mon-role.sh       (~5 min, Docker + Suricata)
    [3/4] install-virtual-lab.sh         (~10 min, build 7 OTLab images)
    [4/4] install-student-promtail.sh    (~1 min, log shipper)
==> all done
    Pi will appear in the teacher panel within 30s
    Logs will appear in Grafana within 1 min
```

### Teacher install

```sh
./scripts/otlab-install.sh otadmin@otlab-teacher.local
```

Role choice = teacher. The chain runs:
- Writes `/etc/otlab/student.env` with `ROLE=teacher`
- Builds + starts the teacher panel container (`teacher/`)
- Builds + starts the SIEM stack (`teacher/siem/docker-compose.yml`)
- After all student Pis are imaged: re-run `teacher/bootstrap-students.sh --range 192.168.10.101-120` to push the teacher's SSH pubkey + lock down student password auth

---

## Lab reset

Two modes. Both run on the student Pi.

### `otlab-reset.sh --step` — between lab steps

Use when transitioning from one exercise to the next. Wipes:
- All PLC program state (re-loads the canonical `.st` from `plc/`)
- Sensor-sim history (resets to baseline scenario)
- iptables counters + conntrack table
- Suricata EVE JSON buffer
- Dashboard captures dir

Keeps:
- `/etc/otlab/student.env` (so the Pi is still student #5)
- Teacher's authorized_keys (so the teacher panel keeps working)
- The classroom IP lease
- Promtail position file (so log shipping resumes cleanly)

Time: ~30 seconds.

```sh
./scripts/otlab-reset.sh --step otadmin@otlab-student-05.local
```

### `otlab-reset.sh --full` — end of class

Use when retiring a classroom. Wipes everything above, plus:
- `containerlab destroy` on the fabric, then redeploy from clean config
- Dashboard SQLite (audit log, settings)
- Teacher panel pubkey (re-locks the Pi as if newly imaged for student #5)
- All `/var/log/*` cleared

Keeps:
- `/etc/otlab/student.env` (the Pi is still student #5 — IP plan stays)
- Pi OS itself (no re-image needed)

Time: ~3 minutes.

```sh
./scripts/otlab-reset.sh --full otadmin@otlab-student-05.local
```

### Teacher-panel button (planned)

The teacher panel will gain a per-Pi "Reset Step" / "Reset Full" button
that SSH-triggers the reset script against the selected Pi using the
teacher's key. UX target: instructor clicks the button on a Pi card and
the card flips to "resetting…" then "ready" within 30s (step) or 3min
(full).

Tracked as a follow-up.

---

## What changes in the existing scripts

The classroom rollout layers on top of the existing single-Pi scripts.
The bootstrap chain is unchanged — `otlab-install.sh` orchestrates the
existing scripts with `/etc/otlab/student.env` providing per-Pi
parameterization.

| Script | Change |
|---|---|
| `scripts/bootstrap-pi.sh` | No change. |
| `scripts/bootstrap-l3-mon-role.sh` | Reads `/etc/otlab/student.env` and stamps `STUDENT_ID` into `/etc/otlab-bootstrap-info`. |
| `scripts/install-virtual-lab.sh` | Renders `virtual/topologies/otlab.clab.yaml` from `.j2` template using `/etc/otlab/student.env` so each student's clab uses unique subnets. **TODO — separate PR.** Until then, all students run identical subnets; teacher SIEM can still distinguish them by their classroom-segment source IP. |
| `scripts/install-dashboard.sh` | No change. |
| `teacher/bootstrap-students.sh` | No change. |

---

## Failure modes & recovery

| Symptom | Cause | Fix |
|---|---|---|
| Install script "Pi got the wrong IP" | Router DHCP reservation MAC doesn't match the Pi | Re-capture MAC from `arp -a`, update router config |
| Install reports "/etc/otlab/student.env already exists" | Re-running on an installed Pi | Pass `--reinstall` to overwrite; or run `otlab-reset.sh --full` first |
| Pi installs but doesn't appear in teacher panel | Teacher panel's `SCAN_BASE` / range doesn't include the classroom subnet | `docker run -e SCAN_BASE=192.168.10 -e SCAN_START=100 -e SCAN_END=120 ...` |
| Logs don't appear in Grafana | Promtail can't reach Loki at `http://192.168.10.10:3100` | Check `journalctl -u promtail-otlab` on the student; verify teacher SIEM container is running |
| Inter-student traffic still works | Router ACL not loaded | Re-paste the `/ip firewall filter` block from the MikroTik .rsc |
| `otlab-reset.sh --step` reports "containerlab not found" | Pi wasn't fully installed | Run the full install first |

---

## Roadmap

The vertical slice in this commit covers:
- Per-student address plan (documented, used by install + router)
- Interactive install script (asks role + student-N, writes student.env)
- Reset script (--step + --full)
- Loki + Grafana + Promtail SIEM (teacher-side compose stack)
- Promtail student agent (ships Suricata EVE + dashboard logs)
- MikroTik 20-student classroom config (.rsc paste)

Follow-up PRs:
- Templated `virtual/topologies/otlab.clab.yaml.j2` so each student's fabric actually uses unique subnets (currently all students run identical internal subnets — works because they're network namespaces, but the SIEM correlation by IP is coarser)
- FortiGate equivalent of the MikroTik .rsc
- Per-student firewall NAT/route surgery (so teacher SIEM sees real internal source IPs, not the Pi's NAT'd outside address)
- Teacher panel "Reset Step" / "Reset Full" buttons
- Grafana alerting rules (per-student Suricata signature trigger thresholds)
