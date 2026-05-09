# honeypot/ — Maple Ridge Conpot deployment

Three vendor honeypot personas (Siemens S7-200 / Schneider Modicon M340 / Allen-Bradley CompactLogix) running as Docker containers on a macvlan network. Cover identity is a small municipal water treatment plant; see [`docs/lab-architecture.md`](../docs/lab-architecture.md#the-honeypot-fabric-maple-ridge-treatment-plant) for the full design.

This directory mirrors `~/conpot/compose/` on `l1-hp-01` (the Pi 3 B+). To rebuild the lab on a fresh Pi 3 B+ from scratch, follow the deploy section below.

## Layout

```
honeypot/
├── docker-compose.yml           three services, macvlan parent eth0,
│                                 IPs .50/.51/.52 in 10.20.30.0/24
├── config-overrides/
│   └── conpot.cfg               disables Conpot's external-IP phone-home
├── pysnmp-overrides/            per-vendor SNMP enterprise OID patches
│   ├── __SNMPv2-MIB-siemens.py        Siemens (4196.1.1.5.4)
│   ├── __SNMPv2-MIB-schneider.py      Schneider (3833.1.7.1)
│   ├── __SNMPv2-MIB-allenbradley.py   Rockwell (5188.1.1.18.3)
│   └── __SNMPv2-MIB.py                stock (kept for reference; not bound)
├── templates-siemens/           PS4-CPU01 — pump control persona
│   ├── template.xml             cover identity + databus values
│   ├── template.xml.original    pre-edit baseline (diff to see changes)
│   ├── modbus|s7comm|http|snmp|...  protocol XMLs (per-vendor enable/disable)
│   ├── http/htdocs/             5 SIMATIC-themed HTML pages
│   ├── http/statuscodes/        stock HTTP error templates
│   └── ssl/                     stock Conpot example certs (public, identical
│                                across personas — see .gitignore exception)
├── templates-schneider/         HVAC-M340 — chemical room HVAC persona
└── templates-allenbradley/      CHEM-LGX01 — chlorination dosing persona
```

## Deploy on a fresh Pi 3 B+

Prereqs: Pi OS Lite 64-bit (Bookworm or Trixie), reachable on a network you can reach via SSH. Lab-segment Ethernet (`eth0`) attached to a switch on the lab subnet — for this lab, `10.20.30.0/24`. Hostname doesn't matter to the deployment but `l1-hp-01` is the convention.

```bash
# 1. Install Docker engine + Compose v2 plugin
sudo apt update && sudo apt install -y docker.io
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -sSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
sudo usermod -aG docker $USER  # log out + back in for this to take effect
docker compose version          # confirm v2.x.x

# 2. Drop the deployment files in place
mkdir -p ~/conpot/compose
# copy the contents of THIS honeypot/ directory into ~/conpot/compose/
# (e.g. via scp from your laptop, or `git clone` then cp)

# 3. Create the per-persona log directories with the right ownership
#    Conpot inside the container runs as UID 2000.
cd ~/conpot/compose
mkdir -p logs/siemens logs/schneider logs/allenbradley
sudo chown -R 2000:2000 logs/

# 4. Bring everything up
docker compose up -d
docker compose ps               # all three should be Up (health: starting → healthy)
```

If `docker compose up` errors with `not a directory` on a bind mount, it's the macvlan side-effect of a previous failed mount — clean it up with `sudo rm -rf <bad-path>` and retry.

## Operating

```bash
# status
docker compose ps

# follow the JSON event log for one persona
tail -f ~/conpot/compose/logs/siemens/conpot.json

# restart one persona after a template edit
docker compose restart honeypot-siemens

# tear down (containers + macvlan network)
docker compose down
```

**Macvlan caveat:** the host running Conpot can't reach the macvlan IPs of its own containers (Linux kernel limitation, not a misconfiguration). Always test reachability and run validation probes from a different host on the lab segment — `l3-mon-01` is the natural choice in this lab.

## Validating from another lab host

Quick three-way sanity check (run from `l3-mon-01` or your laptop, anywhere on the lab segment):

```bash
# Each persona returns its vendor-specific SNMP enterprise OID
snmpwalk -v2c -c public 10.20.30.50 1.3.6.1.2.1.1.2.0   # 4196.1.1.5.4 (Siemens)
snmpwalk -v2c -c public 10.20.30.51 1.3.6.1.2.1.1.2.0   # 3833.1.7.1 (Schneider)
snmpwalk -v2c -c public 10.20.30.52 1.3.6.1.2.1.1.2.0   # 5188.1.1.18.3 (Rockwell)
```

For the full validation battery (HTTP page sweep, port-mix, Modbus process snapshot), see [`docs/lab-architecture.md`](../docs/lab-architecture.md#validation-tests-cross-pi).

## What's NOT in this directory

- `logs/` — runtime forensic capture, gitignored. Generated on first `docker compose up`.
- `__conpot__*` directories — Conpot's per-instance scratch space inside `logs/`.
- The macvlan network itself (created by `docker compose up`).
- The Conpot Docker image (`ghcr.io/telekom-security/conpot:24.04.1`) — pulled on first up.

## Reference

- Conpot upstream: <https://github.com/mushorg/conpot>
- T-Pot Docker image (what we use): <https://github.com/telekom-security/tpotce>
- Detailed build narrative: [`docs/lab-architecture.md`](../docs/lab-architecture.md)
