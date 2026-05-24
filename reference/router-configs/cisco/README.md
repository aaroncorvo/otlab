# OTLab — Cisco Catalyst 2960 classroom switch config

Companion to the MikroTik router config. The Cisco does **L2 switching
only**; routing + DHCP + ACLs live on the MikroTik.

## Hardware tested

| Model | IOS | Notes |
|---|---|---|
| Catalyst 2960-24TT-L | 15.0(2)SE | The OG. Works fine. |
| Catalyst 2960-24PS-L | 15.0(2)SE11 | PoE, 24 FastEthernet ports — fine for Pi traffic |
| Catalyst 2960-X-24PS-L | 15.2(7)E | Gigabit, what most labs end up with |

Older 2960 variants with FastEthernet (`Fa0/N`) work — find/replace
`Gi1/0/` → `Fa0/` throughout the `.ios` file.

## Port layout

| Port | Role | VLAN |
|---|---|---|
| `Gi1/0/1` – `Gi1/0/20` | Student Pi #1–#20 (eth0, mgmt) | 10 (access) |
| `Gi1/0/21` | Teacher Pi (eth0, mgmt + SIEM) | 10 (access) |
| `Gi1/0/22` – `Gi1/0/23` | Spare classroom (laptops, AP) | 10 (access) |
| `Gi1/0/24` | Uplink trunk → MikroTik router | 10 (trunk) |

## What it configures

- **VLAN 10** (otlab-classroom) — single L2 broadcast domain for all Pis + teacher + uplink
- **Access-port hardening** — BPDU guard, portfast, port-security (max 2 MACs per port — catches accidental hub/dock chains)
- **Management Vlan 10** — switch gets DHCP from MikroTik (set a `.50` reservation in the MikroTik for it)
- **SSH** — port 22, local `otlab/P@ssw0rd!` user (rotate per event)
- **SNMP RO** — `otlab-ro` community, restricted by ACL to teacher IP only
- **No HTTP, no CDP, no auto-config** — lab hygiene

## What's NOT configured (yet)

- **VLAN 200 (OT shared)** — for student Pi eth1 PCN extension. Commented in the .ios file; activate when you add a second switch dedicated to OT-side traffic.
- **PoE** — if you have a PoE variant (`PS` / `PD` in the model name), add `power inline auto` per port. Adds ~30 W per Pi.
- **Inter-VLAN ACLs** — Cisco is L2 only. ACLs live on the MikroTik.

## Loading the config

### Option A — console paste (simplest, 1 switch)

1. Console cable, 9600,8,N,1
2. `enable` then `configure terminal`
3. Open `24-port-classroom.ios` in your editor
4. Paste section-by-section (sections are marked with `! ──` headers)
5. `end` then `write memory`

### Option B — TFTP (for repeatable deploys)

```sh
# On your laptop (TFTP server)
brew install dnsmasq                 # or sudo apt install tftpd-hpa
mkdir /tmp/tftp
cp reference/router-configs/cisco/24-port-classroom.ios /tmp/tftp/
sudo dnsmasq --no-daemon --port=0 --enable-tftp --tftp-root=/tmp/tftp

# On the switch (from `enable` prompt)
copy tftp://<your-laptop-ip>/24-port-classroom.ios running-config
write memory
```

## Verification

```
! All 23 access ports on VLAN 10
otlab-classroom-sw# show vlan brief
VLAN Name              Status    Ports
---- ----------------- --------- -------------------------------
10   otlab-classroom   active    Gi1/0/1, Gi1/0/2, ... Gi1/0/23
1    default           active

! Trunk uplink to MikroTik is up
otlab-classroom-sw# show interfaces trunk
Port      Mode       Encapsulation  Status   Native vlan
Gi1/0/24  trunk      802.1q         trunking 1

! Student ports show "connected" + their MAC
otlab-classroom-sw# show interfaces status
Port    Name                            Status      Vlan   Duplex Speed Type
Gi1/0/1 OTLab student Pi eth0 (mgmt/c   connected   10     a-full a-1000 10/100/1000BaseTX
Gi1/0/2 ...                             connected   10     ...
```

From the teacher Pi:

```sh
ping 192.168.10.105     # student-05 — should succeed
ssh otadmin@192.168.10.105 'hostname'    # → otlab-student-05
```

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| Student port goes `err-disabled` shortly after plug-in | BPDU guard tripped (Pi spat a BPDU during cloud-init) | `int gi1/0/N` → `shutdown` → `no shutdown`. Disable BPDU guard if it keeps happening (`no spanning-tree bpduguard enable`) |
| Pi doesn't get DHCP | Trunk uplink to MikroTik not up | `show interfaces trunk` — verify Gi1/0/24 in trunking state |
| `port-security violation` log entries | Pi has changed MAC (e.g. you reseated the Cruiser board's mini-PCIe NIC) | Clear with `clear port-security sticky interface gi1/0/N`; the next 2 MACs will re-stick |
| Teacher panel sees fewer than 20 Pis | Some Pis on wrong ports or wrong VLAN | `show mac address-table` — verify all 20 Pi MACs appear, all on VLAN 10 |
| Switch loses its own DHCP lease | `Vlan10` SVI lost its IP after a reload | `int vlan10` → `shutdown` → `no shutdown`. Or set a static IP if DHCP keeps flapping. |

## Adding the second switch (when ready)

For the OT-shared VLAN 200 (Pi eth1 extension):

1. **Cheapest path** — any 24-port unmanaged Gigabit switch. Plug all 20 Pi eth1 ports + a trunk back to the Cisco. The Cisco needs:
   ```
   vlan 200
    name otlab-ot-shared
    exit
   interface Gi1/0/22       ! re-purpose a spare
    description Trunk to OT-shared switch
    switchport mode trunk
    switchport trunk allowed vlan 10,200
    exit
   ```
2. **Or another managed Cisco** — same recipe, second `.ios` file. We'd ship `reference/router-configs/cisco/24-port-ot-shared.ios` at that point.
3. **MikroTik update** — add VLAN 200 sub-interface for routing. The MikroTik `.rsc` would gain a `/interface vlan` entry.

This is on the roadmap; not built yet because you only have one switch today.

## See also

- `../mikrotik/` — MikroTik router config (DHCP, routing, ACL)
- `../../docs/classroom-network.md` — full network map
- `../../docs/classroom-installer.md` — install walkthrough
