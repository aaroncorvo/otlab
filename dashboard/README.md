# OTLab Dashboard

Single-page status dashboard for the lab. Lives on `softplc-2` so it's reachable from both the lab segment (`10.20.30.49:8000`) and the mgmt network (`192.168.120.19:8000`). Runs as `otuser` under systemd.

## What it shows

Three rows:

1. **Network** — WAN, mgmt gateway, lab firewall (TP-Link). Simple ping cards.
2. **Process Control** — softplc-1, softplc-2, honeypot-host. Ping + OpenPLC web UI probe + (where applicable) Modbus TCP read showing live engineering values + heartbeat + link-liveness counters.
3. **Honeypot Fabric** — the three Conpot personas (Siemens, Schneider, Rockwell). TCP-port probes against each persona's vendor protocol stack (HTTP / S7comm / Modbus / EtherNet/IP).

Each soft-PLC card includes a **Reboot** button. The dashboard SSHes as `otadmin@<target>` and runs `sudo systemctl reboot`. Self-rebooting `softplc-2` works via a narrow sudoers drop-in.

## How it works

```
        ┌──────────────────────────────────┐
        │  Mac browser :8000 (HTTPS)       │
        └────────────────┬─────────────────┘
                         │ basic auth + self-signed TLS
                         ▼
        ┌──────────────────────────────────┐
        │  softplc-2: otlab-dashboard.svc  │  ← Flask app, runs as otuser
        │   - background probe loop @ 2.5s │
        │   - cached JSON via /api/status  │
        │   - POST /api/reboot/<host>      │
        └────────────────┬─────────────────┘
                         │ ping, TCP, HTTP HEAD, Modbus, ssh
                         ▼
            ┌─────────┬──────────┬───────────────┐
            │ softplc-1│ softplc-2│ honeypot-host │
            │ +.50/.51/.52 (Conpot)              │
            └────────────────────────────────────┘
```

A background thread runs every probe every `PROBE_INTERVAL` seconds (default 2.5 s) with per-call timeouts (default 1.5 s). HTTP requests just read the cached snapshot, so multiple browser tabs or clients don't multiply the probe load on the lab.

## Install

From the repo root, with `otadmin@RASPLC02.local` reachable:

```bash
./scripts/install-dashboard.sh
# or with explicit host:
./scripts/install-dashboard.sh otadmin@192.168.120.19
```

The script:

1. rsyncs `dashboard/` to `/home/otuser/lab/dashboard/` (owned by otuser)
2. installs Flask + Flask-HTTPAuth into the lab venv (pymodbus is already there from `bootstrap-pi.sh`)
3. generates a self-signed TLS cert if missing
4. lays down `/etc/sudoers.d/099_otuser_reboot` (narrow NOPASSWD rule for `systemctl reboot` only)
5. generates an ed25519 keypair for `otuser` and authorizes it as `otadmin@softplc-1` + `otadmin@honeypot-host`
6. installs + enables `otlab-dashboard.service`

Idempotent — safe to re-run anytime.

## Auth

HTTP basic auth, defaults `otlab` / `P@ssw0rd!` (the lab-public convention; matches MFCTP and OpenPLC). Override via `dashboard.env` on the Pi:

```ini
DASH_USER=otlab
DASH_PASS=...
```

then `sudo systemctl restart otlab-dashboard`.

## Reboot mechanism

| Target | Path |
|---|---|
| `softplc-2` (self) | `sudo -n /bin/systemctl reboot` (narrow sudoers rule) |
| `softplc-1` | `ssh otadmin@10.20.30.47 sudo systemctl reboot` |
| `honeypot-host` | `ssh otadmin@10.20.30.48 sudo systemctl reboot` |

The HTTP response goes out before the SSH command actually issues the reboot (`subprocess.Popen`, fire-and-forget) so the browser doesn't see the connection drop mid-response.

## Adding cards

Edit `dashboard.py` `HOSTS` / `CONPOTS` dicts and `static/app.js` `ROW_ORDER`. The probe primitives (`ping`, `tcp_probe`, `http_probe`, `modbus_probe`) are reusable. For new card types with different live data, extend the `plcExtras` / `svcsExtras` switch in `app.js`.

## Known limits / future work

- **Polling, not push.** A reboot or service restart shows up on the next 2.5 s probe cycle, not instantly. Good enough for status; if we want sub-second feedback later, switch to Server-Sent Events.
- **No history.** Just a current-state snapshot. No graphs, no trend lines. Keep it simple — Grafana is a different project.
- **No firewall control.** The TP-Link card shows up/down only — its admin UI is on a separate route. Adding a "reboot firewall" button is feasible (TP-Link supports SSH on some models) but vendor-specific.
- **Reboot is "kick the box".** No graceful shutdown of OpenPLC's runtime, no Conpot container draining. Fine for a teaching lab where the goal is fast iteration.
