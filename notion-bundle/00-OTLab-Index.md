# OTLab — Notes & Instructions

Operating notes for the [OTLab](https://github.com/aaroncorvo/otlab) ICS / OT training lab.

The full technical documentation lives in the GitHub repo. This Notion space is for the *operational* stuff — event-day checklists, classroom setup notes, instructor quick-references, and decisions that don't belong in version control.

## What lives where

| Topic | Where |
|---|---|
| Code, Dockerfiles, scripts | [GitHub: aaroncorvo/otlab](https://github.com/aaroncorvo/otlab) |
| Full architecture spec | [docs/network-architecture.md](https://github.com/aaroncorvo/otlab/blob/main/docs/network-architecture.md) |
| Setup from scratch | [docs/setup-from-scratch.md](https://github.com/aaroncorvo/otlab/blob/main/docs/setup-from-scratch.md) |
| Classroom rollout | [docs/classroom-installer.md](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-installer.md) |
| Classroom network map | [docs/classroom-network.md](https://github.com/aaroncorvo/otlab/blob/main/docs/classroom-network.md) |
| Single-Pi dashboard tour | [docs/dashboard-tour.md](https://github.com/aaroncorvo/otlab/blob/main/docs/dashboard-tour.md) |
| Teacher panel reference | [teacher/README.md](https://github.com/aaroncorvo/otlab/blob/main/teacher/README.md) |
| SIEM stack (Loki + Grafana) | [teacher/siem/README.md](https://github.com/aaroncorvo/otlab/blob/main/teacher/siem/README.md) |
| MikroTik router config | [reference/router-configs/mikrotik/](https://github.com/aaroncorvo/otlab/tree/main/reference/router-configs/mikrotik) |
| Cisco switch config | [reference/router-configs/cisco/](https://github.com/aaroncorvo/otlab/tree/main/reference/router-configs/cisco) |
| V4 roadmap | [docs/v4-roadmap.md](https://github.com/aaroncorvo/otlab/blob/main/docs/v4-roadmap.md) |
| Event-day notes, instructor brief | **This Notion space** |

## What the lab is

**Single-Pi mode (default)**: the entire DMZ + Process Control fabric runs as containers on one Raspberry Pi 5. Firewall, DHCP, DNS, virtual PLCs, master/outstation poll loop, Suricata IDS, operator dashboard. A student with one Pi gets the whole stack.

**Classroom mode**: 20 students each on their own multi-port carrier Pi (Exaviz Cruiser Keel), plus an instructor running:
- **Teacher Admin Panel** — auto-discovers student Pis, drag-and-drop classroom canvas, health monitoring, optional FortiGate port monitor
- **SIEM stack** (Loki + Grafana + Promtail) — receives logs from every student tagged with `student_id` for per-student correlation
- **Cisco Catalyst 2960** managed switch (classroom L2)
- **MikroTik RB5009** router (DHCP, routing, ACLs)

Each student gets unique per-layer fabric subnets (`10.75.N.0/24` DMZ, `10.30.N.0/24` PCN, `10.50.N.0/24` ENT) so the teacher SIEM can identify which student fired an alert by source IP alone.

## Trust model (classroom mode)

- Teacher panel holds an ed25519 SSH keypair (auto-generated in its persistent volume)
- Each student Pi has the teacher's pubkey in `~otadmin/.ssh/authorized_keys`
- After `bootstrap-students.sh` runs, **PasswordAuthentication is disabled** on every student
- Students hold no SSH keys / tokens / credentials of any kind
- The only credential that opens any student is the teacher's private key
- Upstream router enforces student-to-student deny ACL (defense-in-depth)

## Credentials (lab convention — rotate per event!)

| Where | User | Password |
|---|---|---|
| Pi SSH (iadmin, before bootstrap) | `iadmin` | `P@ssw0rd!` |
| Pi SSH (otadmin, after bootstrap) | `otadmin` | `P@ssw0rd!` |
| Pi SSH (otuser, after bootstrap) | `otuser` | `P@ssw0rd!` |
| OTLab Dashboard (per-student) | `otlab` | `P@ssw0rd!` |
| OpenPLC web UI (per-student) | `openplc` | `P@ssw0rd!` |
| Teacher Admin Panel | `otlab` | `P@ssw0rd!` |
| Grafana SIEM | `admin` | `P@ssw0rd!` (Grafana may force first-login change — set to `P@ssw0rd!@#$`) |
| **Portainer** (Docker UI) | `admin` | `P@ssw0rd!@#$` |
| MikroTik router (if used) | `admin` | `P@ssw0rd!` |
| Cisco switch (if used) | `otlab` | `P@ssw0rd!` |
| Lab WiFi (SSID `MFCTP`) | n/a | `P@ssw0rd!` |

Intentionally public. Lab convention. Don't reuse anywhere outside this lab.

## Repo status

| | |
|---|---|
| GitHub | [github.com/aaroncorvo/otlab](https://github.com/aaroncorvo/otlab) (public) |
| Latest version | V4.0 — classroom rollout (installer + reset + SIEM + router configs + Portainer) |
| Open issues | [#8 unit tests](https://github.com/aaroncorvo/otlab/issues/8) (non-blocking) |
| Roadmap | V4.1 L4 Enterprise zone · V4.2 Authentik+Guacamole · V4.3 VyOS · V4.4 CODESYS |

## First end-to-end smoke test (May 2026)

Full validation on real hardware: Cruiser CM5 teacher + 2 standard Pi 5 students + Conpot honeypot + Netgear unmanaged switch + TP-Link DHCP. Subnet: `10.20.30.0/24`.

**6 bugs found, all fixed in `main`** before this entry was written:

| Commit | Bug |
|---|---|
| `a46b7e2` | Suricata pulled from Debian Bookworm stable — added `bookworm-backports` fallback in `bootstrap-l3-mon-role.sh` |
| `12b521d` | `bootstrap-users.sh` created otadmin/otuser with no password → teacher panel discovery + `bootstrap-students.sh` both broken. Now sets lab default password. |
| `8c66167` | `otlab-install.sh` assumed otadmin/otuser already existed — now auto-runs `bootstrap-users.sh` first when otuser missing |
| `8ed091e` | `install-virtual-lab.sh` bridged eth0 into dmz-br0 (V3 single-Pi behavior) → orphaned classroom students from the mgmt network. Now detects classroom mode and skips physical NIC bridging. |
| `7b42c2f` | Playbook updated with bug #6 + NIC architecture reference per hardware tier |
| `d15cf58` | Credentials table updated (Portainer + per-stage Pi users) |

Full lessons-learned + step-by-step playbook in [`docs/event-playbook.md`](https://github.com/aaroncorvo/otlab/blob/main/docs/event-playbook.md).

## What's actually running today on the smoke-test hardware

| Role | Hardware | IP | Tailnet | Status |
|---|---|---|---|---|
| Teacher | Cruiser + CM5 + NVMe (Trixie) | `10.20.30.27` | `100.77.2.22` | ✅ live |
| Student 1 (was rasplc01) | Pi 5 single-NIC (Trixie, re-imaged) | `10.20.30.49` | (re-enrolled) | ✅ live |
| Student 2 (was l3-mon-01) | Pi 5 single-NIC (Trixie, re-imaged) | `10.20.30.47` | (re-enrolled) | ✅ live |
| Honeypot | Pi running Conpot (Siemens / Schneider / Rockwell personas) | `10.20.30.48` | `100.102.49.51` | ✅ live (decoy) |

All 4 visible in the teacher panel, students shipping Suricata + clab + journal logs to Loki, Portainer Agents installed on students so all student dockers are visible in the teacher's Portainer UI.

## Child pages

8 operational pages live under this one:

1. **Single-Pi Lab — Setup** — end-to-end build of one student kit
2. **Teacher Admin Panel — Setup & Use** — instructor's dashboard
3. **Architecture Reference** — zones, subnets, asset inventory
4. **Pre-Event Checklist** — what to do before / during / after a class
5. **Classroom Network Map** — three-layer network architecture
6. **Classroom Installer & Reset** — per-Pi install + lab reset walkthrough
7. **Classroom SIEM (Loki + Grafana)** — log aggregation + per-student correlation
8. **Hardware Kit — Cruiser Keel + Cisco** — production hardware spec

---

*Last updated by Aaron / Claude — edit freely.*
