# OTLab — Build From Scratch

End-to-end walkthrough for standing up the OTLab from three fresh Pi
SD cards. The lab runs in **dual mode**: one Pi virtualizes the entire
DMZ + PCN fabric as containers (ContainerLab); two physical Pis extend
it onto real wire for on-the-wire authenticity.

> **Reading order**: this is the linear playbook. For architecture
> background read [`docs/lab-architecture.md`](lab-architecture.md) and
> [`docs/virtualization.md`](virtualization.md). For the address plan
> see [`docs/naming-schema.md`](naming-schema.md). For the dashboard's
> tabs see [`docs/dashboard-tour.md`](dashboard-tour.md).

## What you'll have at the end

```
┌────────────────────────── Internet ──────────────────────────┐
│                          (operator wlan / GL-AR150 WAN)       │
└──────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                │      l3-mon-01 (Pi 5 16GB) │
                │      ─────────────────────  │
                │   Containerlab fabric:      │
                │   ┌─────────────────────┐  │
                │   │  dmz-br0  (.75/24)  │──┼── eth0 (DMZ extended to lab switch)
                │   │   .1   firewall     │  │
                │   │   .2   dhcp-dmz     │  │
                │   │   .40  dashboard    │  │
                │   └──────────┬──────────┘  │
                │              │              │
                │   ┌──────────▼──────────┐  │
                │   │  pcn-br0  (.30/24)  │──┼── eth1 USB NIC (PCN to lab switch)
                │   │   .1   firewall     │  │
                │   │   .2   dhcp-pcn     │  │
                │   │   .43  modbus-master│  │
                │   │   .60  plc-1-virt   │  │
                │   │   .61  plc-2-virt   │  │
                │   │   .70  sensor-sim   │  │
                │   │   .71  dnp3-outstn  │  │
                │   └─────────────────────┘  │
                │   + Suricata (host)         │
                │   + Cockpit / Portainer /   │
                │     EdgeShark (admin UIs)   │
                └─────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                │      Lab Ethernet switch    │
                └──┬───────────────────────┬──┘
                   │                       │
            ┌──────▼──────┐         ┌──────▼──────┐
            │ l1-plc-01    │         │ l1-hp-01    │
            │ Pi 5         │         │ Pi 3 B+     │
            │ ─────────    │         │ ─────────    │
            │ .47/24       │         │ .48/24       │
            │ OpenPLC :502 │         │ Conpot fabric│
            │ + :8080 UI   │         │  .50 siemens │
            │ + Phase 2 hw │         │  .51 schneider│
            │              │         │  .52 rockwell│
            └──────────────┘         └──────────────┘
```

The OTLab Dashboard at `https://l3-mon-01:8000/` becomes the operator
surface — 7 tabs with live process state, IDS alerts, firewall rules,
DHCP leases, and curriculum exercises.

---

## Prerequisites

### Hardware
- **l3-mon-01**: Raspberry Pi 5 16GB + NVMe SSD (Waveshare PCIe HAT
  recommended). 8GB works for the virtual fabric alone but is tight
  once Suricata + Cockpit + Portainer + EdgeShark are added.
- **l1-plc-01**: Raspberry Pi 5 (4/8 GB OK). Optional Phase 2 hardware
  (Freenove HAT, AD16 indicators, LED strip, pushbutton, 24V PSU).
- **l1-hp-01**: Raspberry Pi 3 B+ (or 4). Just runs Conpot containers.
- **USB Ethernet adapter** for l3-mon-01 (Realtek RTL8157 5GbE
  verified; any cdc_ncm-class adapter works). Plugs into the lab
  switch as `eth1` to bridge physical Pis into `pcn-br0`.
- **Lab Ethernet switch**: any unmanaged 5+ port switch is fine. If
  you'll have unrelated devices on the same switch, see the "VLAN
  isolation note" below before plugging eth1 in.
- **WAN gateway**: any router providing DHCP + internet to the
  operator's wlan + (optionally) the lab switch's WAN side. The
  GL-AR150 with stock OpenWrt is what this lab was built against;
  any consumer router works.

### Operator workstation
- macOS or Linux
- `ssh`, `rsync`, `git`
- This repo cloned locally
- Network access to whatever subnet the Pis end up on (operator wlan
  or tailscale tailnet)

### A note about WiFi vs. Ethernet on l3-mon-01

`l3-mon-01` reaches the internet via **wlan0** (operator wlan). `eth0`
is a bridge port for the DMZ — it has no IP. `eth1` is the same for
the PCN. This is intentional: the host keeps a separate management
plane (wlan0 + tailscale) so the lab fabric can be torn down and
rebuilt without losing administrative access.

---

## Step 1 — Image fresh Pi OS on each SD card

Use the official Raspberry Pi Imager. For each Pi:

| Pi | OS | Hostname during imaging | Notes |
|---|---|---|---|
| l3-mon-01 | Pi OS Lite (64-bit Bookworm) | `l3-mon-01` | NVMe boot recommended |
| l1-plc-01 | Pi OS Lite (64-bit Bookworm) | `l1-plc-01` | |
| l1-hp-01 | Pi OS Lite (64-bit Bookworm) or default | `l1-hp-01` | Pi 3 B+ — keep it lean |

In the Imager's "Advanced options":
- Set hostname (above)
- Configure wifi for your operator network
- Enable SSH with the imager's username + password (you'll replace
  this in Step 2)
- Set locale + keyboard

> Legacy hostnames (`RASPLC01`, `RASPLC02`, `honeypot-host`) on
> existing Pis still work via `/etc/hosts` aliases; canonical names
> are the new ones above. See `docs/naming-schema.md`.

Boot each Pi, wait ~60 s for first-boot, then on your operator
workstation:

```sh
ssh-copy-id <imager-user>@l3-mon-01.local
ssh-copy-id <imager-user>@l1-plc-01.local
ssh-copy-id <imager-user>@l1-hp-01.local
```

`<imager-user>` is whatever you set in Pi Imager. If mDNS isn't
working, replace `.local` with the IP from your router.

---

## Step 2 — Bootstrap users on every Pi

Lays down `otadmin` (NOPASSWD sudo, what scripts use) and `otuser`
(non-privileged runtime user). Disables cloud-init, fixes wifi
powersave.

```sh
./scripts/bootstrap-users.sh <imager-user>@l3-mon-01.local
./scripts/bootstrap-users.sh <imager-user>@l1-plc-01.local
./scripts/bootstrap-users.sh <imager-user>@l1-hp-01.local
```

After this you'll log in as `otadmin@<pi>.local` everywhere.

---

## Step 3 — Bootstrap the L3 monitoring host (the virtualization host)

```sh
./scripts/bootstrap-pi.sh           otadmin@l3-mon-01.local
./scripts/bootstrap-l3-mon-role.sh  otadmin@l3-mon-01.local
```

This installs Docker, the lab's Python venv, tailscale, and the apt
deps needed by the containerlab fabric. ~10 minutes.

When tailscale prompts (one-time per host), follow the printed URL to
authorize the device against your tailnet. You'll do this for each Pi.

---

## Step 4 — Deploy the virtualized lab on l3-mon-01

This is the big one. Builds 7 Docker images, lays down host bridges +
NetworkManager pinning, deploys the containerlab topology.

```sh
./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
```

First run takes **~30 minutes** because of the OpenPLC source build
(matiec compile). Subsequent runs reuse the Docker layer cache and are
fast.

### What this lays down on the Pi

| Path | What |
|---|---|
| `/home/otuser/lab/virtual/` | ContainerLab topology + Dockerfiles |
| `/home/otuser/lab/dashboard/` | Dashboard source (Flask + JS) |
| `/home/otuser/lab/plc/` | sensor-sim, dnp3, modbus-master sources |
| `/usr/local/sbin/otlab-bridges-up` | idempotent bridge setup helper |
| `/etc/systemd/system/otlab-bridges.service` | runs the helper at boot |
| `/etc/otlab/bridge-attach.conf` | per-NIC attach config (see Step 5) |
| `/etc/NetworkManager/conf.d/99-otlab-unmanaged.conf` | keeps NM out of clab veths |
| `/var/lib/otlab/mm-state/` | shared volume — modbus-master ↔ dashboard |
| `/var/lib/otlab/fw-state/` | shared volume — firewall ↔ dashboard |
| `/var/lib/otlab/dhcp-{dmz,pcn}.{leases,reservations,log}` | shared DHCP state |
| `/etc/otlab-bootstrap-info` | install timestamp + commit hash |

### Verify

After the install completes, you should see 9 containers running:

```sh
ssh otadmin@l3-mon-01.local 'sudo containerlab inspect -t /home/otuser/lab/virtual/topologies/otlab.clab.yaml --format table'
```

All should show `running` state. Browse to `https://l3-mon-01:8000/`
and log in as `otlab` / `P@ssw0rd!`. The Overview tab should show 13
cards (4 net, 5 PCN services, 3 lab infrastructure, 1 mon) all green.

The IDS / Firewall / DHCP tabs are populated as soon as data flows.

---

## Step 5 — Decide whether to bridge eth1 onto the PCN

This is the **VLAN-isolation decision point**.

**The physical-Pi integration story**: if you bridge-port `eth1` (USB
NIC) into `pcn-br0`, the physical Pis on the lab switch share an L2
segment with the virtual containers. modbus-master polls the physical
PLC, the dashboard sees both physical and virtual hosts as cards,
Suricata sniffs cross-segment attacks. All the V2.x integration tests
pass.

**The DHCP cross-talk risk**: when `eth1` is in `pcn-br0`, the
`dhcp-pcn` container will hand out leases (`10.20.30.200-.250`) to
**any** device on the lab switch that broadcasts a DHCPDISCOVER —
including unrelated devices that don't belong on the lab subnet.

Pick one:

| Posture | Config | When to use |
|---|---|---|
| **Virtual only** (default) | `# eth1=pcn-br0` (commented out) | Lab switch shared with non-lab devices, no VLANs. dhcp-pcn won't see physical traffic, so no risk. |
| **Bridged + accept risk** | `eth1=pcn-br0` | Lab switch is dedicated to OTLab gear, OR you have VLAN isolation, OR you don't mind dhcp-pcn handing out leases. |
| **Bridged + dedicated switch** | `eth1=pcn-br0` + a separate $15 5-port unmanaged switch | Cleanest. Lab Pis only on this switch. |

To enable physical bridging:

```sh
ssh otadmin@l3-mon-01.local '
    sudo sed -i "s/^# eth1=pcn-br0/eth1=pcn-br0/" /etc/otlab/bridge-attach.conf
    sudo /usr/local/sbin/otlab-bridges-up'
```

To disable later:

```sh
ssh otadmin@l3-mon-01.local '
    sudo sed -i "s/^eth1=pcn-br0/# eth1=pcn-br0/" /etc/otlab/bridge-attach.conf
    sudo /usr/local/sbin/otlab-bridges-up'
```

The helper is fully idempotent and detaches NICs that are no longer
in the config.

---

## Step 6 — Install the companion admin UIs

```sh
./scripts/install-cockpit.sh    otadmin@l3-mon-01.local
./scripts/install-portainer.sh  otadmin@l3-mon-01.local
./scripts/install-edgeshark.sh  otadmin@l3-mon-01.local
```

After these:

| URL | What |
|---|---|
| `https://l3-mon-01:9090/` | Cockpit — Linux server admin (services, networking, terminal) |
| `https://l3-mon-01:9443/` | Portainer CE — Docker UI (live container logs, exec, restart) |
| `http://l3-mon-01:5001/`  | EdgeShark — live packet capture in browser, click any veth to start sniffing |

First-time login:
- Cockpit: `otadmin` / your `otadmin` password
- Portainer: enter a 12+ char admin password on first visit (Portainer enforces this)
- EdgeShark: no auth (lab convention)

---

## Step 7 — Install Suricata IDS

```sh
./scripts/install-suricata.sh otadmin@l3-mon-01.local
```

Runs as a host service (not containerized) sniffing on `pcn-br0`. The
default rule pack ships with OTLAB-1003 / 1004 / 1005 / 1006 (Modbus
write FC5/6/15/16 from non-master) and OTLAB-4001 (SSH brute force).

After install, alerts land in `/var/log/suricata/eve.json`. The
dashboard's IDS tab tails this file.

To trigger a test alert:

```sh
ssh otadmin@l3-mon-01.local 'sudo docker exec clab-otlab-dashboard python3 -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient(\"10.20.30.70\", port=5020, timeout=2); c.connect()
c.write_register(0, 0xCAFE, device_id=0); c.close()"'
```

That's a FC6 write from the dashboard (a non-master IP) → OTLAB-1004
should fire within a second.

---

## Step 8 — Bootstrap the physical OpenPLC Pi (l1-plc-01)

```sh
./scripts/bootstrap-pi.sh                  otadmin@l1-plc-01.local
OPENPLC_PASSWORD='P@ssw0rd!' \
  ./scripts/bootstrap-l1-plc-role.sh       otadmin@l1-plc-01.local  l1-plc-01
```

~15-20 minutes (matiec compile on first run). Lays down OpenPLC
service, Phase 2 hardware drivers, sensor-sim + dnp3-outstation
fallback services.

Post-install: OpenPLC web UI at `http://l1-plc-01.local:8080/`,
default `openplc` / `P@ssw0rd!`. The static IP `10.20.30.47/24` is set
on `eth0` via NetworkManager (with `ipv4.never-default yes` so the
Pi keeps tailscale + apt working independently of the lab fabric).

---

## Step 9 — Bootstrap the honeypot Pi (l1-hp-01)

```sh
./scripts/bootstrap-l1-hp-role.sh otadmin@l1-hp-01.local
```

~3-5 minutes. Pulls Conpot Docker images for Siemens / Schneider /
Allen-Bradley personas, lays down `docker-compose.yaml`, starts the
three personas as macvlan children at `.50/.51/.52`.

Post-install: HTTP UIs at `http://10.20.30.{50,51,52}/` (each a
different vendor-themed admin page). All speak protocol on the
canonical port (`102` for S7, `502` for Modbus, `44818` for EtherNet/IP)
when their respective Conpot template is enabled.

---

## Step 10 — Add DHCP reservations for the physical devices

The DHCP servers default to handing out leases for the dynamic scope
(`.150-.199` on DMZ, `.200-.250` on PCN). To pin known devices to
their canonical IPs (so a fresh SD card image doesn't grab a random
lease), add `dhcp-host=` reservations.

The default config already has reservations for the physical Pis +
Conpot personas. To add a new one, edit `virtual/topologies/otlab.clab.yaml`,
find the `dhcp-pcn` (or `dhcp-dmz`) node, and add a line under
`DHCP_HOSTS`:

```yaml
        DHCP_HOSTS: |
          2c:cf:67:4f:d3:09,l1-plc-01,10.20.30.47
          b8:27:eb:78:85:77,l1-hp-01,10.20.30.48
          # ... add your new device here:
          aa:bb:cc:dd:ee:ff,my-device,10.20.30.55
```

To get a device's MAC: from the firewall container, after the device
has been on the wire briefly:

```sh
sudo docker exec clab-otlab-fw-dmz-pcn ip neigh show 10.20.30.55
```

After editing, re-deploy:

```sh
ssh otadmin@l3-mon-01.local 'sudo containerlab deploy -t /home/otuser/lab/virtual/topologies/otlab.clab.yaml --reconfigure'
```

The DHCP tab in the dashboard will show all reservations + active
leases.

---

## Verifying everything works

End-to-end smoke test from your operator workstation:

```sh
# 1. Dashboard reachable, all cards green
curl -sk -u otlab:P@ssw0rd! https://l3-mon-01:8000/api/status \
  | python3 -c "import json,sys; d=json.load(sys.stdin); \
                cards=d['cards']; up=sum(1 for c in cards.values() if c.get('up')); \
                print(f'cards up: {up}/{len(cards)}')"

# 2. Modbus master polling (should be 10.0/s, 0 errors)
ssh otadmin@l3-mon-01.local 'sudo cat /var/lib/otlab/mm-state/last.json' \
  | python3 -m json.tool

# 3. DHCP reservations rendered
ssh otadmin@l3-mon-01.local 'sudo cat /var/lib/otlab/dhcp-pcn.reservations'

# 4. Cross-segment Modbus test (only if eth1 is bridged)
ssh otadmin@l3-mon-01.local 'sudo docker exec clab-otlab-modbus-master python3 -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient(\"10.20.30.47\", port=502, timeout=3)
print(\"physical PLC reachable:\", c.connect()); c.close()"'

# 5. Suricata catches a FC6 write
ssh otadmin@l3-mon-01.local 'sudo docker exec clab-otlab-dashboard python3 -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient(\"10.20.30.70\", port=5020, timeout=2); c.connect()
c.write_register(0, 0xCAFE, device_id=0); c.close()"'
sleep 2
ssh otadmin@l3-mon-01.local 'sudo grep "OTLAB-1004" /var/log/suricata/eve.json | tail -1' \
  | python3 -c "import json,sys; e=json.loads(sys.stdin.read()); \
                print(f'IDS alert: {e[\"alert\"][\"signature\"]} @ {e[\"timestamp\"][:19]}')"
```

If all five pass, the lab is production-ready for cohort use.

---

## Common gotchas

**Pi voltage alarm on the L3 host.** The Pi 5 + NVMe HAT + USB NIC
draws close to the rated 27 W of the official Pi 5 PSU. Throttled
status `0x50000` means "previously throttled" — fine if running
benchmarks shows current state at `0x0`. Use the official 27 W PSU
and a quality USB-C cable. Cheaper PSUs cause boot loops under load.

**ContainerLab can't create bridges.** `sudo modprobe br_netfilter`
and ensure `iptables-legacy` is the alternative (`sudo
update-alternatives --set iptables /usr/sbin/iptables-legacy`).
The bootstrap-l3-mon-role.sh script handles this.

**Host kernel got a 10.20.30.x lease from dhcp-pcn.** If you see
this, NetworkManager's pinning didn't apply. Check
`/etc/NetworkManager/conf.d/99-otlab-unmanaged.conf` exists and
contains the clab veth blocklist. Restart NM:
`sudo systemctl restart NetworkManager`.

**Dashboard cards all show DOWN.** The dashboard's `ping()` shells
out to `/bin/ping` — if the Dockerfile ever loses `iputils-ping`,
every card flips false. Rebuild dashboard image and redeploy.

**eth1 NO-CARRIER.** USB NIC detected but no Ethernet link. Check
the cable, the switch port LED, and the switch power. `sudo ip link
set eth1 down && sudo ip link set eth1 up` to bounce.

**clab destroy/deploy cycle wiped firewall iptables.** Expected —
clab restarts the container, exec hooks re-run, and `start-firewall.sh`
re-applies the policy. If you `docker restart` the firewall outside
of clab, the netns persists but exec hooks don't re-run, so iptables
is empty. Always use `containerlab deploy --reconfigure` or full
destroy/deploy.

---

## Disaster recovery

```sh
# Nuke the topology + start fresh (preserves images)
ssh otadmin@l3-mon-01.local 'sudo bash -c "
    cd /home/otuser/lab/virtual
    containerlab destroy -t topologies/otlab.clab.yaml --cleanup
    containerlab deploy  -t topologies/otlab.clab.yaml"'

# Nuke EVERYTHING (images too — forces full rebuild)
ssh otadmin@l3-mon-01.local 'sudo bash -c "
    cd /home/otuser/lab/virtual
    containerlab destroy -t topologies/otlab.clab.yaml --cleanup
    docker rmi $(docker images -q otlab/*) 2>/dev/null || true
    docker system prune -af"'
./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local

# Reset just one container (keep state)
ssh otadmin@l3-mon-01.local 'sudo containerlab deploy -t /home/otuser/lab/virtual/topologies/otlab.clab.yaml --reconfigure'
```

---

## What's next

Once the lab is up, see:
- [`docs/dashboard-tour.md`](dashboard-tour.md) — what each tab does + how to use it for teaching
- [`docs/curriculum.md`](curriculum.md) — lessons + Attack/Detect/Defend exercises
- [`docs/phase-1-modbus-loop.md`](phase-1-modbus-loop.md) — first lesson walkthrough
