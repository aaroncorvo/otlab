# OTLab — First-Event Playbook & Lessons Learned

The actual "go from zero to running a classroom" playbook, written
**after** the first end-to-end smoke test (May 2026). Distilled from
the bugs that surfaced + the architectural decisions we landed on.

This complements but doesn't replace:
- [`docs/classroom-installer.md`](classroom-installer.md) — formal install walkthrough
- [`docs/classroom-network.md`](classroom-network.md) — network architecture
- [`notion-bundle/04-Pre-Event-Checklist.md`](../notion-bundle/04-Pre-Event-Checklist.md) — pre-event checklist

> If you're running an OTLab event for the first time, **start here**.
> If anything in this playbook disagrees with the other docs, this one
> wins — it reflects what we actually did, what broke, and what fixed it.

---

## TL;DR — the architecture we landed on

| Layer | Hardware | OS | Network |
|---|---|---|---|
| Teacher | Exaviz **Cruiser + CM5** (8 PoE switch ports + 2.5GbE WAN + ESP32 wireless) | Debian 13 Trixie | `eth1` (2.5GbE WAN) into the classroom switch |
| Students × N | Exaviz **Cruiser Keel + CM4/CM5** (4 ports) OR standard Pi 5 single-NIC | Debian 13 Trixie | `eth0` into the classroom switch |
| Honeypot | Standard Pi (any) running **Conpot** (Siemens / Schneider / Rockwell personas) | Bookworm or Trixie | `eth0` into the classroom switch |
| Classroom switch | Cisco Catalyst 2960 24-port (managed) OR Netgear unmanaged (8-24 port) | n/a | All Pis + teacher uplink |
| Router / DHCP | TP-Link, MikroTik, Cisco, or anything that serves DHCP on a `/24` | n/a | Provides DHCP + internet uplink (optional) |

Subnet today (this lab): `10.20.30.0/24` via TP-Link DHCP. Per-student internal fabrics on `10.75.N/24` (DMZ) + `10.30.N/24` (PCN), unique per student-N.

---

## What broke in the smoke test — and the fixes

These are real failures we hit. The fixes are **in `main` already**; future operators won't see them. Documenting them so you know *why* certain things are the way they are.

### 1. Suricata pulled from Debian Bookworm stable

**Symptom**: `bootstrap-l3-mon-role.sh` died at `apt-get install ... suricata` with `Package 'suricata' has no installation candidate`.

**Why**: Suricata was pulled from Bookworm stable in 2024 due to a security maintenance gap. It's available in `bookworm-backports` and in Debian 13 Trixie.

**Fix**: `bootstrap-l3-mon-role.sh` now:
1. Tries `apt install suricata` from main first (works on Trixie)
2. On Bookworm fallback, enables `bookworm-backports` and installs from there
3. If both fail, warns + continues (Suricata isn't strictly required; students just lose per-student IDS alerts)

**Better answer**: Re-image old Bookworm Pis with Trixie. We standardize on Trixie going forward. The fallback exists as defense-in-depth.

### 2. `otadmin` had no password — discovery + bootstrap-students both broken

**Symptom**: Teacher panel ping-sweep finds 9 IPs, then SSHs as `otadmin/P@ssw0rd!` — every one fails with `AuthenticationException`. Roster stays empty even though Pis are pingable.

**Why**: `bootstrap-users.sh` originally created `otadmin` with `useradd -m -s /bin/bash ...` and **no password**. The user existed but was locked for password auth (key-auth only). The teacher panel's password auth couldn't get in. Worse: `teacher/bootstrap-students.sh` uses `sshpass` to do its one-shot pubkey push — also broken.

**Fix**: `bootstrap-users.sh` now runs `echo "$u:P@ssw0rd!" | sudo chpasswd` for both `otadmin` and `otuser` after creating them. Password is the lab default initially; `bootstrap-students.sh` disables `PasswordAuthentication` after pushing the panel's pubkey, so the password becomes console-only recovery thereafter.

### 3. SSH host key changed on re-imaged Pis

**Symptom**: SSH refused to connect to `10.20.30.49` (re-imaged rasplc01 → student-01) — "REMOTE HOST IDENTIFICATION HAS CHANGED!"

**Why**: Re-imaging a Pi generates a new host key. The Mac's `~/.ssh/known_hosts` still cached the old key under that IP.

**Fix at smoke-test time**: `ssh-keygen -R 10.20.30.49 && ssh-keygen -R 10.20.30.47` to clear stale entries, then `-o StrictHostKeyChecking=accept-new` to auto-add the new keys.

**Better answer**: After re-imaging, always clear known_hosts entries for the old IP. Or use `-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null` for ephemeral connections.

### 4. iadmin lacked NOPASSWD sudo (Pi Imager default)

**Symptom**: `bootstrap-users.sh` exits with "$PI_HOST does not have passwordless sudo for the existing user."

**Why**: Pi Imager creates the user with normal `sudo` (password required), not `NOPASSWD`. Our automation expects `NOPASSWD` to avoid hanging on password prompts.

**Fix at smoke-test time**: One-shot from your terminal (script can't do this — needs your password):

```bash
ssh iadmin@<pi>
echo "iadmin ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/099_iadmin_nopasswd
sudo chmod 440 /etc/sudoers.d/099_iadmin_nopasswd
exit
```

After that, `bootstrap-users.sh` works.

**Better answer**: In Pi Imager's advanced options, check "Set username" + your `~/.ssh/id_ed25519.pub` for SSH key auth. The user it creates will have NOPASSWD sudo by default. Then this step is unnecessary.

### 5. Tailscale advertised the wrong subnet route

**Symptom**: Mac couldn't reach `10.20.30.0/24` Pis via tailnet — the teacher was advertising `192.168.10.0/24` (a planned-but-unused subnet from earlier docs).

**Fix**: `sudo tailscale set --advertise-routes=10.20.30.0/24` on the teacher, then approve in the admin console.

**Better answer**: When you change the classroom subnet, update the Tailscale advertised routes to match. Use `tailscale set` (not `tailscale up` which can re-prompt for auth).

---

## The real playbook — step by step

### Phase 1: Hardware (you do)

- [ ] **Teacher Pi**: assemble Cruiser + CM5 + NVMe + 48-57V PSU. Plug ethernet from `eth1` (2.5GbE WAN port) into the classroom switch.
- [ ] **Student Pis**: assemble Cruiser Keel + CM4/CM5 + NVMe + 12V PSU. Plug each `eth0` (mgmt port) into the classroom switch.
- [ ] **Honeypot Pi**: standard Pi. Plug `eth0` into the classroom switch.
- [ ] **Classroom switch**: powered on, all ports show link.
- [ ] **Router**: powered on, DHCP serving `10.x.y.0/24` (or your chosen subnet).

### Phase 2: Image (you do, one Pi at a time)

In **Pi Imager** (Mac):
- OS: **Raspberry Pi OS Lite (64-bit) — Trixie** (or newer)
- Storage: the Pi's SD card or NVMe
- Advanced options (gear icon, **before flashing**):
  - **Hostname**: `otlab-teacher`, `otlab-student-01` .. `otlab-student-20`
  - **Username**: `iadmin` (just a temporary imager user)
  - **Password**: `P@ssw0rd!`
  - **SSH**: ✅ enabled
  - **Allow public-key authentication only**: ✅ — paste your `~/.ssh/id_ed25519.pub`
  - **WiFi**: skip (we want wired)
  - **Locale**: your usual
- Flash, insert, power on, give it ~60 sec to boot.

Find the IP via TP-Link admin page (or `arp -a` on the Mac, or `tailscale status` once tailscale enrolled).

### Phase 3: Per-Pi bootstrap (I do this, automated)

**One time per Pi**: from your terminal, give `iadmin` NOPASSWD sudo:

```bash
ssh iadmin@<pi-ip>
echo "iadmin ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/099_iadmin_nopasswd
sudo chmod 440 /etc/sudoers.d/099_iadmin_nopasswd
exit
```

Then from the OTLab repo:

```bash
# Teacher (the Cruiser, only one)
./scripts/bootstrap-users.sh           iadmin@<teacher-ip>
./scripts/bootstrap-teacher-pi.sh      otadmin@<teacher-ip>
./scripts/install-teacher-panel.sh     otadmin@<teacher-ip>   # SCAN_BASE env to match your subnet
./scripts/install-siem.sh              otadmin@<teacher-ip>
./scripts/finalize-teacher-pi.sh       otadmin@<teacher-ip>
./scripts/install-portainer.sh         otadmin@<teacher-ip>   # optional but recommended

# Each student (N = 1 .. 20)
./scripts/bootstrap-users.sh                                  iadmin@<student-ip>
CLASSROOM_SEGMENT=<subnet>/24 \
CLASSROOM_GATEWAY=<router-ip> \
TEACHER_IP=<teacher-ip> \
STUDENT_IP_BASE=<base-octet> \
./scripts/otlab-install.sh --role student --student-id N --yes otadmin@<student-ip>
./teacher/bootstrap-students.sh <student-ip>
```

The orchestration takes ~25 min per student (OpenPLC compile + Docker image builds). Run multiple students in parallel — they don't contend on anything.

### Phase 4: Label & lock the roster (you do, in browser)

- Open **http://<teacher-ip>:8080** (login `otlab` / `P@ssw0rd!`)
- Wait ~60 sec — student cards appear as their installs complete
- Click each card → assign student name
- Click "Lock Roster" once all expected Pis are present
- Open **http://<teacher-ip>:3000** (Grafana, login `admin` / `P@ssw0rd!`) and check the **OTLab → Classroom Overview** dashboard. Logs from each student should be flowing.

### Phase 5: Event day

- Monitor the panel — red cards mean voltage warnings or cable issues
- Watch Grafana — Suricata alert counts per student tell you who's actively probing
- Between lab steps: `./scripts/otlab-reset.sh --step <pi>` for a fast 30-sec reset (keeps teacher access)
- End of class: `./scripts/otlab-reset.sh --full <pi>` for full wipe

---

## Component reference (after smoke test)

### Teacher Pi services (all running today)

| Service | Port | UI Login | Volume |
|---|---|---|---|
| Teacher Admin Panel | 8080 | `otlab` / `P@ssw0rd!` | `classroom-state` |
| Grafana (SIEM dashboards) | 3000 | `admin` / `P@ssw0rd!` | `otlab-siem-grafana-data` |
| Loki (log aggregator) | 3100 | n/a (Grafana proxies) | `otlab-siem-loki-data` |
| Promtail (teacher-side scraper) | 9080 (debug) | n/a | `otlab-siem-promtail-data` |
| Portainer (Docker UI) | 9443 (HTTPS) | self-set first visit | `portainer_data` |

### Per-student services (after install)

| Service | Port | What |
|---|---|---|
| OTLab Dashboard | 8000 | Operator surface (Firewall / DHCP / Live Data tabs) |
| OpenPLC #1 web UI | 8081 | Master PLC ladder logic IDE |
| OpenPLC #2 web UI | 8082 | Outstation PLC |
| Promtail | 9080 (local) | Ships Suricata + dashboard + firewall logs to teacher Loki |
| Suricata IDS | — | Sniffs `pcn-br0`, writes EVE JSON, picked up by Promtail |

---

## Open issues + roadmap

| # | What | Where it bites | Workaround |
|---|---|---|---|
| #23 | Per-student firewall NAT/route surgery | Loki sees the Pi's classroom IP as source, not the real internal subnet IP. SIEM correlation works via `student_id` label instead. | Use Promtail `student_id` label as the source-of-truth correlator. |
| — | ESP32 wireless on Cruiser teacher | Not yet configured as classroom AP — students currently need wired connections | Configure when ≥9 students need wireless. ESP32 supports WPA2/3 STA + AP modes. |
| — | PoE switch ports (poe0–poe7) on teacher | Not yet bridged — currently isolated | Future: bridge poe0–poe7 into a classroom L2 for "collapsed teacher = switch" mode (small classes) |
| — | FortiGate alternative router config | MikroTik `.rsc` shipped; FortiGate CLI equivalent not yet | Use MikroTik for now (works fine) |
| — | Grafana alerting rules over Loki ruler | No automated alerts yet — instructor watches the dashboard manually | Manual watch is fine for one event; alerting comes later |

---

## Troubleshooting (real symptoms we hit, not theoretical)

| Symptom | First check | Then |
|---|---|---|
| **Panel finds 0 hosts** | `curl -fsS -u otlab:P@ssw0rd! http://teacher:8080/api/status` — roster size? | `docker logs otlab-teacher` — look for `[scan] N IPs responded, SSHing…`. If N > 0 but roster=0, the SSH step is failing (auth or no otadmin). Verify `otadmin` exists on the Pi + has `P@ssw0rd!`. |
| **Panel finds N IPs but roster=0** | Check `otadmin` password on each: `sshpass -p 'P@ssw0rd!' ssh otadmin@<pi> hostname` | If that fails, `bootstrap-users.sh` either didn't run or its fix for password-setting wasn't applied. Re-run on the affected Pi. |
| **suricata not found during install** | OS version: `cat /etc/os-release` | If Bookworm, `bootstrap-l3-mon-role.sh` should auto-fall-back to backports. Verify it does in the install logs. If it doesn't, re-image with Trixie. |
| **Mac can't reach 10.x.x.x Pis via tailscale** | `ssh otadmin@otlab-teacher 'tailscale debug prefs | grep -A2 AdvertiseRoutes'` | If wrong route advertised: `sudo tailscale set --advertise-routes=<correct-subnet>/24` on teacher, then approve in https://login.tailscale.com/admin/machines |
| **SSH refuses with "REMOTE HOST IDENTIFICATION HAS CHANGED"** | Re-imaged Pi has new host key | `ssh-keygen -R <ip>` to clear stale cache, then reconnect |
| **OpenPLC compile fails on Pi** | Disk space: `df -h /` | Need at least 4 GB free in `/`. If SD card is full, migrate to NVMe (`./scripts/migrate-to-nvme.sh otadmin@<pi>`) |
| **Logs don't appear in Grafana for student-N** | `ssh otadmin@<student-pi> 'sudo systemctl status promtail-otlab'` | If service inactive: `sudo systemctl restart promtail-otlab` + check `/var/lib/promtail/positions.yaml` exists |

---

## See also

- [`docs/classroom-installer.md`](classroom-installer.md) — formal install walkthrough
- [`docs/classroom-network.md`](classroom-network.md) — three-layer network architecture + trust boundaries
- [`teacher/README.md`](../teacher/README.md) — teacher panel API + asymmetric SSH trust model
- [`teacher/siem/README.md`](../teacher/siem/README.md) — Loki + Grafana + Promtail SIEM
- [`notion-bundle/`](../notion-bundle/) — paste-ready markdown bundle for the Notion knowledge base
