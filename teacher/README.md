# Teacher Admin Panel

Companion app for the OTLab. **Audience: classroom instructor running a
multi-Pi cohort.** Different audience than the OTLab single-operator
dashboard (`dashboard/`) — runs separately, on a different network,
with its own deployment.

## What it does

1. **Pi auto-discovery** — ping-sweeps a configurable classroom subnet,
   SSHes responding hosts to confirm they're Pis, captures hostname + CPU
   / mem / temp / disk / uptime / load. Runs every `PROBE_INTERVAL` seconds
   until the roster is locked.
2. **Drag-and-drop classroom canvas** — instructor arranges Pi cards on a
   2D canvas to match the physical room layout. Positions persist across
   container restarts. Click "Lock Roster" once the class is full to stop
   discovery.
3. **Student labels** — assign a student name to each Pi card.
4. **Health refresh** — every `HEALTH_INTERVAL` seconds, re-polls each
   known Pi for live metrics. Cards turn red when a Pi goes offline.
5. **FortiGate port monitor (optional)** — when `FORTI_IP` is set, the
   panel renders a FortiGate card showing live port stats from a Fortinet
   firewall. Supports session auth (admin user/pass) and API token. Helps
   the instructor see which student is plugged into which switch port.
6. **Demo mode** — `POST /api/demo` seeds 12 fake students for
   presentations or screenshots.

## Quick start

From the repo root:

```sh
# Build
docker build -t otlab-teacher -f teacher/Dockerfile teacher/

# Run — basic single-Pi classroom (no FortiGate)
docker run -d --name otlab-teacher \
  -p 8080:8080 \
  -e SCAN_BASE=192.168.1 \
  -e SCAN_START=100 \
  -e SCAN_END=200 \
  -e MAX_HOSTS=30 \
  -v classroom-state:/var/lib/teacher \
  otlab-teacher

# Visit http://<host>:8080/
# Login:  otlab / P@ssw0rd!  (default — rotate per event)
```

Run with the FortiGate panel enabled:

```sh
docker run -d --name otlab-teacher \
  -p 8080:8080 \
  -e SCAN_BASE=192.168.1 \
  -e FORTI_IP=192.168.0.10 \
  -v classroom-state:/var/lib/teacher \
  otlab-teacher
```

## Environment variables

All have defaults; all are runtime-overridable.

| Var | Default | Purpose |
|---|---|---|
| `SCAN_BASE` | `192.168.1` | First three octets of the classroom subnet |
| `SCAN_START` | `100` | First host octet to scan |
| `SCAN_END` | `200` | Last host octet to scan |
| `SSH_USER` | `otadmin` | SSH username for Pi health probes — OTLab convention |
| `SSH_PASS` | `P@ssw0rd!` | SSH password for Pi health probes — OTLab convention |
| `DASH_USER` | `otlab` | HTTP basic auth user for the teacher dashboard |
| `DASH_PASS` | `P@ssw0rd!` | HTTP basic auth password for the teacher dashboard |
| `FORTI_IP` | *(empty)* | FortiGate management IP. **Empty = panel hidden.** Set to enable. |
| `FORTI_TIMEOUT` | `8` | Seconds before FortiGate HTTPS calls time out |
| `PROBE_INTERVAL` | `30` | Seconds between discovery sweeps |
| `HEALTH_INTERVAL` | `15` | Seconds between per-host health polls |
| `LISTEN_PORT` | `8080` | HTTP listen port |
| `DATA_DIR` | `/var/lib/teacher` | Persistent state directory (roster, layout, lock) |
| `MAX_HOSTS` | `0` | Auto-lock the roster once N hosts found. 0 = off |

## Security posture — asymmetric trust

The default trust model is **teacher key in, students hold nothing**.

| Direction | Allowed? | How |
|---|---|---|
| Teacher panel → any student (SSH/22) | ✅ yes | ed25519 keypair in the teacher's persistent volume; pubkey is in each student's `authorized_keys` |
| Student → teacher panel | ❌ no | Students have no SSH key or token for the teacher box |
| Student → another student | ❌ no | Students have no SSH key for each other, and `PasswordAuthentication` is disabled after bootstrap so the shared lab password no longer opens anything |
| Student → internet | ✅ yes | For `apt`, lab updates, etc. — not gated by the teacher panel |

### How to set it up

1. **Bootstrap the students with the OTLab convention** so they have `otadmin`/`P@ssw0rd!` (this is the temporary credential — disabled in step 3). Use `scripts/bootstrap-users.sh` for each Pi.

2. **Start the teacher panel** — the keypair auto-generates on first start (ed25519, lives at `/var/lib/teacher/keys/id_ed25519`).

3. **Run the bootstrap script** from the host running the panel:
   ```sh
   ./teacher/bootstrap-students.sh 192.168.1.101 192.168.1.102 192.168.1.103
   # or a range:
   ./teacher/bootstrap-students.sh --range 192.168.1.100-120
   ```
   The script:
   - Fetches the panel's pubkey via `GET /api/teacher/pubkey`
   - For each student: SSHes in **once** with the shared password, appends the pubkey to `~otadmin/.ssh/authorized_keys`, drops `/etc/ssh/sshd_config.d/99-teacher-key-only.conf` with `PasswordAuthentication no`, reloads sshd
   - Idempotent — safe to re-run if the class roster changes

4. **Verify**: try to SSH into a student WITHOUT the teacher's key — should be refused.
   ```sh
   ssh otadmin@192.168.1.101                # → Permission denied (publickey)
   sshpass -p 'P@ssw0rd!' ssh otadmin@192.168.1.101    # → same — password auth disabled
   ```
   And from inside the teacher container — should still work:
   ```sh
   docker exec otlab-teacher ssh -i /var/lib/teacher/keys/id_ed25519 otadmin@192.168.1.101 hostname
   ```

### Restoring password access (if needed)

If something goes sideways and you need to get back in with a password — physically attach a keyboard + monitor to the Pi, log in locally as `otadmin`, then:

```sh
sudo rm /etc/ssh/sshd_config.d/99-teacher-key-only.conf
sudo systemctl reload sshd
```

Password auth is back. Or just re-image the Pi.

### Why this matters

Classroom security best practice: **nobody walks out of class with credentials that work elsewhere.** With the asymmetric model:
- Students have nothing to steal — no SSH keys, no Tailscale auth, no shared passwords that work after bootstrap
- A student who pokes at another student's Pi can't SSH in (no key, no password)
- The teacher's private key never leaves the teacher's persistent volume

If you want stronger isolation (student-to-student blocked at L3 not just at sshd), use a managed switch with VLANs or set up the FortiGate ACLs (per-student VLAN, deny student→student inter-VLAN routing). Tracked as a future enhancement.

## Network expectations

The teacher panel needs:

| | What |
|---|---|
| **Reachability** | The host running this container must be able to reach every student Pi via ICMP (ping) + TCP/22 (SSH). |
| **Same broadcast domain** *(recommended)* | If your teacher box and students share a `/24`, ARP works and the ping sweep is fast. If they're on different subnets you'll need to widen `SCAN_BASE` / `SCAN_START` / `SCAN_END` and accept slower sweeps. |
| **Shared SSH creds** | Every student Pi must accept the same `SSH_USER` / `SSH_PASS`. If your class uses per-student creds, this panel won't work in its current form (issue #7 would extend it). |
| **DHCP scope** | Recommend a `/23` for the teacher subnet and a `/24` for student devices (e.g. teacher at `192.168.0.0/23`, students at `192.168.1.0/24`). |
| **Firewall** *(optional)* | A FortiGate at a known management IP, with either a REST API admin (API token mode) or an `admin` account (session mode). |

## API surface

All endpoints require HTTP basic auth.

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/` | Dashboard HTML |
| `GET`  | `/api/status` | Roster, layout, lock state, scan config, FortiGate enabled flag |
| `POST` | `/api/scan` | Force an immediate discovery sweep |
| `POST` | `/api/lock` | Lock the roster (stop discovery) |
| `POST` | `/api/unlock` | Resume discovery |
| `POST` | `/api/demo` | Seed 12 fake students (presenter mode) |
| `POST` | `/api/layout` | `{ip, x, y}` — persist a card's canvas position |
| `POST` | `/api/arrange` | Reset all cards to a left-to-right grid |
| `POST` | `/api/label/<ip>` | `{label}` — assign a student name to a Pi |
| `POST` | `/api/remove/<ip>` | Remove a host from the roster |
| `POST` | `/api/clear_offline` | Drop all offline hosts |
| `GET`  | `/api/teacher/pubkey` | Return the teacher panel's SSH public key (used by `bootstrap-students.sh` to push to students) |
| `POST` | `/api/fortigate/connect` | `{user, pass}` or `{token}` — authenticate to the FortiGate |
| `GET`  | `/api/fortigate/interfaces` | Live port stats from FortiOS |
| `POST` | `/api/fortigate/disconnect` | Clear FortiGate credentials |

FortiGate endpoints return HTTP 503 with `disabled: true` when `FORTI_IP`
is unset.

## State

Persistent state (`roster`, `layout`, `locked`) lives at
`/var/lib/teacher/state.json` inside the container — bind a docker
volume there to survive restarts.

State is written atomically (temp file + rename) on every mutation.

## Position in the OTLab repo

This component is intentionally **standalone** — it doesn't share code or
runtime state with the OTLab single-Pi dashboard (`dashboard/`). They're
two different products serving two different audiences:

| | OTLab Dashboard | Teacher Admin Panel |
|---|---|---|
| **Audience** | Operator running a single-Pi lab | Instructor running a multi-Pi cohort |
| **Scope** | One Pi, deep visibility (IDS, firewall, DHCP) | N Pis, shallow visibility (up/down, system health) |
| **Network** | OTLab fabric (`dmz-br0` + `pcn-br0`) | Classroom LAN |
| **Deployment** | Inside the ContainerLab topology | Standalone Docker container, anywhere with reach |

Both live in this repo because they're built for the same teaching context
(ICS Village), and a class typically deploys both — students each run an
OTLab lab; the instructor runs the teacher panel.

## Credit

Authored by Dillon Lee (ICSVillage) — PR [#1](https://github.com/aaroncorvo/otlab/pull/1).
Polished into the OTLab convention in issues #2–#8.
