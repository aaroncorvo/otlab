# Pre-Event Checklist

What to do **before**, **during**, and **after** a classroom event. Pin
this to your event-day notes app — it's the operational playbook.

## T-7 days — kit prep

- [ ] **Count Pis** — confirm you have 21 student-ready Cruiser Keel kits (20 students + 1 spare)
- [ ] **Inventory cables** — 40+ Cat6 patch cables, all working (test if older than 1 event)
- [ ] **Power strips** — 4× 6-outlet strips
- [ ] **Switch + router** — Cisco 2960 24-port + MikroTik RB5009 boxed with their PSUs
- [ ] **Reimage all 21 Pis** (Pi Imager, Pi OS Lite Bookworm 64-bit, hostnames `otlab-teacher` + `otlab-student-01..20`, user `otadmin`/`P@ssw0rd!`, SSH on)
- [ ] **Capture all 21 MACs** — power on, plug into the Cisco one-by-one with hostnames visible, write down MAC↔hostname
- [ ] **Update MikroTik `.rsc`** — replace the 21 placeholder MAC addresses in `reference/router-configs/mikrotik/20-student-classroom.rsc` with the real ones
- [ ] **Smoke-test on 2 Pis** — run the full install + verify per `teacher/TESTING.md`

## T-2 days — bench rehearsal

- [ ] **Connect the kit on your bench**: MikroTik (uplinks to your home internet), Cisco 24-port (uplinks to MikroTik), 2 Pis plugged in
- [ ] **Paste the MikroTik `.rsc`** — verify 2 leases bind correctly to the right IPs
- [ ] **Paste the Cisco `.ios`** — verify the 2 student ports + teacher port + uplink trunk all show "connected"
- [ ] **Install the teacher panel container** on a laptop:
  ```bash
  docker run -d --name otlab-teacher -p 8080:8080 \
    -e SCAN_BASE=192.168.10 -e SCAN_START=100 -e SCAN_END=120 \
    -v classroom-state:/var/lib/teacher otlab-teacher
  ```
- [ ] **Install the SIEM stack**:
  ```bash
  cd teacher/siem && docker compose up -d
  ```
- [ ] **Run `otlab-install.sh` against the 2 bench Pis** (one as `--role student --student-id 1`, one as `--role student --student-id 2`)
- [ ] **Verify teacher panel sees both Pis** within 30s
- [ ] **Verify Grafana shows logs from both Pis** within 1 min — open OTLab Classroom Overview dashboard
- [ ] **Run `bootstrap-students.sh`** to push the SSH key + lock down password auth
- [ ] **Negative test**: from your laptop (not the teacher), `sshpass -p 'P@ssw0rd!' ssh otadmin@<student-ip>` should fail
- [ ] **Reset test**: `otlab-reset.sh --step otadmin@otlab-student-01.local` succeeds, takes ~30s, Pi still online
- [ ] **Full reset test**: `otlab-reset.sh --full otadmin@otlab-student-01.local` succeeds, takes ~3min, then re-bootstrap from teacher

## T-0 (event day) — setup

### 90 min before students arrive

- [ ] Pull the kit out of the case, lay it out on the desk
- [ ] **Wire the MikroTik** — WAN to venue uplink, LAN to Cisco port 24 trunk
- [ ] **Power on MikroTik** — verify it gets a WAN IP, default gateway works (ping 1.1.1.1)
- [ ] **Wire the Cisco** — power on, verify all 24 ports show "no link" except port 24 (trunk)
- [ ] **Power on the teacher Pi** — connects to Cisco port 21 → should get `192.168.10.10` lease
- [ ] **Start the teacher panel container** + verify it's accessible at `http://<teacher-ip>:8080`
- [ ] **Start the SIEM stack** + verify Grafana at `http://<teacher-ip>:3000`
- [ ] **Pre-stage student Pis** — power up each, plug into the correct Cisco port (Pi N → Gi1/0/N)

### 30 min before students arrive

- [ ] Run `otlab-install.sh otadmin@otlab-student-NN.local` for each Pi, confirming role + student # at each prompt
- [ ] Watch the teacher panel — verify all 20 Pis appear
- [ ] Open Grafana — verify all 20 students are shipping logs
- [ ] Run `teacher/bootstrap-students.sh --range 192.168.10.101-120` — push key + lock down

### 15 min before students arrive

- [ ] **Lock the roster** in the teacher panel (click Lock Roster)
- [ ] **Assign labels** to each Pi card with the student's name as they sit down
- [ ] **Brief the instructor team** on the room layout in the panel

## During the event

- [ ] **Monitor the teacher panel** — any Pi turning red means investigate (usually voltage warning or cable wiggle)
- [ ] **Between lab steps**: run `./scripts/otlab-reset.sh --step` for the relevant Pi(s) if the exercise needs a clean baseline
- [ ] **Watch Grafana** — Suricata alert counts give you a real-time view of who's actively probing
- [ ] **If a Pi dies hard**: have a spare ready with the same student-N install. Swap, run `otlab-install.sh --student-id N --reinstall`, run `bootstrap-students.sh <pi-ip>`, student is back in ~25 min.

## End of event

- [ ] **Full reset on every Pi**:
  ```bash
  for n in $(seq -w 01 20); do
    ./scripts/otlab-reset.sh --full --yes otadmin@otlab-student-$n.local
  done
  ```
- [ ] **Pull network configs** for backup:
  - MikroTik: `/export file=otlab-classroom-postevent.rsc` then download
  - Cisco: `copy running-config tftp://<your-ip>/otlab-classroom-postevent.ios`
- [ ] **Power down** everything in order: students → switch → router
- [ ] **Pack the kit** — count what came in, what's going out, note any missing cables
- [ ] **Write up the event in this Notion space** — what worked, what didn't, what to change before the next one

## What to bring (instructor's go-bag)

| Item | Why |
|---|---|
| Laptop with full repo cloned | Run installer, run reset, push fixes |
| USB-C → Ethernet adapter | Plug the laptop directly into the Cisco for emergency mgmt |
| Console cable for Cisco | If the switch loses its mgmt IP, console is the only way back in |
| Console cable for MikroTik (or `winbox` ready) | Same reason |
| Spare microSD or NVMe + USB enclosure | For emergency Pi re-image |
| Phone with hotspot | When venue internet dies, you still need apt for emergency installs |
| Power strip + multi-region adapter | International events |
| Spare Pi 5 (in case one dies) | One spare per 20 Pis is enough |
| Printed copy of this checklist | When the wifi is down |

## Common event-day issues

| Symptom | First fix |
|---|---|
| Pi doesn't appear in teacher panel | Check the Cisco port LED — if dark, swap cable or move to another port (then update MikroTik DHCP reservation if needed) |
| Student panel "offline" intermittently | Voltage warning — swap PSU |
| MikroTik DHCP not handing out leases | `/ip dhcp-server lease print` — verify the server is enabled |
| Cisco port shows `err-disabled` | BPDU guard tripped — `int gi1/0/N / shut / no shut` |
| Grafana shows no logs from a student | `ssh otadmin@<student-ip> 'sudo systemctl status promtail-otlab'` |
| Teacher panel hangs at "discovering" | Check `SCAN_BASE` / `SCAN_START` / `SCAN_END` env match the classroom subnet |

## See also

- **Classroom Installer & Reset** — install + reset script details
- **Classroom Network Map** — full network architecture
- **Hardware Kit — Cruiser Keel + Cisco** — bill of materials
- [`teacher/TESTING.md`](https://github.com/aaroncorvo/otlab/blob/main/teacher/TESTING.md) — 12-case smoke test
