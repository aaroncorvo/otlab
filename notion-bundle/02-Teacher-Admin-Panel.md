# Teacher Admin Panel — Setup & Use

Instructor's dashboard for a classroom of student Pis. Auto-discovers
Pis on the classroom subnet, lets you drag them onto a 2D canvas
matching the physical room, polls health, optionally taps a FortiGate
for switch port stats.

> **Repo source of truth**: [`teacher/README.md`](https://github.com/aaroncorvo/otlab/blob/main/teacher/README.md)

## What it does

1. **Pi auto-discovery** — ping-sweeps the classroom subnet (`192.168.10.100`–`192.168.10.120` by default), SSHes responding hosts to confirm they're Pis, captures hostname + CPU/mem/temp/disk/uptime/load
2. **Drag-and-drop classroom canvas** — arrange Pi cards on a 2D canvas to match the physical room. Positions persist across container restarts. Click "Lock Roster" once the class is full to stop discovery.
3. **Student labels** — assign a student name to each Pi card
4. **Health refresh** — every `HEALTH_INTERVAL` seconds, re-polls each known Pi. Cards turn red when a Pi goes offline.
5. **FortiGate port monitor** (optional) — when `FORTI_IP` is set, renders a FortiGate card showing live port stats. Helps identify which student is plugged into which switch port.
6. **Demo mode** — `POST /api/demo` seeds 12 fake students for presentations or screenshots

## Quick start

From the laptop running the teacher Pi:

```bash
docker build -t otlab-teacher -f teacher/Dockerfile teacher/

docker run -d --name otlab-teacher \
  -p 8080:8080 \
  -e SCAN_BASE=192.168.10 \
  -e SCAN_START=100 \
  -e SCAN_END=120 \
  -e MAX_HOSTS=21 \
  -v classroom-state:/var/lib/teacher \
  otlab-teacher
```

Visit `http://<teacher-ip>:8080` → login `otlab` / `P@ssw0rd!`.

With FortiGate enabled:

```bash
docker run -d --name otlab-teacher \
  -p 8080:8080 \
  -e SCAN_BASE=192.168.10 \
  -e FORTI_IP=192.168.10.1 \
  -v classroom-state:/var/lib/teacher \
  otlab-teacher
```

## Trust model — asymmetric SSH

**Teacher holds the only key. Students hold nothing.**

| Direction | Allowed? | How |
|---|---|---|
| Teacher → any student (SSH/22) | ✅ | ed25519 keypair in teacher's persistent volume; pubkey in each student's `authorized_keys` |
| Student → teacher | ❌ | Students have no key/token for the teacher box |
| Student → another student | ❌ | No SSH key, AND password auth disabled after bootstrap |
| Student → internet | ✅ | NAT'd via the upstream router for apt/Docker pulls |

### Setting it up

1. Bootstrap students with `otadmin/P@ssw0rd!` (temporary — disabled in step 3)
2. Start the teacher panel — keypair auto-generates on first start
3. From the host running the teacher panel:
   ```bash
   ./teacher/bootstrap-students.sh --range 192.168.10.101-120
   ```
   This pushes the teacher pubkey to every student, then disables PasswordAuthentication, then reloads sshd. Idempotent.

### Restoring password access (if needed)

If something goes sideways and you need back in via password:
- Plug keyboard + monitor into the Pi
- Log in locally as `otadmin`
- `sudo rm /etc/ssh/sshd_config.d/99-teacher-key-only.conf`
- `sudo systemctl reload sshd`

Password auth is back. Or just re-image the Pi.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `SCAN_BASE` | `192.168.1` | First three octets of classroom subnet |
| `SCAN_START` | `100` | First host octet to scan |
| `SCAN_END` | `200` | Last host octet to scan |
| `SSH_USER` | `otadmin` | Student Pi SSH username |
| `SSH_PASS` | `P@ssw0rd!` | Student Pi SSH password (one-time bootstrap) |
| `DASH_USER` | `otlab` | Teacher dashboard basic-auth user |
| `DASH_PASS` | `P@ssw0rd!` | Teacher dashboard basic-auth pass |
| `FORTI_IP` | *(empty)* | FortiGate management IP. Empty = panel hidden. |
| `PROBE_INTERVAL` | `30` | Seconds between discovery sweeps |
| `HEALTH_INTERVAL` | `15` | Seconds between per-host health polls |
| `MAX_HOSTS` | `0` | Auto-lock roster after N hosts. 0 = off |

## API surface

All endpoints require HTTP basic auth.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Dashboard HTML |
| GET | `/api/status` | Roster, layout, lock state, scan config, FortiGate enabled flag |
| POST | `/api/scan` | Force immediate discovery sweep |
| POST | `/api/lock` | Lock roster (stop discovery) |
| POST | `/api/unlock` | Resume discovery |
| POST | `/api/demo` | Seed 12 fake students |
| POST | `/api/layout` | `{ip, x, y}` — persist card position |
| POST | `/api/arrange` | Reset cards to grid |
| POST | `/api/label/<ip>` | Assign student name |
| POST | `/api/remove/<ip>` | Remove host from roster |
| POST | `/api/clear_offline` | Drop all offline hosts |
| GET | `/api/teacher/pubkey` | Return panel's SSH public key (used by bootstrap-students.sh) |
| POST | `/api/fortigate/connect` | `{user, pass}` or `{token}` — auth to FortiGate |
| GET | `/api/fortigate/interfaces` | Live port stats from FortiOS |
| POST | `/api/fortigate/disconnect` | Clear FortiGate credentials |

FortiGate endpoints return HTTP 503 with `disabled: true` when `FORTI_IP` is unset.

## Smoke test (after install)

1. Visit `/` → basic-auth prompt → log in → dashboard loads
2. Within `PROBE_INTERVAL` (30s default), all student Pis appear as cards
3. Health metrics populate (CPU/mem/temp/disk/uptime)
4. Status badge reads "online" for all
5. Drag cards to match room layout
6. Click **Lock Roster** → no new cards appear
7. Click each Pi → assign student label
8. (Optional FortiGate): click FortiGate card → auth → port stats populate

Full 12-case test plan: [`teacher/TESTING.md`](https://github.com/aaroncorvo/otlab/blob/main/teacher/TESTING.md)

## Credit

Authored by Dillon Lee ([@LogicGateOperator](https://github.com/LogicGateOperator)) — PR [#1](https://github.com/aaroncorvo/otlab/pull/1). Polished into the OTLab convention in issues #2–#11.

## See also

- **Classroom Installer & Reset** — how the teacher panel fits into the install flow
- **Classroom SIEM** — the Loki/Grafana stack that runs alongside this
- [`teacher/TESTING.md`](https://github.com/aaroncorvo/otlab/blob/main/teacher/TESTING.md) — 12-case smoke test
