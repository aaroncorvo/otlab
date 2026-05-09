# Architecture Evolution: L3 Segmentation

The OTLab is being grown out of an early "everything on one segment, dashboard
co-located with PLC" shape into a Purdue-aligned L1/L2/L3 split. This doc tracks
the plan, what's already done, and what remains.

> **Phase 1 (logical segmentation) — DONE as of 2026-05-09.** softplc-2 (the
> Pi 5 + NVMe) was repurposed from L1 PLC role into L3 monitoring role and
> renamed `l3-mon-01`. Services that lived on softplc-2 (sensor-sim, DNP3
> outstation) collapsed onto `l1-plc-01` (formerly softplc-1). The dashboard
> already lived on softplc-2 and stays on the same physical box, but is now
> on a host that does *only* L3 work — Suricata + Guacamole co-deploy here.
>
> **Phase 2 (physical segmentation) — pending managed switch.** The
> 10.20.30.0/24 → 10.20.40.0/24 VLAN split is still on the roadmap; it's
> what makes the L3 separation real (currently it's host-role separation
> on a flat network).
>
> **Phase 3 (backfill) — pending hardware.** A second L1 PLC (`l1-plc-02`)
> restores the Master ↔ Outstation network split. Until that arrives,
> `l1-plc-01` runs both roles.

---

## Phase 1 — Logical L3 separation (done)

### Before

```
                 Internet (WAN)
                        │
                  TP-Link router
                        │
              ─── Lab segment ─── 10.20.30.0/24
              │       │       │
          softplc-1  softplc-2  honeypot-host
          (L1 PLC)   (L1 PLC +   (L1 deception)
                      L2 HMI +
                      L3 ops ←── architectural compromise
                      tailscale subnet router)
                        │
                  Conpot personas
                  (.50/.51/.52)
```

The dashboard ran on softplc-2 alongside sensor-sim and OpenPLC. In Purdue
terms, the operator surface (L3) was co-located with Basic Control (L1) —
exactly what NERC CIP, NIST SP 800-82, and IEC 62443 forbid.

### After (current)

```
                 Internet (WAN)
                        │
                  TP-Link router
                  10.20.30.1
                        │
              ─── Lab segment ─── 10.20.30.0/24
              │             │             │
          l1-plc-01     l3-mon-01     l1-hp-01
          (L1 — was      (L3 — was     (L1 deception)
           softplc-1.     softplc-2.
           Now also       Now monitoring
           runs sensor-   only: dashboard
           sim + DNP3     + Suricata +
           outstation     Guacamole)
           — collapsed
           role)
                        │
                  Conpot personas
                  10.20.30.50/.51/.52
```

L3 monitoring lives on its own host. PLCs do PLC things. The two are
still on the same physical segment (10.20.30.0/24), but **functionally**
separated: l3-mon-01 doesn't run any L1 service. When the managed switch
arrives, the physical separation drops in cleanly without re-arranging
roles.

### Current service map

| Service | Host | Purdue level | Notes |
|---|---|---|---|
| sensor-sim Modbus :5020 | l1-plc-01 | L1 | Moved from softplc-2 |
| sensor-sim DNP3 :20000 | l1-plc-01 | L1 | Moved from softplc-2 |
| OpenPLC master :502 | l1-plc-01 | L1 | Polls 127.0.0.1:5020 (loopback during gap) |
| OpenPLC web UI :8080 | l1-plc-01 | L2 | The L2 surface for now |
| OTLab Dashboard :8000 | l3-mon-01 | L3 | Stayed put physically; isolated from L1 services |
| Suricata IDS | l3-mon-01 | L3 | New (planned, ships next) |
| Apache Guacamole :8443 | l3-mon-01 | L3 | New (planned, ships next) |
| Conpot personas | l1-hp-01 (macvlan) | L1 deception | Unchanged |
| Tailscale subnet router | l3-mon-01 | overlay | Unchanged (still advertises 10.20.30.0/24) |

### Why this is enough teaching value already

Even on a flat network, the **role separation** lets students answer:

- *"What runs at L3 on this lab?"* → `ssh l3-mon-01`. One box.
- *"What runs at L1?"* → `ssh l1-plc-01`. PLC-only.
- *"If the dashboard is compromised, can it directly write Modbus?"* →
  Yes today, because of the flat network. Students see the *gap* between
  role-separation and segment-separation. That gap is the lesson.

Phase 2 closes the gap. Phase 1 makes the gap visible.

### What changed in this commit set (Phase 1 wrap-up)

- ✓ Bulk hostname rename: `softplc-1` → `l1-plc-01`, `softplc-2` →
  `l3-mon-01`, `honeypot-host` → `l1-hp-01`. Legacy aliases retained
  on each Pi via `/etc/hosts` for one transition window.
- ✓ Bootstrap scripts renamed to `bootstrap-l1-plc-role.sh`,
  `bootstrap-l1-hp-role.sh`, `bootstrap-l3-mon-role.sh`.
- ✓ `scripts/wipe-plc-role.sh` — destructive role-reclaim helper, used
  to reclaim softplc-2's Pi for the L3 role.
- ✓ `docs/naming-schema.md` — codifies the canonical naming convention.
- ✓ Dashboard Purdue diagram updated to show l3-mon-01 as **active L3**
  (no longer "planned").
- ✓ All test scripts and scenario files re-pointed at l1-plc-01 (.47)
  for sensor-sim / DNP3 targets (was .49 on softplc-2).

---

## Phase 2 — Physical L3 segment break (pending managed switch)

This is the one that turns role-separation into network-segmentation.

### Target state

```
                 Internet (WAN)
                        │
                  TP-Link router (or managed switch w/ inter-VLAN routing)
                        │
                        ├── 10.20.30.0/24  (Lab — L1)
                        │       │
                        │   l1-plc-01  l1-plc-02  l1-hp-01
                        │   (master)   (outstation -- when backfilled)
                        │       │       │
                        │   Conpot personas .50/.51/.52
                        │
                        └── 10.20.40.0/24  (Operations — L3)
                                │
                            l3-mon-01 (renumbered to .61)
                            ─ OTLab Dashboard :8000      (L3 operator HMI)
                            ─ Apache Guacamole :8443     (L3 jump host)
                            ─ Suricata IDS               (sniffs L1 via SPAN)
                            ─ tailscale subnet router    (advertises both /24s)

           ─── firewall rules between segments ───
            L3 → L1: allow Modbus :502, :5020 reads · DNP3 :20000 ·
                     SSH 22 (limited) · HTTP :8080 (OpenPLC web)
            L1 → L3: allow established/related only (responses)
            L1 → L1: free (PLCs talk to each other)
            L3 writes (FC5/6/15/16): require explicit allowlist
```

### Networking options

**Option A — TP-Link Omada VLANs (preferred when the router supports it)**
- VLAN 10 for Lab (10.20.30.0/24), VLAN 40 for Ops (10.20.40.0/24)
- Tag the l3-mon-01 port as VLAN 40, all PLC ports as VLAN 10
- TP-Link routes between VLANs with explicit ACL rules
- Single physical switch, true segmentation

**Option B — Static routes via TP-Link's secondary LAN**
- Use TP-Link's "guest network" feature as the second segment
- Configure static route between primary LAN and guest LAN
- Limited ACL granularity; sufficient for teaching

**Option C — stay flat with logical segmentation only (current state)**
- l3-mon-01 stays on `10.20.30.49`
- All segmentation enforced via host-based firewalls, not network-layer
- Pedagogically weaker but works with any router
- This is the *current* fallback until A or B is deployable

### Firewall policy (target state)

Implemented via iptables on each host. The role bootstrap scripts ship
default-deny templates with these allow rules:

**On l3-mon-01 (L3):**
```
INPUT  default DROP
OUTPUT default ACCEPT (anything outbound)

# allow tailscale + lo
-A INPUT -i tailscale0 -j ACCEPT
-A INPUT -i lo -j ACCEPT

# allow established (responses to our outbound)
-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# allow operator access to dashboard, Guacamole
-A INPUT -p tcp --dport 8000 -s <operator-allowlist> -j ACCEPT
-A INPUT -p tcp --dport 8443 -s <operator-allowlist> -j ACCEPT
-A INPUT -p tcp --dport 22   -s <operator-allowlist> -j ACCEPT
```

**On l1-plc-01 (L1):**
```
INPUT  default DROP

-A INPUT -i lo -j ACCEPT
-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# Modbus: allow reads from anywhere on the lab segment;
# allow writes only from the legitimate master (self) + l3-mon-01
-A INPUT -p tcp --dport 5020 -s 10.20.30.47 -j ACCEPT  # self (master polls)
-A INPUT -p tcp --dport 5020 -s 10.20.40.0/24 -j ACCEPT # l3-mon-01 mgmt segment

# DNP3: same pattern
-A INPUT -p tcp --dport 20000 -s 10.20.30.47 -j ACCEPT

# SSH for management — only from L3 mgmt segment or tailscale
-A INPUT -p tcp --dport 22 -s 10.20.40.0/24 -j ACCEPT
-A INPUT -p tcp --dport 22 -i tailscale0    -j ACCEPT

# OpenPLC web UI :8080 — only from L3 mgmt segment
-A INPUT -p tcp --dport 8080 -s 10.20.40.0/24 -j ACCEPT

# log + drop everything else
-A INPUT -j LOG --log-prefix 'OTLAB-DROP: '
-A INPUT -j DROP
```

Each policy will be applied via a planned `install-l1-firewall.sh` /
`install-l3-firewall.sh` companion script (independent commit; can
ship before the segment break since host firewalls are valuable on
their own).

### Cutover plan (when switch arrives)

```bash
# 1. Configure managed switch / TP-Link Omada with VLANs 10 + 40.
#    Test inter-VLAN routing with the firewall closed (deny all),
#    then open the policy gradually.

# 2. Renumber l3-mon-01:
ssh otadmin@l3-mon-01.local 'sudo nmtui'   # set 10.20.40.61/24
# Or via /etc/dhcpcd.conf static-IP block.

# 3. Move l3-mon-01's port to VLAN 40 on the switch.

# 4. Update tailscale advertised routes:
ssh otadmin@l3-mon-01.local \
    'sudo tailscale set --advertise-routes=10.20.30.0/24,10.20.40.0/24'

# 5. Apply firewall rulesets (each script self-tests SSH reachability
#    and auto-rolls back if it cuts itself off):
./scripts/install-l3-firewall.sh otadmin@l3-mon-01.local
./scripts/install-l1-firewall.sh otadmin@l1-plc-01.local
./scripts/install-l1-firewall.sh otadmin@l1-hp-01.local

# 6. Update dashboard's HOSTS dict to reflect new IP for l3-mon-01.
./scripts/install-dashboard.sh otadmin@l3-mon-01.local --target-host=l3-mon-01

# 7. Verify:
#    - Dashboard reachable on l3-mon-01:8000 from tailscale
#    - test-modbus-write.py from a non-master IP → fires Suricata + dropped at firewall
#    - link_ok=1 on master ↔ outstation Modbus loop
#    - Guacamole still reaches all PLCs
```

Estimated time: ~45 minutes including switch configuration.

### Risks + rollback (Phase 2)

**Risk: lock yourself out by mis-applying firewall rules.**
- Mitigation: each `install-*-firewall.sh` schedules an `at +1 minute`
  job that flushes the firewall back to ACCEPT-all, cancelled if the
  script verifies SSH still works after applying rules.

**Risk: dashboard's reboot/restart endpoints break because SSH path changes.**
- Mitigation: re-run `install-dashboard.sh` to refresh otuser's SSH
  ControlMaster paths against the new IPs. Already idempotent.

**Risk: tailscale subnet route conflict during cutover.**
- Mitigation: tailscale handles overlapping route announcements
  gracefully (load-balances). Cut over in two phases — add the new
  route advertisement, verify, then remove the old.

**Rollback:** if anything goes wrong, swap the switch configuration back
to a flat untagged network and re-run `install-l3-firewall.sh` with
`--policy=permissive` (planned). Lab is back to current state in
<60 seconds.

---

## Phase 3 — Backfill l1-plc-02 (pending hardware)

When a fourth Pi (or repurposed third Pi) lands, it becomes `l1-plc-02`
and takes back the outstation role. l1-plc-01 reverts to pure-master.

```bash
./scripts/bootstrap-users.sh         <imager>@l1-plc-02.local
./scripts/bootstrap-pi.sh            otadmin@l1-plc-02.local
./scripts/bootstrap-l1-plc-role.sh   otadmin@l1-plc-02.local l1-plc-02
./scripts/install-sensor-sim.sh      otadmin@l1-plc-02.local
./scripts/install-dnp3.sh            otadmin@l1-plc-02.local
# Then on l1-plc-01: re-point OpenPLC master Slave_dev row from
# 127.0.0.1:5020 back to 10.20.30.49:5020 (l1-plc-02).
```

The Master ↔ Outstation network split returns. Suricata sees the
legitimate polls on the wire again. Multi-PLC scenarios become
demonstrable without simulating from outside.

---

## What we're NOT doing in this evolution

- **No IDMZ (L3.5).** Real plants put a DMZ between L3 and L4 — patch
  staging, jump server, AV. For a teaching lab without an L4 corp IT
  zone, the IDMZ has no role. Documented for completeness; not built.
- **No certificate authority.** Self-signed certs continue everywhere.
  Real PKI is out of scope.
- **No secrets manager.** Lab creds remain intentionally-public per
  project convention.

---

## Future evolution (post Phase 2 + 3)

- **Active/standby HA dashboard** — second L3 host for cutover demos.
- **L4 corp-IT-emulation host** — Windows VM with AD, mock email, file
  shares. IDMZ becomes meaningful. Demonstrates "Stuxnet / Industroyer
  cross-zone movement" exercises.
- **OPC UA + MQTT layers** — encrypted, authenticated alternatives to
  plain Modbus/DNP3 on the wire.
- **AI HAT on l3-mon-01** — process-anomaly autoencoder + alert
  clustering + booth camera (see proposal in commit history).

---

## Cross-references

- [naming-schema.md](naming-schema.md) — canonical hostnames, IPs, services.
- [lab-architecture.md](lab-architecture.md) — overall system architecture.
- [curriculum.md](curriculum.md) — teaching modules that lean on this segmentation work.
