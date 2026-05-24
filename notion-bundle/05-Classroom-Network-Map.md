# Classroom Network Map

How the lab sits on the wire when you run it as a 20-student + 1-teacher classroom. Three layers, no shared IPs.

> **Repo source of truth**: [`docs/classroom-network.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-network.md)

## TL;DR — three layers

```
L3 OPERATOR PLANE     (instructor laptop, tailscale, internet)
        │
L2 CLASSROOM SEGMENT  (one subnet shared by teacher + students)
  .1   gateway / MikroTik router
  .10  instructor laptop / teacher panel host
  .50  Cisco switch mgmt
  .101–.120  student Pis (DHCP reservation by MAC)
        │
L1 LAB FABRIC (per Pi — UNIQUE PER STUDENT for SIEM correlation)
  Student N gets:
    dmz-br0  10.75.N.0/24    (operator surface, dashboard)
    pcn-br0  10.30.N.0/24    (PLCs, sensors, IDS)
    ent-br0  10.50.N.0/24    (planned V4.1)
  Routed (not NAT'd) for traffic to teacher SIEM, so logs show
  real source IPs.
```

The three layers stack but **don't share IPs**:

| Layer | Subnet | Scope | Who's on it |
|---|---|---|---|
| **L1 — Lab fabric** | `10.75.N.0/24` + `10.30.N.0/24` (+ `10.50.N.0/24` V4.1) — unique per student N | Internal to each Pi, but routable from teacher | Containers inside one student's Pi |
| **L2 — Classroom segment** | `192.168.10.0/24` (default) | Across all Pis + teacher | Instructor laptop, student Pis, Cisco, MikroTik |
| **L3 — Operator plane** | Venue WAN + tailscale | Outside the classroom | Instructor's internet uplink, remote ops |

## Layer 2 — Classroom segment

Single L2 broadcast domain on the Cisco's VLAN 10. One DHCP server (MikroTik). Everything in this layer is visible to every other device on it.

### Recommended subnet: `192.168.10.0/24`

| Range | Use | How assigned |
|---|---|---|
| `192.168.10.1` | Gateway (MikroTik) | Static on the MikroTik |
| `192.168.10.10` | Instructor laptop / teacher panel host | DHCP reservation |
| `192.168.10.50` | Cisco switch mgmt | DHCP reservation |
| `192.168.10.101`–`.120` | Student Pis #1–#20 | DHCP reservation by MAC |
| `192.168.10.200`–`.250` | Spillover / guest devices | Dynamic |

### DHCP source

The MikroTik runs DHCP for the classroom segment. The reservations are in `reference/router-configs/mikrotik/20-student-classroom.rsc`. To capture MACs:

```
[admin@MikroTik] > /ip dhcp-server lease print where status=bound
```

Then edit the .rsc to replace placeholder MACs with real ones.

## Layer 1 — Lab fabric (inside each student's Pi)

Each student Pi runs the ContainerLab fabric as Docker containers in the Pi's own network namespace. **Unique per-student subnets** (so the teacher SIEM can identify which student an alert came from by source IP alone).

### Per-student subnet plan

| Layer | Pattern | Student 1 | Student 5 | Student 20 |
|---|---|---|---|---|
| DMZ | `10.75.N.0/24` | 10.75.1.0/24 | 10.75.5.0/24 | 10.75.20.0/24 |
| PCN | `10.30.N.0/24` | 10.30.1.0/24 | 10.30.5.0/24 | 10.30.20.0/24 |
| ENT *(V4.1)* | `10.50.N.0/24` | 10.50.1.0/24 | 10.50.5.0/24 | 10.50.20.0/24 |

Upstream router (MikroTik) holds 60 static routes (3 layers × 20 students), each pointing at the student's classroom-segment IP.

## Layer 3 — Operator plane

What's outside the classroom that the instructor needs:

| Resource | What | Reach |
|---|---|---|
| Venue WAN | Internet uplink (apt, Docker pulls, future Ignition licence) | Via MikroTik WAN port |
| Tailscale tailnet | Optional — remote SSH into student Pis from anywhere | Each Pi advertises its OTLab subnets |
| Instructor's laptop | Where the teacher panel runs from | On the classroom segment + tailscale |

## Trust boundaries

| Source → Destination | Allowed? | How enforced |
|---|---|---|
| Teacher → any student (SSH/22) | ✅ | Teacher's ed25519 pubkey in each student's authorized_keys |
| Teacher → any student lab fabric (e.g. 10.30.5.43) | ✅ | MikroTik static routes |
| Student → teacher (Loki :3100) | ✅ | MikroTik firewall allow rule |
| Student → another student (classroom) | ❌ | MikroTik ACL deny |
| Student → another student (lab fabric) | ❌ | No route + MikroTik deny |
| Student → venue WAN | ✅ | NAT'd at MikroTik |

### What "students hold no tokens" means

- No SSH keys anywhere on the student Pi
- No tailscale auth
- No cloud credentials
- No personal data — fresh Pi OS image, lab-only `otadmin/P@ssw0rd!` (then password disabled after teacher lockdown)

A student walking out with their Pi (or SD card) gets nothing useful.

## Support runbook

### Symptom: a student Pi isn't reachable from the teacher panel

| First check | Then check |
|---|---|
| Pi has power? Activity LED solid? | `ping <student-ip>` from teacher |
| Pi has the right IP? `ip addr` shows `192.168.10.10X`? | MikroTik DHCP reservation MAC matches Pi's MAC? |
| `sshd` running on the Pi? | Re-run `bootstrap-students.sh` for that IP |

### Symptom: logs not appearing in Grafana for a specific student

| First check | Then check |
|---|---|
| `ssh otadmin@<pi> 'sudo systemctl status promtail-otlab'` | Re-run `teacher/agents/install-student-promtail.sh` |
| Loki reachable from student? `curl http://192.168.10.10:3100/ready` | Check MikroTik firewall rule "student → teacher SIEM" |
| Student.env has correct STUDENT_ID? | `cat /etc/otlab/student.env` on the Pi |

### Symptom: inter-student traffic works (shouldn't!)

| Cause | Fix |
|---|---|
| MikroTik ACL deny rules not loaded | `/ip firewall filter print where comment~"otlab"` — should show 7 rules |
| Wrong subnet on the MikroTik | Compare classroom subnet to what students actually got |

## See also

- **Classroom Installer & Reset** — install + reset walkthrough
- **Classroom SIEM** — log aggregation
- **Hardware Kit — Cruiser Keel + Cisco** — hardware spec
- [`docs/classroom-network.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-network.md) — full repo doc with diagrams
