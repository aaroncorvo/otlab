# scripts/

Deployment + automation for the OTLab. All scripts run from the laptop; SSH into the target Pi and orchestrate from there. Idempotent throughout — safe to re-run any time to reset a Pi to the canonical state in this repo.

## User model

Every Pi runs two non-root accounts:

| User | Sudo | Purpose |
|---|---|---|
| `otadmin` | NOPASSWD | What scripts SSH in as. Runs installs, edits systemd, manages services. |
| `otuser` | none | Operator / attendee account. For inspection, running probes, watching logs. Owns the lab venv at `/home/otuser/lab/.venv-modern/`. `sensor-sim.service` and `otlab-dashboard.service` run as this user. |

Both accept the same SSH public key from the laptop. Both are members of `dialout`, `gpio`, `i2c`, `spi`, `video`, `wireshark`, and `adm` (so `journalctl` works without sudo, which the dashboard's failed-SSH telemetry relies on). Created from a fresh Pi by `bootstrap-users.sh`.

`otuser` is *enforced* non-sudo — `bootstrap-users.sh` strips any sudoers drop-in and removes from sudo/wheel groups even if Pi Imager seeded the initial user as "otuser" (see [the canonical-user-model commit](https://github.com/aaroncorvo/otlab/commit/92b5e52)).

## Bootstrap workflow (deploying the lab from scratch)

### 0. One-time per Pi: create the lab users

Run against whatever user the Pi was imaged with (Pi Imager prompts for one during the OS image step). That user must already have NOPASSWD sudo (default for the Pi-Imager-created user).

```bash
ssh-copy-id <existing-user>@<host>.local         # one-time, prompts for password
./scripts/bootstrap-users.sh <existing-user>@<host>.local
```

After this completes, the Pi has `otadmin` (NOPASSWD sudo, SSH key auth) + `otuser` (no sudo, SSH key auth), `cloud-init` is disabled (so manual hostname / `/etc/hosts` changes stick across reboots), and wifi powersave is off (so the Pi stays reachable from a wifi-only host). Subsequent scripts default to `otadmin@<host>.local`.

### 1. softplc-1 — OpenPLC master

```bash
./scripts/bootstrap-pi.sh                       otadmin@RASPLC01.local           # ~15-20 min (matiec compile)
OPENPLC_PASSWORD='P@ssw0rd!' \
    ./scripts/bootstrap-openplc-role.sh         otadmin@RASPLC01.local softplc-1 # ~30 s
```

Deploys the `softplc1-sensor-monitor.st` program (polls sensor-sim at 100 ms, mirrors values to local registers, computes link-liveness telemetry), configures the slave-device row pointing at `softplc-2:5020`, regenerates `mbconfig.cfg`, compiles, sets `Start_run_mode=true`.

### 2. softplc-2 — sensor-sim slave + DNP3 outstation + dashboard host

```bash
./scripts/bootstrap-pi.sh                       otadmin@RASPLC02.local
OPENPLC_PASSWORD='P@ssw0rd!' \
    ./scripts/bootstrap-openplc-role.sh         otadmin@RASPLC02.local softplc-2
./scripts/install-sensor-sim.sh                 otadmin@RASPLC02.local           # ~5 s — sensor-sim + scenarios + tests/
./scripts/install-dnp3.sh                       otadmin@RASPLC02.local           # ~5 s — DNP3 outstation on :20000
./scripts/install-dashboard.sh                  otadmin@RASPLC02.local           # ~30 s
```

softplc-2's role-config clears any program + slave devices (its OpenPLC runtime stays dormant — no `:502` binding). `install-sensor-sim.sh` deploys `plc/sensor-sim.py` + scenarios + tests (runs as `otuser`, listens on `:5020` for Modbus + `:5021` for fault-injection control). `install-dnp3.sh` deploys the DNP3 outstation on `:20000` (utility-vertical wire surface). `install-dashboard.sh` deploys the Flask dashboard, generates SAN-rich self-signed TLS, lays down sudoers + SSH keypair for cross-Pi reboot/restart/capture orchestration.

### 3. honeypot-host — Conpot fabric

```bash
./scripts/bootstrap-honeypot.sh                 otadmin@honeypot-host.local      # ~3-5 min first run, ~5 s on idempotent re-run
```

Installs Docker + Compose v2 plugin if not present, rsyncs the `honeypot/` tree to `~/conpot/compose/` on the Pi, ensures log directories are owned by UID 2000, runs `docker compose up -d`. Verification probes (cross-Pi snmpwalk / curl) run from softplc-2 — see [`honeypot/README.md`](../honeypot/README.md) for the full battery.

## Script reference

| Script | Purpose | Idempotent | Time |
|---|---|---|---|
| [`bootstrap-users.sh`](bootstrap-users.sh) | Pi Imager user → otadmin + otuser, NOPASSWD + SSH keys, **strip otuser sudo if leaked**, **disable cloud-init**, **disable wifi powersave**, stamp `/etc/otlab-bootstrap-info` | yes | ~5 s |
| [`bootstrap-pi.sh`](bootstrap-pi.sh) | Fresh Pi OS → apt deps + raspi-config (I2C/SPI/UART) + group memberships (dialout/gpio/i2c/spi/video/wireshark/adm) + OpenPLC v3 + lab venv (pymodbus, paho-mqtt, etc.) + cloud-init disable safety net + bootstrap-info stamp | yes | ~15-20 min |
| [`bootstrap-openplc-role.sh`](bootstrap-openplc-role.sh) | OpenPLC bare → role-configured (`softplc-1` or `softplc-2`): hardware target, web-UI password (cleartext per OpenPLC's compare logic), Start_run_mode, slave-device + mbconfig.cfg, compile | yes | ~30 s |
| [`install-sensor-sim.sh`](install-sensor-sim.sh) | Push `plc/sensor-sim.py` + `plc/scenarios/*.json` + `plc/tests/test-*.{py,sh}` + systemd unit (runs as otuser), enable + start. Sensor-sim listens on TCP/5020 (Modbus FC1-6,15,16) + TCP/5021 (fault-injection + writes-override + scenario HTTP control). Test scripts auto-discovered by the dashboard's Test Library. | yes | ~5 s |
| [`install-dnp3.sh`](install-dnp3.sh) | Push `plc/dnp3-outstation.py` + systemd unit. Pure-stdlib DNP3 outstation listening on TCP/20000, scenario-driven (loads same scenario JSON as sensor-sim). Utility-vertical teaching artifact. | yes | ~5 s |
| [`install-dashboard.sh`](install-dashboard.sh) | Deploy Flask dashboard to softplc-2: rsync source, install Flask deps, generate SAN-rich self-signed TLS, sudoers drop-in (narrow NOPASSWD for reboot + tcpdump + service-restart), otuser SSH keypair → otadmin@remote-Pis, captures + ControlMaster directories, audit-log SQLite, systemd unit | yes | ~30 s |
| [`bootstrap-honeypot.sh`](bootstrap-honeypot.sh) | Pi OS → Docker + Compose v2 + 3-persona Conpot fabric on macvlan + cloud-init disable + wifi powersave fix + adm group + bootstrap-info stamp | yes | ~3-5 min first run |

All scripts accept `user@host` as the first arg. Defaults are `otadmin@RASPLC0X.local` (or `otadmin@honeypot-host.local`); override per-deployment.

After every successful run, scripts write `/etc/otlab-bootstrap-info` with the timestamp + the git commit hash from the laptop's working tree + which script wrote it. The dashboard surfaces this on each Pi's system-health card so you can tell at a glance which version is deployed.

## Disaster recovery

If a Pi's storage dies, recovery is fully scripted:

1. Re-image with Pi OS Lite (Pi Imager — same hostname as before, `RASPLC01` / `RASPLC02` / `honeypot-host`)
2. `ssh-copy-id <imager-user>@<host>.local` (one-time password auth)
3. `./scripts/bootstrap-users.sh <imager-user>@<host>.local`
4. Run the appropriate role chain for that Pi:
   - **softplc-1**: `bootstrap-pi.sh` → `bootstrap-openplc-role.sh ... softplc-1`
   - **softplc-2**: `bootstrap-pi.sh` → `bootstrap-openplc-role.sh ... softplc-2` → `install-sensor-sim.sh` → `install-dnp3.sh` → `install-dashboard.sh`
   - **honeypot-host**: `bootstrap-honeypot.sh`

Total time per fresh Pi: ~20 min for soft-PLCs (matiec compile is the long pole), ~5 min for honeypot-host.

The repo's `.st` programs, `plc/sensor-sim.py`, `dashboard/`, and `honeypot/` tree are the source of truth — scripts reproduce DB state, runtime config, and dashboard config from those files.

### NVMe migration (softplc-2 only)

If you're rebuilding softplc-2 with the NVMe HAT, image to SD first, run the full bootstrap chain, then `dd` the SD to the NVMe and patch PARTUUIDs in `cmdline.txt` + `fstab`. The Pi 5's BOOT_ORDER prefers NVMe (`0xf146`) so it boots from NVMe automatically; the SD stays inserted as a hot fallback. The repo doesn't ship a one-shot NVMe-clone script (rpi-clone has bugs with NVMe partition naming) — manual rsync after `mkfs` is the reliable path. Documented in the project journal; ask Aaron if you need to walk through it.

## Adding a new role to bootstrap-openplc-role.sh

Edit the `case "$ROLE" in ...` block. Each role can specify:
- A `.st` program file to deploy (relative to repo root)
- A target filename inside `~/OpenPLC_v3/webserver/st_files/`
- Slave device IP/port + DI/coil/HR sizes
- `Start_run_mode` (true to auto-run, false to leave dormant)
- An `active_program` row pointer (`blank_program.st` for roles with no program)

Example pattern in the script for `softplc-1`. The current `softplc-2` role is a no-program example.

## Adding a new bootstrap script

Each install script should end with the standard bootstrap-info stamp block so the dashboard surfaces what's deployed:

```bash
COMMIT="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$(basename "$0")"
ssh "$PI_HOST" "
sudo tee /etc/otlab-bootstrap-info >/dev/null <<EOF
ts=$TS
commit=$COMMIT
script=$SCRIPT
EOF
sudo chmod 644 /etc/otlab-bootstrap-info
"
```
