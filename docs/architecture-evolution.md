# Architecture Evolution

The OTLab is being grown from an early "everything on one segment, dashboard co-located with PLC" shape into a **dual-mode (virtual + physical)** Purdue-aligned architecture. The virtual fabric runs on `l3-mon-01` (Pi 5 16GB + NVMe) via ContainerLab; physical Pis (`l1-plc-01`, `l1-hp-01`) extend it for on-the-wire authenticity.

This doc tracks the phase plan + decisions.

> **V0 baseline — DONE (pre-2026-05-09).** Three Pis on a flat
> `10.20.30.0/24` segment. softplc-1 = master, softplc-2 = outstation +
> dashboard, honeypot-host = Conpot fabric. Dashboard at L3 co-located
> with sensor-sim at L1 — architectural compromise.
>
> **V0.5 naming + repurpose — DONE (2026-05-09).** Naming standardized
> to `<purdue-level>-<role>-<NN>`. softplc-2 → l3-mon-01 (role change
> L1 → L3.5). sensor-sim + DNP3 outstation collapsed onto l1-plc-01
> during the gap. Earlier "managed switch + VLAN" / "USB NIC + iptables
> on host" plans considered, then **superseded** by the team's
> containerized-fabric plan below.
>
> **V1 virtualization MVP — codebase shipped (this commit).**
> ContainerLab topology with firewall + dual virtual OpenPLC + sensor-sim
> + DNP3 + dashboard. Physical Pis untouched, virtual lab runs in
> parallel on `l3-mon-01`. See [`virtualization.md`](virtualization.md).
>
> **V2 DMZ services + physical integration — planned next.** Authentik
> (IdP), Ignition SCADA, Apache Guacamole, Suricata IDS. Bridge physical
> Pis (l1-plc-01, l1-hp-01) into pcn-br0 via macvlan over a USB NIC.
>
> **V3 CODESYS + curriculum — planned.** CODESYS Control SL + CODESYS
> Web HMI containers. Vendor-runtime PLC for the curriculum to contrast
> with OpenPLC.
>
> **Obsolete / dropped:**
> - "Managed switch + VLAN-based segmentation" — replaced by container-bridge segmentation
> - "USB NIC + iptables-on-host as L3 router" — replaced by firewall container between bridges
> - "l1-plc-02 backfill Pi" — replaced by virtual OpenPLC #2 (`plc-2-virt`)

---

## Why the team's containerized plan superseded the earlier paths

I'd been planning toward two earlier architectures:

1. **"Managed switch + VLAN."** Real network segmentation between L1 and L3 via VLAN-tagged physical ports. Pedagogically clean but blocked on procuring a managed switch.

2. **"USB NIC + iptables on l3-mon-01 as router."** Pi acts as the L3 segment break with a second NIC. Worked, but iptables on the host is fragile (lock-yourself-out risk; rules harder to debug than container netns).

The team's containerized plan is **strictly better**:

- **No new hardware.** Bridges + veth pairs in a container netns, fully software-defined.
- **Cleaner blast radius.** Firewall is a container — `docker restart` to fix a bad rule, `containerlab destroy` to wipe topology.
- **Reproducible.** YAML defines the entire topology. Clone, deploy, run anywhere.
- **More authentic.** Real-plant DMZ patterns include dedicated SCADA (Ignition), federated SSO (Authentik), jump-host (Guacamole). All run as containers, all live alongside on l3-mon-01.
- **L1 backfill obsolete.** Multi-PLC scenarios come from virtual fan-out, not buying a 4th Pi.
- **Physical hardware investment preserved.** Phase 2 hardware (relays, AD16, LED strip, pushbutton) still belongs on the physical `l1-plc-01`. The virtual fabric extends it; doesn't replace it.

---

## V0.5 — what landed in the previous commit (recap)

### Before

```
                 Internet (WAN)
                        │
                  TP-Link router
                        │
              ─── Lab segment ─── 10.20.30.0/24
              │       │       │
          softplc-1  softplc-2  honeypot-host
          (L1 PLC)   (L1 PLC +  (L1 deception)
                      L3 ops)
```

### After V0.5

```
              ─── Lab segment ─── 10.20.30.0/24  (transitional flat)
              │             │             │
          l1-plc-01     l3-mon-01     l1-hp-01
          (L1 — also     (L3 — was     (L1 deception
           runs sensor-   softplc-2.    — unchanged)
           sim + DNP3     Now monitoring
           collapsed)     only)
```

Renames + role-change happened in this phase. Services reshuffled from softplc-2 onto l1-plc-01. Earlier `architecture-evolution.md` (now superseded) had Phase 2 = "managed switch break" and Phase 3 = "l1-plc-02 backfill"; both are now obsolete.

---

## V1 — Virtual fabric MVP (this commit)

### Result

```
                          ┌────── tailscale ──────┐
                          │   advertises          │
                          │   192.168.75.0/24     │
                          │   10.20.30.0/24       │
                          └───────────────────────┘
                                     │
            ╔═════════════ l3-mon-01 (Pi 5 16GB) ═════════════╗
            ║                                                  ║
            ║ ┌─ dmz-br0 192.168.75.0/24 (L3.5) ────────────┐ ║
            ║ │ .40 dashboard      (V1)                     │ ║
            ║ │ .10/.20/.30 ignition + authentik + guac (V2)│ ║
            ║ └───────────────────────┬─────────────────────┘ ║
            ║                         │ ◄── Conduit ───       ║
            ║          ┌──────────────┴──────────────┐        ║
            ║          │ fw-dmz-pcn (firewall cont.) │        ║
            ║          └──────────────┬──────────────┘        ║
            ║                         │                        ║
            ║ ┌─ pcn-br0 10.20.30.0/24 (L1/L2) ─────────────┐ ║
            ║ │ .60 plc-1-virt (master)    (V1)              │ ║
            ║ │ .61 plc-2-virt (outstation)(V1)              │ ║
            ║ │ .70 sensor-sim             (V1)              │ ║
            ║ │ .71 dnp3-outstation        (V1)              │ ║
            ║ │ .80/.81 codesys-plc + hmi  (V3)              │ ║
            ║ └─────────────────────────────────────────────┘ ║
            ╚══════════════════════════════════════════════════╝

  l1-plc-01 (Pi 5 + Phase 2 hardware) — physical OpenPLC, untouched in V1
  l1-hp-01  (Pi 3 B+)                 — physical Conpot fabric, untouched in V1
```

### What V1 delivers

- ContainerLab installed on `l3-mon-01`
- Two Linux bridges (`dmz-br0`, `pcn-br0`) created and isolated by netns
- Firewall container enforcing the conduit (DMZ→PCN allowed for known protocols, PCN→DMZ ESTABLISHED only, NAT outbound)
- Two virtual OpenPLC instances + sensor-sim + DNP3 outstation on `pcn-br0`
- Dashboard reachable at `https://l3-mon-01:8000/`
- Physical Pis continue running on their existing flat segment in parallel — V1 doesn't bridge them in yet

### What V1 ships

- `virtual/topologies/otlab.clab.yaml` — main topology
- `virtual/dockerfiles/{sensor-sim,dnp3-outstation,firewall,openplc,dashboard}/` — ARM64 Dockerfiles
- `scripts/install-virtual-lab.sh` — bootstraps containerlab + builds images + deploys topology
- `docs/virtualization.md` — comprehensive architecture doc
- Dashboard Purdue diagram updated to show DMZ + Conduit + PCN with V1/V2/V3 status tags

### V1 verification (after on-Pi execution)

```bash
sudo containerlab inspect -t ~/lab/virtual/topologies/otlab.clab.yaml --format table
# Expect: 7 containers (2 bridges + 1 firewall + 4 services), all "running"

sudo docker exec clab-otlab-fw-dmz-pcn iptables -nvL FORWARD
# Expect: established/related accept, multi-port allow DMZ→PCN, default DROP

# From dashboard container, ping a PCN node:
sudo docker exec clab-otlab-dashboard ping -c1 10.20.30.70  # sensor-sim
# Expect: success (DMZ→PCN allowed)

# From sensor-sim container, ping back to dashboard:
sudo docker exec clab-otlab-sensor-sim ping -c1 192.168.75.40  # dashboard
# Expect: timeout (PCN→DMZ blocked except ESTABLISHED)
```

---

## V2 — DMZ services + physical integration (planned next)

### What V2 adds

```
   dmz-br0 (192.168.75.0/24)
     +─ authentik (server + worker + postgres + redis)   ← IdP/SSO
     +─ ignition  (Maker edition, free)                  ← full SCADA
     +─ guacamole (clientless RDP/SSH/VNC)               ← jump host
     +─ suricata  (IDS — sniffs pcn-br0)                 ← network detection

   pcn-br0 (10.20.30.0/24)
     +─ macvlan to physical USB NIC (eth1 on l3-mon-01)
        +─ physical l1-plc-01 (.47) joins
        +─ physical l1-hp-01 (.48) + Conpot personas join
```

### V2 cutover plan

1. Plug USB NIC into l3-mon-01 (one ethernet adapter, ~$15-25; UGREEN UE300 known-good on Pi OS)
2. Update `virtual/topologies/otlab.clab.yaml` to add `kind: host` link from `pcn-br0` to physical eth1
3. Redeploy: `sudo containerlab deploy -t topologies/otlab.clab.yaml --reconfigure`
4. Add Authentik + Ignition + Guacamole + Suricata containers (uncomment in YAML, redeploy)
5. Wire OIDC: Authentik as the IdP for Ignition Designer + Guacamole + dashboard
6. Verify physical PLCs see virtual PCN: from l1-plc-01, `ping 10.20.30.70` (sensor-sim)
7. Verify Suricata fires on `test-modbus-write.py` from a non-master IP

Estimated time: 3-5 days (most of it is Authentik OIDC + Ignition gateway config; Suricata + macvlan are a few hours each).

### V2 risks

- **Authentik is the most complex piece.** First-time setup needs DB init, blueprint loading, OIDC client config for each service. Plan ~1 day for "Authentik + first OIDC integration."
- **Ignition resource usage.** Default heap 1.5 GB; Pi 5 16GB has headroom but still the biggest single container.
- **macvlan on Pi.** Conpot already uses macvlan on l1-hp-01 — pattern is proven. The new wrinkle is macvlan on the *containerlab* side bridging to physical eth1.

---

## V3 — CODESYS + curriculum (planned)

CODESYS Control SL is the runtime that's actually deployed on real PLCs (Festo, Wago, ABB, B&R, Beckhoff). Adding it lets the curriculum contrast OpenPLC (open-source IEC 61131-3) vs CODESYS (vendor runtime), which is what's actually deployed in plants.

### What V3 adds

- `codesys-plc` container — runs CODESYS Control SL, exposes Modbus + OPC-UA
- `codesys-hmi` container — Web HMI, mounts the runtime
- Curriculum modules:
  - "Vendor PLC fingerprinting" — students enumerate the OPC-UA server, identify it as CODESYS
  - "OPC-UA security" — anonymous access vs Basic128Rsa15 vs SecurityPolicy comparison
  - "Modbus across runtimes" — same FC, different vendor; pcap differences

### V3 risks

- **CODESYS license.** Free 30-day trial works for events; Maker license ~$20/yr personal/educational. Or skip for V3 and add later.
- **ARM64 image.** CODESYS Control for Linux SL has ARM64 builds, confirmed.

---

## V4 / future / optional

- **AI HAT integration** — process anomaly detection (autoencoder on sensor-sim values), alert clustering on Suricata EVE JSON, optional booth camera (Hailo-8L on Pi 5)
- **Take-home topologies** — ContainerLab YAMLs students can deploy on their laptops for at-home practice
- **Vendor coverage expansion** — Honeywell Experion sim, Yokogawa Centum sim, GE Mark VIe sim
- **L4 corp-IT-emulation** — Windows VM with AD, mock email, file shares (QEMU/KVM container or libvirt). Makes the L3.5 ↔ L4 conduit a real teaching artifact
- **OPC UA Pub/Sub + Sparkplug B MQTT** — modern OT pub/sub patterns
- **CI / regression testing** — GitHub Actions runs `containerlab deploy` + the Test Library + verifies expected outcomes; catches regressions before deploy

---

## What we're NOT doing

- **No managed switch with VLANs** — superseded by container-bridge segmentation. Document only; don't buy.
- **No certificate authority** — self-signed certs everywhere. Real PKI out of scope.
- **No secrets manager** — lab convention is intentionally-public passwords. Rotate per event.
- **No MDM / device-lifecycle service** — out of scope for a teaching lab.

---

## Cross-references

- [`virtualization.md`](virtualization.md) — V1+ architecture in depth
- [`naming-schema.md`](naming-schema.md) — canonical hostnames, IPs, services
- [`lab-architecture.md`](lab-architecture.md) — overall system architecture
- [`curriculum.md`](curriculum.md) — teaching modules + scenario walkthroughs
- [`../virtual/topologies/otlab.clab.yaml`](../virtual/topologies/otlab.clab.yaml) — the topology itself
