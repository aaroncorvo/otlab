# OTLab — Build From Scratch

End-to-end walkthrough for standing up the OTLab. **You only need one Raspberry Pi** to get the full lab running. Additional Pis + hardware are optional expansion — described in Stage 2+ below.

> **Reading order**: Stage 1 is the linear playbook. Most users stop there. Stages 2-4 are optional expansion. For architecture background read [`docs/lab-architecture.md`](lab-architecture.md) and [`docs/virtualization.md`](virtualization.md).

---

## Stage 1 — Single-Pi lab (required, ~30 minutes)

This gets you the **full lab** running on one Raspberry Pi. Everything in the dashboard's Overview, IDS, Firewall, DHCP, Live Data, and Teaching tabs works. The only things this stage doesn't deliver are real GPIO (Phase 2 hardware) and physical Conpot honeypots — both are optional Stage 2+ expansions.

### Prerequisites

**Hardware:**
- Raspberry Pi 5 (16 GB recommended; 8 GB works for the virtual fabric, tight once you add Suricata + admin UIs). Pi 4 8 GB would also work, ARM64.
- SD card (32 GB+) or NVMe via Waveshare PCIe HAT
- Power: **official Pi 5 27 W USB-C PSU** (cheap PSUs cause boot loops under container build load)
- WiFi for internet uplink (the Pi's onboard wlan0)

**Operator workstation:**
- macOS or Linux laptop with `ssh`, `rsync`, `git`
- Same network as the Pi (so mDNS `<host>.local` resolves)
- This repo cloned locally

**Network:**
- Just operator WiFi for the Pi to reach the internet during image builds. No special routing, no separate router, no VLAN — the lab fabric lives entirely inside the Pi's network namespace.

### 1. Image the Pi

Use the official Raspberry Pi Imager. Pick **Raspberry Pi OS Lite (64-bit Bookworm)** and in "Advanced options":

- Hostname: `l3-mon-01`
- Configure WiFi for your operator network
- Enable SSH with your username + password
- Set locale + keyboard

Boot the Pi, wait ~60 seconds for first-boot to finish, then on your operator workstation:

```sh
ssh-copy-id <imager-user>@l3-mon-01.local
```

### 2. Bootstrap users

Lays down `otadmin` (NOPASSWD sudo, what scripts use) and `otuser` (non-privileged runtime user). Disables cloud-init, fixes WiFi powersave.

```sh
./scripts/bootstrap-users.sh <imager-user>@l3-mon-01.local
```

After this, you'll log in as `otadmin@l3-mon-01.local`.

### 3. Bootstrap the Pi

Installs Docker, the lab's Python venv, tailscale, the apt deps needed by ContainerLab. ~10 minutes.

```sh
./scripts/bootstrap-pi.sh           otadmin@l3-mon-01.local
./scripts/bootstrap-l3-mon-role.sh  otadmin@l3-mon-01.local
```

When tailscale prompts (one-time), follow the printed URL to authorize the device. Skip if you don't want tailscale — the lab works fine without it.

### 4. Deploy the lab fabric

The big one. Builds 7 Docker images, lays down host bridges + NetworkManager pinning, deploys the ContainerLab topology.

```sh
./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
```

First run takes **~30 minutes** because of the OpenPLC source build (matiec compile). Subsequent runs reuse the Docker layer cache and are fast.

#### What this lays down on the Pi

| Path | What |
|---|---|
| `/home/otuser/lab/virtual/` | ContainerLab topology + Dockerfiles |
| `/home/otuser/lab/dashboard/` | Dashboard source (Flask + JS) |
| `/home/otuser/lab/plc/` | sensor-sim, dnp3, modbus-master sources |
| `/usr/local/sbin/otlab-bridges-up` | idempotent bridge setup helper |
| `/etc/systemd/system/otlab-bridges.service` | runs the helper at boot |
| `/etc/otlab/bridge-attach.conf` | per-NIC attach config (single-Pi default: eth0 only) |
| `/etc/NetworkManager/conf.d/99-otlab-unmanaged.conf` | keeps NM out of clab veths |
| `/var/lib/otlab/mm-state/` | shared volume — modbus-master ↔ dashboard |
| `/var/lib/otlab/fw-state/` | shared volume — firewall ↔ dashboard |
| `/var/lib/otlab/ssh/` | dashboard SSH keypair (for optional physical Pi expansion) |
| `/etc/otlab-bootstrap-info` | install timestamp + commit hash |

#### Verify

After the install completes, you should see **9 containers running**:

```sh
ssh otadmin@l3-mon-01.local 'sudo containerlab inspect -t /home/otuser/lab/virtual/topologies/otlab.clab.yaml --format table'
```

Browse to `https://l3-mon-01:8000/` and log in as `otlab` / `P@ssw0rd!`. The Overview tab should show 13 cards (4 net, 5 PCN services, 3 lab infrastructure, 1 mon) all green.

### 5. Install Suricata + admin UIs (recommended)

These aren't strictly required, but they make the lab significantly richer for teaching.

```sh
./scripts/install-suricata.sh   otadmin@l3-mon-01.local
./scripts/install-cockpit.sh    otadmin@l3-mon-01.local
./scripts/install-portainer.sh  otadmin@l3-mon-01.local
./scripts/install-edgeshark.sh  otadmin@l3-mon-01.local
```

After install:

| URL | What |
|---|---|
| `https://l3-mon-01:9090/` | Cockpit — Linux server admin |
| `https://l3-mon-01:9443/` | Portainer CE — Docker UI |
| `http://l3-mon-01:5001/`  | EdgeShark — live packet capture in browser |

The dashboard's **IDS tab** lights up as soon as Suricata starts producing alerts.

### Smoke test

End-to-end check from your operator workstation:

```sh
# 1. Dashboard reachable, all cards green
curl -sk -u otlab:P@ssw0rd! https://l3-mon-01:8000/api/status \
  | python3 -c "import json,sys; d=json.load(sys.stdin); \
                cards=d['cards']; up=sum(1 for c in cards.values() if c.get('up')); \
                print(f'cards up: {up}/{len(cards)}')"

# 2. Modbus master polling (should be 10.0/s, 0 errors)
ssh otadmin@l3-mon-01.local 'sudo cat /var/lib/otlab/mm-state/last.json' \
  | python3 -m json.tool

# 3. Suricata catches a FC6 write
ssh otadmin@l3-mon-01.local 'sudo docker exec clab-otlab-dashboard python3 -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient(\"10.20.30.70\", port=5020, timeout=2); c.connect()
c.write_register(0, 0xCAFE, device_id=0); c.close()"'
sleep 2
ssh otadmin@l3-mon-01.local 'sudo grep "OTLAB-1004" /var/log/suricata/eve.json | tail -1' \
  | python3 -c "import json,sys; e=json.loads(sys.stdin.read()); \
                print(f'IDS alert: {e[\"alert\"][\"signature\"]} @ {e[\"timestamp\"][:19]}')"
```

If all three pass: **your single-Pi lab is production-ready for cohort use.** Browse to the dashboard and start teaching.

---

## Stage 2 — Add a physical OpenPLC Pi (optional)

Adds a second Raspberry Pi running real OpenPLC with real GPIO. Joins the lab's PCN segment via a USB Ethernet NIC plugged into the L3 Pi.

**Why bother?** The single-Pi lab teaches protocol-level ICS concepts beautifully, but every PLC, sensor, and outstation is virtual. For "real wires, real timing, real GPIO" demos — pushbuttons that fire relays, AD16 indicators that light up — you need a physical PLC. This stage adds one.

### Stage 2 prerequisites

- **Second Raspberry Pi** — Pi 5 (4/8 GB) recommended. Pi 4 works.
- **USB Ethernet adapter for l3-mon-01** — Realtek RTL8157 5GbE verified; any cdc_ncm-class adapter works
- **Ethernet switch** — any 5+ port unmanaged switch. If your lab switch will have non-lab devices on it, **see the "VLAN isolation note" below** before plugging in
- **Optional Phase 2 hardware** — Freenove HAT, AD16 24V indicators, LED strip, pushbutton, 24V PSU (for the GPIO demos)

### Stage 2 setup

```sh
# 1. Image l1-plc-01 with hostname `l1-plc-01`, configure WiFi, enable SSH
ssh-copy-id <imager-user>@l1-plc-01.local

# 2. Bootstrap (creates otadmin + otuser, posture)
./scripts/bootstrap-users.sh <imager-user>@l1-plc-01.local
./scripts/bootstrap-pi.sh    otadmin@l1-plc-01.local

# 3. Bootstrap as L1 PLC role (installs OpenPLC, Phase 2 driver, sets static IP .47)
OPENPLC_PASSWORD='P@ssw0rd!' \
  ./scripts/bootstrap-l1-plc-role.sh otadmin@l1-plc-01.local l1-plc-01
```

Then **on the L3 Pi**, plug in the USB Ethernet adapter and enable PCN bridge attach:

```sh
ssh otadmin@l3-mon-01.local '
    sudo sed -i "s/^# eth1=pcn-br0/eth1=pcn-br0/" /etc/otlab/bridge-attach.conf
    sudo /usr/local/sbin/otlab-bridges-up'
```

Plug an Ethernet cable from the USB NIC into your lab switch. Plug `l1-plc-01`'s eth0 into the same switch. Both should now share the PCN segment (10.20.30.0/24).

Then re-deploy with physical mode enabled — the dashboard will light up cards for the new Pi:

```sh
ssh otadmin@l3-mon-01.local '
    sudo sed -i s/OTLAB_PHYSICAL: \"0\"/OTLAB_PHYSICAL: \"1\"/ /home/otuser/lab/virtual/topologies/otlab.clab.yaml
    sudo bash -c "cd /home/otuser/lab/virtual && containerlab deploy -t topologies/otlab.clab.yaml --reconfigure"'

# Authorize the dashboard's SSH key on the new Pi so system-health probes work
PHYSICAL_PIS="otadmin@l1-plc-01.local" \
  ./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
```

The dashboard's Overview tab now shows `l1-plc-01` with OpenPLC web UI status + system metrics.

### VLAN isolation note

If your lab switch is shared with non-lab devices (other devices on the same Ethernet network), the `dhcp-pcn` container will hand out leases (`10.20.30.200-.250`) to **any** device that broadcasts a DHCPDISCOVER. Three ways to handle this:

| Approach | When to use |
|---|---|
| **Dedicated unmanaged switch** *(simplest)* | $15-30. Only lab Pis on it. Total isolation. |
| **VLAN on managed switch** *(production-like)* | If you have a managed switch (GS305E/GS308E etc.), tag eth1's port + the Pi ports onto a lab VLAN. |
| **Disable dynamic scope** *(software-only)* | Edit topology so DHCP only serves reservations. See the per-host DHCP_HOSTS config. |

The default config has `eth1=pcn-br0` commented OUT — explicitly opt-in. Only enable when you've decided which isolation approach you're using.

---

## Stage 3 — Add a physical Conpot honeypot Pi (optional)

Adds a third Pi (Pi 3 B+ is plenty) running three vendor-themed Conpot honeypot personas: Siemens S7-200, Schneider M340, Allen-Bradley CompactLogix. Each persona presents a vendor-coherent HTTP admin page and speaks vendor-canonical protocols (S7comm, Modbus, EtherNet/IP CIP).

### Stage 3 setup

```sh
# 1. Image l1-hp-01 (Pi 3 B+ or 4 — Conpot is lightweight)
ssh-copy-id <imager-user>@l1-hp-01.local
./scripts/bootstrap-users.sh   <imager-user>@l1-hp-01.local

# 2. Bootstrap as L1 honeypot role
./scripts/bootstrap-l1-hp-role.sh otadmin@l1-hp-01.local
```

Plug `l1-hp-01`'s eth0 into the lab switch (same PCN segment).

Re-authorize the dashboard SSH key (so honeypot log probes work):

```sh
PHYSICAL_PIS="otadmin@l1-plc-01.local otadmin@l1-hp-01.local" \
  ./scripts/install-virtual-lab.sh otadmin@l3-mon-01.local
```

The dashboard's Overview tab now shows three additional cards (`siemens-PS4`, `schneider-M340`, `rockwell-CHEM`) with live persona telemetry.

---

## Stage 4 — Add an RS485 Modbus device (optional)

Adds a real industrial Modbus RTU sensor (temp/humidity, energy meter, flow sensor, etc.) via a Waveshare RS485-to-Ethernet gateway. The gateway translates Modbus TCP queries to RTU on the serial side, so the lab's `modbus-master` can poll the sensor as if it were a TCP slave.

See [`docs/lab-architecture.md` § "RS485 expansion"](lab-architecture.md) for the gateway configuration walkthrough.

---

## Stage 5 — Add wireless IoT (optional)

Adds ESP32 boards on the lab WiFi acting as small remote Modbus servers (vendor-IIoT-monitoring-device persona). Demonstrates "remote sensor → wireless → on-prem network → polled by master" attack surface.

Requires:
- A WiFi access point bridged onto the PCN segment (or a separate AP serving its own subnet with firewall routing)
- ESP32 firmware in [`plc/esp32/iot-1/`](../plc/esp32/iot-1/) (already written)
- Arduino IDE on your dev machine to flash the firmware

See [`docs/arduino-setup.md`](arduino-setup.md) for the IDE setup.

---

## Common gotchas (all stages)

**Pi voltage alarm on the L3 host.** The Pi 5 + NVMe HAT + USB NIC draws close to the rated 27 W of the official Pi 5 PSU. `throttled=0x50000` means "previously throttled" — fine if `vcgencmd get_throttled` now shows `0x0`. Use the **official Pi 5 27 W PSU** and a quality USB-C cable.

**ContainerLab can't create bridges.** `sudo modprobe br_netfilter` and ensure `iptables-legacy` is the alternative (`sudo update-alternatives --set iptables /usr/sbin/iptables-legacy`). The bootstrap-l3-mon-role.sh script handles this.

**Host kernel got a 10.20.30.x lease from `dhcp-pcn`.** NetworkManager's pinning didn't apply. Check `/etc/NetworkManager/conf.d/99-otlab-unmanaged.conf` exists and contains the clab veth blocklist. Restart NM: `sudo systemctl restart NetworkManager`.

**Dashboard cards all show DOWN.** The dashboard's `ping()` shells out to `/bin/ping` — if the Dockerfile ever loses `iputils-ping`, every card flips false. Rebuild dashboard image and redeploy.

**eth1 NO-CARRIER.** USB NIC detected but no Ethernet link. Check the cable, the switch port LED, and the switch power. `sudo ip link set eth1 down && sudo ip link set eth1 up` to bounce.

**clab destroy/deploy cycle wiped firewall iptables.** Expected — clab restarts the container, exec hooks re-run, `start-firewall.sh` re-applies the policy. If you `docker restart` the firewall outside of clab, the netns persists but exec hooks don't re-run, so iptables is empty. Always use `containerlab deploy --reconfigure` or full destroy/deploy.

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
```

---

## What's next

Once the lab is up, see:
- [`docs/dashboard-tour.md`](dashboard-tour.md) — what each of the 7 tabs does + how to use it for teaching
- [`docs/curriculum.md`](curriculum.md) — lessons + Attack/Detect/Defend exercises (curriculum is the next chunk of work — **looking for contributors!** see [CONTRIBUTING.md](../CONTRIBUTING.md))
- [`docs/phase-1-modbus-loop.md`](phase-1-modbus-loop.md) — first lesson walkthrough
