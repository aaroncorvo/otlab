# Network Topology — l3-mon-01 NIC layout

This doc describes how physical NICs on `l3-mon-01` (Pi 5 16GB + NVMe) map
to the virtual ContainerLab fabric. It exists because that mapping is the
single most confusing thing about the lab — the same fabric runs on three
different hardware shapes (current Pi, future multi-NIC board, paper-only
all-virtual variant), and it's easy to lose track of which interface is
which without a diagram.

## Conventions

| Term | Meaning |
| --- | --- |
| **L3.5 / DMZ** | `192.168.75.0/24` — Industrial DMZ (operator surface, dashboard, jump host) |
| **L1/L2 / PCN** | `10.20.30.0/24` — Process Control Network (PLCs, sensors, honeypots) |
| **WAN** | Whatever's upstream of the GL-AR150 (lab internet, hotel wifi, etc.) |
| **dmz-br0** | Linux bridge in the host netns. L3 endpoint of the DMZ. |
| **pcn-br0** | Linux bridge in the host netns. L3 endpoint of the PCN. |
| **fw-dmz-pcn** | The L3.5↔L1/L2 firewall container. Holds `192.168.75.1` and `10.20.30.1`. |

## Current state (May 2026)

Single Pi, two NICs (one onboard + one USB), wlan0 for tailscale + apt.
The GL-AR150 is the WAN gateway; we plug into its LAN side.

```
                              ┌─────────────────────────────────────────────┐
                              │              l3-mon-01 (Pi 5)               │
                              │                                             │
   physical lab switch ──┬─── │ eth0 ──┐                                    │
   (Netgear, untagged)   │    │        │ bridge-port                        │
                         │    │        ▼                                    │
                         │    │  dmz-br0 (no IP — pure L2 bridge)           │
                         │    │        ▲                                    │
                         │    │        │ veth                               │
                         │    │  fw-dmz-pcn:eth1 (192.168.75.1/24)          │
                         │    │  fw-dmz-pcn:eth2 (10.20.30.1/24)            │
                         │    │        │ veth                               │
                         │    │        ▼                                    │
                         │    │  pcn-br0 (no IP — pure L2 bridge)           │
                         │    │        ▲                                    │
                         │    │        │ bridge-port                        │
   physical lab switch ──┼─── │ eth1 ──┘  (USB Realtek RTL8157 5GbE)        │
   (Netgear, untagged)   │    │                                             │
                         │    │ wlan0 ── tailscale + apt (default route)    │
                         │    │                                             │
                         │    └─────────────────────────────────────────────┘
                         │
                         ├─── physical L1 Pis (l1-plc-01 .47, l1-hp-01 .48)
                         │     reachable via pcn-br0 ↔ eth1 ↔ switch
                         │
                         └─── GL-AR150 LAN side ── WAN (hotel wifi / lab
                                                       drop)
```

Key points:
- **eth0 + eth1 have no IP**. They're pure bridge-ports. The bridges are
  the L3 endpoints, and the firewall container is what holds the gateway
  IPs (.1 in each zone).
- **DMZ extends to the physical wire** via eth0. Anything plugged into
  the Netgear switch on an untagged port lands in the DMZ broadcast
  domain alongside the dashboard, and gets a DHCP lease from `dhcp-dmz`
  (range `.150-.199`).
- **PCN extends to the physical wire** via eth1. Same story — physical
  Pis on the lab switch share the broadcast domain with virtual
  PCN containers, get leases from `dhcp-pcn` (range `.200-.250`).
- **wlan0 carries the host's default route** (tailscale + apt). It is
  *not* the fabric's WAN — that's the GL-AR150 reachable through eth0.
  In the future-state design wlan0 goes away.
- **The firewall container is the L3 boundary**, period. Nothing on the
  host's network namespace routes between zones.

## Address plan summary

```
DMZ  192.168.75.0/24     gateway 192.168.75.1   (firewall container)
                          DNS     192.168.75.1   (firewall container, dnsmasq)
                          DHCP    192.168.75.2   (dhcp-dmz container)
                          scope   192.168.75.150 -- 192.168.75.199
                          static  192.168.75.40  dashboard
                                 (extend physical-DMZ devices in 75.10–.39)

PCN  10.20.30.0/24       gateway 10.20.30.1     (firewall container)
                          DNS     10.20.30.1     (firewall container, dnsmasq)
                          DHCP    10.20.30.2     (dhcp-pcn container)
                          scope   10.20.30.200 -- 10.20.30.250
                          static  10.20.30.43    modbus-master (virtual)
                                  10.20.30.47    l1-plc-01 (physical)
                                  10.20.30.48    l1-hp-01  (physical)
                                  10.20.30.50    Conpot Siemens persona
                                  10.20.30.51    Conpot Schneider persona
                                  10.20.30.52    Conpot Rockwell persona
                                  10.20.30.60    plc-1-virt (OpenPLC)
                                  10.20.30.61    plc-2-virt (OpenPLC)
                                  10.20.30.70    sensor-sim
                                  10.20.30.71    dnp3-outstation
```

See `docs/naming-schema.md` for the canonical list.

## Future state — multi-NIC board, no wlan

When `l3-mon-01` migrates to a board with several onboard NICs, the
mapping becomes (interface names are nominal — actual `ethN` will depend
on the hardware):

```
   eth0 (WAN)        ── upstream router / WAN gateway (NAT egress)
   eth1 (DMZ)        ── bridge-port'd into dmz-br0
   eth2 (PCN)        ── bridge-port'd into pcn-br0
   eth3 (PLC-direct) ── future: dedicated NIC for a hardware PLC
   wlan0             ── REMOVED
```

The fabric on top is unchanged: same containers, same IPs, same firewall
policy. Only `/usr/local/sbin/otlab-bridges-up` (the helper invoked by
`otlab-bridges.service`) needs to be updated with the new interface
names. Everything else is interface-agnostic.

## Why the firewall container holds the gateway IPs

A reasonable alternative is to give `dmz-br0` and `pcn-br0` IPs directly
on the host and have the host kernel do L3 forwarding. We rejected that:

- **Teaching artifact**: the firewall *being a container* makes the
  conduit visible. Students can `docker exec` into it, read
  `/opt/otlab-firewall/policy.iptables`, watch packets traverse it.
- **Clean failure modes**: stop the firewall container and the zones
  are isolated — exactly the property real Purdue-aligned shops want.
- **Identical on every shape**: works the same on the current Pi, the
  future multi-NIC board, and a hypothetical all-virtual deployment
  with no physical NICs at all.

## DHCP / DNS data flow

```
   client (e.g. l1-plc-01 physical)
       │ DHCPDISCOVER (broadcast) on PCN segment
       ▼
   dhcp-pcn container (10.20.30.2)
       │ DHCPOFFER advertising:
       │   gateway 10.20.30.1   (firewall)
       │   DNS     10.20.30.1   (firewall)
       │
   client gets a lease, e.g. 10.20.30.215
       │
       │ later: dig example.com
       ▼
   firewall container :53 (dnsmasq forwarder)
       │ recurses to 1.1.1.1 / 8.8.8.8 via WAN uplink
       ▼
   answer back to client
```

DNS queries are logged inside the firewall at
`/var/log/dnsmasq-fw.log` — useful as a "DNS exfil at the firewall"
teaching artifact.

## How to verify it's working

After `containerlab deploy`:

```sh
# DHCP — watch a fresh client get a lease
docker exec dhcp-dmz cat /var/log/dnsmasq.log
docker exec dhcp-pcn cat /var/log/dnsmasq.log

# DNS — resolve through the firewall from inside any virtual container
docker exec clab-otlab-modbus-master nslookup example.com 10.20.30.1

# Bridge ports — confirm physical NICs are bridge-port'd
bridge link show
ip -br link show master dmz-br0
ip -br link show master pcn-br0

# Firewall policy — confirm DNS rules are present
docker exec clab-otlab-fw-dmz-pcn iptables -nvL INPUT --line-numbers
```
