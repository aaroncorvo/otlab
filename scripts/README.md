# scripts/

Deployment + automation for the OTLab. All scripts run from the laptop; SSH into the target Pi and orchestrate from there. Idempotent throughout — safe to re-run any time to reset to the canonical state in this repo.

## User model

The lab uses two non-root accounts on every Pi:

| User | Sudo | Purpose |
|---|---|---|
| `otadmin` | NOPASSWD | What scripts SSH in as. Runs installs, edits systemd, manages services. |
| `otuser` | none | Operator / attendee account. For inspection, running probes, watching logs. Owns the lab venv at `/home/otuser/lab/.venv-modern/`. `sensor-sim.service` runs as this user. |

Both accept the same SSH public key from the laptop. Created from a fresh Pi with `bootstrap-users.sh`.

## Bootstrap workflow (deploying the lab from scratch)

### 0. One-time per Pi: create the lab users

Run against whatever user the Pi was imaged with (Pi Imager prompts for one during the OS image step). That user must already have NOPASSWD sudo (default for the Pi-Imager-created user).

```bash
ssh-copy-id <existing-user>@<host>.local         # one-time, prompts for password
./scripts/bootstrap-users.sh <existing-user>@<host>.local
```

After this completes, the Pi has `otadmin` (NOPASSWD sudo, SSH key auth) and `otuser` (no sudo, SSH key auth). Subsequent scripts default to `otadmin@<host>.local`.

### 1. softplc-1 / softplc-2 — OpenPLC + lab venv

```bash
# fresh Pi OS Lite + bootstrap-users.sh has run
./scripts/bootstrap-pi.sh otadmin@RASPLC01.local
# (~15-20 min, mostly OpenPLC's matiec compile)

# canonical role config:
OPENPLC_PASSWORD='P@ssw0rd!' \
    ./scripts/bootstrap-openplc-role.sh otadmin@RASPLC01.local softplc-1
# (~30 s)
```

Same flow for `softplc-2` with role `softplc-2`. softplc-2 doesn't get a PLC program — its OpenPLC base config is set (Hardware target, password) but no slave devices and no `.st`. softplc-2's actual workload is `sensor-sim`, deployed separately:

```bash
./scripts/install-sensor-sim.sh otadmin@RASPLC02.local
```

### 2. honeypot-host — Conpot fabric

```bash
./scripts/bootstrap-honeypot.sh otadmin@honeypot-host.local
# (~3-5 min on a fresh Pi, ~5 s on an idempotent re-run)
```

Installs Docker + Compose v2 plugin if not present, rsyncs the `honeypot/` tree to `~/conpot/compose/` on the Pi, ensures log directories are owned by UID 2000, runs `docker compose up -d`. Verification probes (cross-Pi snmpwalk / curl) run from softplc-2 — see `honeypot/README.md` for the full cross-Pi verification battery.

## Script reference

| Script | Purpose | Idempotent | Time |
|---|---|---|---|
| [`bootstrap-users.sh`](bootstrap-users.sh) | Pi Imager user → otadmin + otuser with NOPASSWD + SSH keys | yes | ~5 s |
| [`bootstrap-pi.sh`](bootstrap-pi.sh) | Fresh Pi OS → apt deps + raspi-config + OpenPLC v3 + lab venv | yes | ~15-20 min |
| [`bootstrap-openplc-role.sh`](bootstrap-openplc-role.sh) | OpenPLC bare → role-configured (`softplc-1` or `softplc-2`) | yes | ~30 s |
| [`install-sensor-sim.sh`](install-sensor-sim.sh) | Push `sensor-sim.py` + systemd unit (runs as otuser), enable + start | yes | ~5 s |
| [`bootstrap-honeypot.sh`](bootstrap-honeypot.sh) | Pi OS → Docker + 3-persona Conpot fabric on macvlan | yes | ~3-5 min first run |

All scripts accept `user@host` as the first arg. Defaults are `otadmin@RASPLC0X.local` (or `otadmin@honeypot-host.local`); override per-deployment.

## Disaster recovery

If a Pi's storage dies, the recovery is fully scripted:

1. Re-image with Pi OS Lite (Pi Imager — same hostname as before, `RASPLC01` / `RASPLC02` / `honeypot-host`)
2. `ssh-copy-id <imager-user>@<host>.local` (one-time password auth)
3. `./scripts/bootstrap-users.sh <imager-user>@<host>.local`
4. Run the appropriate bootstrap chain for that Pi's role

Total time per Pi: ~20 min for soft-PLCs, ~5 min for honeypot-host.

The repo's `.st` programs, `plc/sensor-sim.py`, and `honeypot/` tree are the source of truth — scripts reproduce DB state and runtime config from those files.

## Adding a new role to bootstrap-openplc-role.sh

Edit the `case "$ROLE" in ...` block. Each role can specify:
- A `.st` program file to deploy (relative to repo root)
- A target filename inside `~/OpenPLC_v3/webserver/st_files/`
- Slave device IP/port + DI/coil/HR sizes

Example pattern in the script for `softplc-1`. Roles without programs (like the current `softplc-2`) just leave those fields empty.
