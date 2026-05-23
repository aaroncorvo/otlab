# Teacher Admin Panel — 2-Pi Smoke Test

Before classroom rollout, validate the teacher panel against at least
**two real student Pis** on a real classroom LAN. The discovery + lock +
labelling flows have edge cases that don't show up with the demo-data
endpoint.

This doc is a manual checklist. Run it once end-to-end after every
non-trivial change to `teacher/`.

## Hardware

| Role | What | Notes |
|---|---|---|
| Teacher host | Anything that runs Docker — Mac, Linux laptop, another Pi, Linux VM | Needs network reach to the student Pis (ping + SSH/TCP 22) |
| Student Pi #1 | Raspberry Pi (any model) running OTLab single-Pi or just a fresh Pi OS | Must have `otadmin` user with `P@ssw0rd!` password, SSH enabled |
| Student Pi #2 | Same | Different MAC + IP than #1 |
| Classroom switch / wifi | Any unmanaged switch or wifi AP | All three devices on the same broadcast domain (recommended for fast discovery) |
| FortiGate *(optional)* | Any Fortinet on the same network | Only if you're testing the FortiGate panel; skip otherwise |

If you don't have two physical Pis, two VMs or two Docker containers
running an SSH daemon on the same LAN will exercise the same code paths.

## Setup

1. **Image both Pis** with Pi OS Lite (Bookworm 64-bit). In Pi Imager
   "Advanced options":
   - Username: `otadmin`
   - Password: `P@ssw0rd!`
   - SSH enabled
   - Same wifi (if using wifi instead of Ethernet)
2. **Boot both Pis**. Verify they get IPs in your DHCP scope (check your
   router's "Connected Devices" page, or `arp -a` from the teacher host).
3. **Note the IP range** the Pis landed in. Most consumer routers hand
   out leases somewhere in `192.168.0.100`–`192.168.1.254`. Adjust
   `SCAN_BASE` / `SCAN_START` / `SCAN_END` env vars to bracket your range.
4. **Build the teacher panel** from the repo root:
   ```sh
   docker build -t otlab-teacher -f teacher/Dockerfile teacher/
   ```
5. **Run it**, pointing at the right subnet:
   ```sh
   docker run -d --name otlab-teacher \
     -p 8080:8080 \
     -e SCAN_BASE=192.168.1 \
     -e SCAN_START=100 \
     -e SCAN_END=254 \
     -v classroom-state:/var/lib/teacher \
     otlab-teacher
   ```
6. **Open** `http://<teacher-host-ip>:8080/` in a browser.
   Auth prompt: `otlab` / `P@ssw0rd!`.

## Test cases

Run each one; tick when it passes.

### 1. Basic auth gate

- [ ] Visit `/` without creds — browser shows basic-auth prompt
- [ ] Wrong password → `401 Unauthorized`
- [ ] Correct password (`otlab` / `P@ssw0rd!`) → dashboard loads
- [ ] Same prompt protects `/api/status` (try via `curl`)

```sh
curl -u otlab:wrong       http://<host>:8080/api/status  # expect 401
curl -u otlab:P@ssw0rd!   http://<host>:8080/api/status  # expect 200 + JSON
```

### 2. Pi auto-discovery

- [ ] Within `PROBE_INTERVAL` (default 30s) both Pis appear as cards on
      the canvas
- [ ] Each card shows the correct hostname (set during Pi Imager)
- [ ] Health metrics populate within `HEALTH_INTERVAL` (default 15s):
      CPU %, mem %, temp °C, disk %, uptime, load
- [ ] Status badge for both reads "online"

If a card doesn't appear: check that the Pi accepts the configured
`SSH_USER` / `SSH_PASS`. Default is `otadmin` / `P@ssw0rd!`.

### 3. Drag-and-drop layout

- [ ] Drag Pi #1's card from its default grid slot to a new position
- [ ] Drag Pi #2 elsewhere
- [ ] Click **Lock Roster** in the top bar
- [ ] Wait `PROBE_INTERVAL` — no new cards appear, lock badge says "Locked"
- [ ] Restart the container: `docker restart otlab-teacher`
- [ ] After restart: cards reappear in the **same dragged positions**, lock state preserved

### 4. Student labels

- [ ] Click a Pi card → label input appears
- [ ] Enter "Alice" → press enter
- [ ] Card now shows "Alice" prominently
- [ ] Restart container → label persists

### 5. Auto-arrange

- [ ] Drag both cards to weird positions
- [ ] Click **Auto-Arrange** in the top bar
- [ ] Cards snap back to a clean left-to-right grid

### 6. Offline detection + clear

- [ ] Power off Pi #2 (or block its SSH)
- [ ] Within ~30 seconds (2× HEALTH_INTERVAL): card flips to red "offline" status
- [ ] Click **Clear Offline** → card disappears
- [ ] Power Pi #2 back on (or unblock SSH) → unlock the roster, it'll be rediscovered

### 7. Demo mode (no Pis required)

- [ ] `curl -u otlab:P@ssw0rd! -X POST http://<host>:8080/api/demo`
- [ ] 12 fake students (Alice, Bob, Carol, …) appear in a 4-wide grid
- [ ] Roster is locked
- [ ] Useful for screen-recording demos / presentations

### 8. FortiGate disabled (single-Pi classroom)

- [ ] Container started WITHOUT `FORTI_IP` set
- [ ] Browse `/api/status` — `config.fortigate.enabled` is `false`
- [ ] Instructor zone with FortiGate card is **NOT visible** on the canvas
- [ ] Direct call: `curl -u otlab:P@ssw0rd! http://<host>:8080/api/fortigate/interfaces` returns HTTP 503 with `disabled: true`

### 9. FortiGate enabled — session auth *(only if you have a Fortinet)*

- [ ] Restart container with `-e FORTI_IP=<your-fortigate-ip>`
- [ ] `/api/status` now shows `config.fortigate.enabled = true` and the IP
- [ ] FortiGate card renders in the Instructor zone with the configured IP visible
- [ ] Click the card → auth modal opens; subtitle shows the configured IP (not `192.168.0.10`)
- [ ] Enter admin username/password → modal closes, status badge turns "Connected"
- [ ] Port stats table populates with real interface names + rx/tx counters
- [ ] Click **Disconnect** → status returns to "Click to authenticate"

### 10. FortiGate enabled — API token *(only if you have a REST API admin)*

- [ ] In FortiGate: System → Administrators → Create REST API user, copy the token
- [ ] Click the FortiGate card → leave user/pass blank, paste token into the API Token field
- [ ] Submit → "Connected" status; port stats populate

### 11. Persistence

- [ ] After running for 5+ minutes, `docker stop otlab-teacher && docker rm otlab-teacher`
- [ ] Re-run with the same `-v classroom-state:/var/lib/teacher` flag
- [ ] All cards, labels, layouts, and lock state come back exactly

### 12. Asymmetric SSH trust — teacher key in, students locked down

This validates the security posture: teacher's key works, password auth gone.

- [ ] Panel was started fresh (volume empty) — confirm `ls /var/lib/teacher/keys/` in the container shows `id_ed25519` + `id_ed25519.pub`
- [ ] `curl -u otlab:P@ssw0rd! http://<host>:8080/api/teacher/pubkey` returns the pubkey
- [ ] Run `./teacher/bootstrap-students.sh <pi1-ip> <pi2-ip>` — should succeed for both
- [ ] **Negative test**: from your laptop, try `sshpass -p 'P@ssw0rd!' ssh otadmin@<pi1-ip> hostname` — should be **rejected** (`Permission denied (publickey)`)
- [ ] **Positive test**: from inside the teacher container, key auth works:
      ```sh
      docker exec otlab-teacher ssh -i /var/lib/teacher/keys/id_ed25519 \
          -o StrictHostKeyChecking=accept-new otadmin@<pi1-ip> hostname
      ```
- [ ] **Cross-student test**: SSH from pi1 → pi2 should also be **rejected** (no key, no password)
- [ ] Teacher panel dashboard still shows both Pis as "online" (it switched to key auth automatically)
- [ ] Restart the teacher container — same key, students still accept it

## Triage

If a test fails:

| Symptom | Likely cause | Fix |
|---|---|---|
| Cards never appear | Wrong `SCAN_BASE` / `SCAN_START` / `SCAN_END`, OR firewall blocking ICMP, OR Pis not on the same subnet | Verify with `ping <pi-ip>` from the teacher host |
| Cards appear but show "offline" immediately | SSH creds mismatch (Pis use a different user/pass) | Override `SSH_USER` / `SSH_PASS` env |
| Health values all zero | Health script failed to run — Pi might not have `bash` available at `/bin/bash` | SSH manually + run the script from `teacher/teacher.py` to see error |
| Basic auth prompt loops | Wrong `DASH_USER` / `DASH_PASS` env, OR browser cached old creds | Clear browser auth cache; double-check env |
| FortiGate "Token rejected" | API admin doesn't have the right scope, OR Fortinet's trusted-hosts ACL is blocking your IP | Add a trusted-host entry in the FortiGate admin profile |
| Layout doesn't persist | `-v classroom-state:/var/lib/teacher` not in the docker run command | Add the volume mount |

## After passing

Once all 11 cases pass against 2 real Pis, the teacher panel is ready
for cohort rollout. Document any deviations or environment-specific
quirks in this file (or open an issue) so the next person testing has
the same context.
