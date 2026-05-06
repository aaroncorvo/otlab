# scripts/

Deployment + automation scripts for the OTLab.

## Bootstrap workflow (deploying the lab from scratch)

Three Pis. Each gets one bootstrap path:

### 1. softplc-1 / softplc-2 — full from-scratch deployment

```bash
# one-time, manual
ssh-copy-id otadmin@RASPLC01.local                       # SSH key auth

# fresh Pi OS Lite → OpenPLC installed + lab venv (~15 min)
./scripts/bootstrap-pi.sh otadmin@RASPLC01.local

# OpenPLC bare → role-configured (~30 s)
OPENPLC_PASSWORD='P@ssw0rd!' \
    ./scripts/bootstrap-openplc-role.sh otadmin@RASPLC01.local softplc-1
```

Same flow for `softplc-2` with role `softplc-2` instead of `softplc-1`. softplc-2 doesn't get a PLC program — the role configures the OpenPLC base (Hardware target, password, Start_run_mode) but no slave devices and no `.st` program. softplc-2's actual workload is sensor-sim (next step).

```bash
# softplc-2 also gets sensor-sim as a systemd service
./scripts/install-sensor-sim.sh otadmin@RASPLC02.local
```

### 2. honeypot-host — Conpot fabric

Not yet wrapped into a single script (planned). Manual steps:

```bash
ssh-copy-id otadmin@honeypot-host.local

# scp the honeypot tree, install Docker, bring up
scp -r honeypot otadmin@honeypot-host.local:~/conpot/compose
ssh otadmin@honeypot-host.local 'sudo apt update && sudo apt install -y docker.io docker-compose-plugin || sudo curl -sSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64" -o /usr/local/lib/docker/cli-plugins/docker-compose && sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose'
ssh otadmin@honeypot-host.local 'sudo usermod -aG docker $USER'
# log out + back in
ssh otadmin@honeypot-host.local 'cd ~/conpot/compose && mkdir -p logs/{siemens,schneider,allenbradley} && sudo chown -R 2000:2000 logs/ && docker compose up -d'
```

See `honeypot/README.md` for the full deploy walkthrough.

## Script reference

| Script | Purpose | Idempotent | Time |
|---|---|---|---|
| [`bootstrap-pi.sh`](bootstrap-pi.sh) | Fresh Pi OS → apt deps + raspi-config + OpenPLC v3 + lab venv | yes | ~15-20 min (most is OpenPLC compile) |
| [`bootstrap-openplc-role.sh`](bootstrap-openplc-role.sh) | OpenPLC bare → role-configured (`softplc-1` or `softplc-2`) | yes | ~30 s |
| [`install-sensor-sim.sh`](install-sensor-sim.sh) | Push `sensor-sim.py` + systemd unit, enable + start | yes | ~5 s |

All three accept `user@host` as the first arg. Default targets are `otadmin@RASPLC0X.local`; override per-deployment.

## Disaster recovery

If a Pi's storage dies, the recovery is fully automated by the bootstrap chain:

1. Re-image with Pi OS Lite (Pi Imager — same hostname as before, `RASPLC01` / `RASPLC02` / `honeypot-host`)
2. `ssh-copy-id` from the laptop
3. Run the appropriate scripts in order

The Pi will be back to its canonical lab role within ~20 minutes (most of that is the OpenPLC compile).

The repo's `honeypot/`, `plc/softplc1-sensor-monitor.st`, and `plc/sensor-sim.py` are the source of truth — the scripts reproduce DB state from those files.

## Adding a new role to bootstrap-openplc-role.sh

Edit the `case "$ROLE" in ...` block. Each role can specify:
- A `.st` program file to deploy (relative to repo root)
- A target filename inside `~/OpenPLC_v3/webserver/st_files/`
- Slave device IP/port + DI/coil/HR sizes

Example pattern in the script for `softplc-1`. Roles without programs (like the current `softplc-2`) just skip those fields.

## Adding a new lab-tooling Python script

Use the existing pattern in `install-sensor-sim.sh`: scp the file + a systemd unit, enable + start. Add docs at `plc/<thing>/README.md` mirroring `plc/sensor-sim/` style (when we get to that).
