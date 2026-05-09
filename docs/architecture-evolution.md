# Architecture Evolution: L3 Segmentation

The current OTLab is genuinely useful as a teaching artifact, but it has one architectural compromise that's worth fixing: **the dashboard runs on softplc-2, the same host as sensor-sim and OpenPLC**. In Purdue model terms, the operator surface (L3) is co-located with Basic Control (L1). Real plants explicitly forbid this — NERC CIP, NIST SP 800-82, IEC 62443 all require segmentation between L1/L2 and L3.

This doc lays out the migration plan: what we have today, what production-aligned looks like, and how we get there with one additional Raspberry Pi.

## Current state (May 2026)

```
                 Internet (WAN)
                        │
                  TP-Link router
                  10.20.30.1
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
                  10.20.30.50/.51/.52
                  (L1 deception, macvlan)
```

**Where each service runs today:**

| Service | Host | Purdue level (per service) | Compromise? |
|---|---|---|---|
| sensor-sim Modbus :5020 | softplc-2 | L1 | — |
| sensor-sim DNP3 :20000 | softplc-2 | L1 | — |
| OpenPLC master :502 | softplc-1 | L1 | — |
| OpenPLC web UI :8080 | softplc-1 + softplc-2 | L2 | acceptable for small plants |
| OTLab Dashboard :8000 | softplc-2 | L3 | **misplaced — should be on a separate host** |
| Conpot personas | honeypot-host (macvlan) | L1 deception | — |
| Tailscale subnet router | softplc-2 | L5↔L1 overlay | acceptable; tailnet is not "in" the Purdue stack |

**The problem with co-location:**
- An attacker who compromises the dashboard has direct access to sensor-sim — no segmentation crossed
- The dashboard's `subprocess.run(['sudo', ...])` for self-reboot has the same blast radius as compromising the PLC itself
- Students see the lab as "PLCs and HMIs are the same box" — directly contradicts what every ICS curriculum teaches

## Target state (one additional Pi)

```
                 Internet (WAN)
                        │
                  TP-Link router
                        │
                        ├── 10.20.30.0/24  (Lab — L1/L2 PLC zone)
                        │       │
                        │   softplc-1  softplc-2  honeypot-host
                        │   (L1 PLC)   (L1 PLC +  (L1 deception)
                        │              sensor-sim
                        │              + DNP3)
                        │              ─────────
                        │                  no L3 services
                        │                  on this host anymore
                        │       │       │
                        │   Conpot personas .50/.51/.52
                        │
                        └── 10.20.40.0/24  (Operations — L3)
                                │
                            ops-host (4th Pi — new)
                            ─ OTLab Dashboard :8000      (L3 operator HMI)
                            ─ Apache Guacamole :8443     (L3 jump host / RDP/VNC/SSH gateway)
                            ─ Suricata IDS               (L3 monitoring, sniffs Lab segment)
                            ─ tailscale subnet router    (replaces softplc-2 as router)

           ─── firewall rules between segments ───
            L3 → L1: allow Modbus :502, :5020 reads · DNP3 :20000 ·
                     SSH 22 (limited) · HTTP :8080 (OpenPLC web)
            L1 → L3: allow established/related only (responses)
            L1 → L1: free (PLCs talk to each other)
            anything → L1 from L3: writes (FC5/6/15/16) require explicit allowlist
```

**Net result:** the dashboard lives one router-hop away from the PLCs. Anything the dashboard does to L1 traverses an enforced policy point. Real segmentation, real teaching artifact.

## What goes on ops-host

**Apache Guacamole** — clientless remote desktop gateway. Browser-based access to:
- SSH into softplc-1, softplc-2, honeypot-host (terminal in browser)
- VNC/RDP into anything that adds a desktop (planned: Velocio Ace 1600, OPC UA test clients)
- OpenPLC web UI as a "tunneled" connection (so students learn the jump-host pattern)

The Guacamole connection profiles teach a real-world pattern: "operators don't SSH directly to PLCs; they connect through a recorded, audited gateway."

**Suricata IDS** — network intrusion detection sniffing the lab segment promiscuously. Loaded with:
- Emerging Threats (ET-OT) ICS rules — community Modbus/DNP3/S7 signatures
- Quickdraw rules from Digital Bond — older but classic ICS rules
- Custom rules surfaced from our scenario walkthroughs (FC5/6 from non-master, DNP3 setpoint manipulation, etc.)

Suricata's EVE JSON output is consumed by a small daemon that pushes alerts into the dashboard's audit log AND a new "IDS Alerts" panel.

**OTLab Dashboard** — moved off softplc-2 entirely. Same code, different host. The `install-dashboard.sh` script accepts `--target-host` so the deployment is one command.

**tailscale subnet router** — replaces softplc-2 in this role. Advertises BOTH `10.20.30.0/24` (Lab) and `10.20.40.0/24` (Ops) so the laptop can reach either segment via tailscale. softplc-2 reverts to a plain tailnet node.

## Networking specifics

Two options for the inter-segment link:

**Option A — TP-Link Omada VLANs (preferred when Aaron's router supports it)**
- Configure VLAN 10 for Lab (10.20.30.0/24) and VLAN 40 for Ops (10.20.40.0/24)
- Tag the ops-host port as VLAN 40, all PLC ports as VLAN 10
- TP-Link routes between VLANs with explicit ACL rules
- Single physical switch, true segmentation

**Option B — Static routes via TP-Link's secondary LAN (alternative)**
- Use TP-Link's "guest network" feature as the second segment
- Configure static route between primary LAN and guest LAN
- Limited ACL granularity; sufficient for teaching

**Option C — ops-host on same physical 10.20.30.0/24 with logical segmentation only (fallback)**
- ops-host gets `10.20.30.100` (operator-workstation range per the addressing plan)
- All segmentation enforced via host-based firewalls, not network-layer
- Pedagogically weaker but works with any router
- This is the "Phase B.1" fallback if VLANs don't materialize

The docs assume Option A as the primary plan; bootstrap scripts support all three.

## Firewall policy (target state)

Implemented via iptables on each host. The `bootstrap-ops-host.sh` script ships a default-deny policy with these allow rules:

**On ops-host (L3):**
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

# allow Suricata's EVE-receiver from internal services
-A INPUT -i lo -j ACCEPT
```

**On softplc-2 (L1):**
```
INPUT  default DROP

-A INPUT -i lo -j ACCEPT
-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# Modbus: allow reads from anywhere on the lab segment;
# allow writes only from softplc-1 (the legitimate master) + ops-host
-A INPUT -p tcp --dport 5020 -s 10.20.30.47 -j ACCEPT  # softplc-1 master
-A INPUT -p tcp --dport 5020 -s 10.20.40.0/24 -j ACCEPT # ops-host (read-only enforced at app layer)

# DNP3: same pattern
-A INPUT -p tcp --dport 20000 -s 10.20.30.47 -j ACCEPT

# SSH for management — only from ops-host or tailscale
-A INPUT -p tcp --dport 22 -s 10.20.40.0/24 -j ACCEPT
-A INPUT -p tcp --dport 22 -i tailscale0    -j ACCEPT

# OpenPLC web UI :8080 — only from ops-host
-A INPUT -p tcp --dport 8080 -s 10.20.40.0/24 -j ACCEPT

# log + drop everything else
-A INPUT -j LOG --log-prefix 'OTLAB-DROP: '
-A INPUT -j DROP
```

Each policy is documented in the `bootstrap-*-firewall.sh` companion scripts (planned, ship alongside the bootstrap). Students see the ruleset in the dashboard's Firewall Rules panel.

## Migration plan (when Pi #4 arrives)

Estimated total time: ~30 minutes start-to-finish.

### Pre-flight (already done in this prep work)
- ✓ `docs/architecture-evolution.md` — this doc
- ✓ `scripts/bootstrap-ops-host.sh` — provisions a fresh Pi for L3 role
- ✓ `scripts/install-guacamole.sh` — Docker-compose deploy
- ✓ `scripts/install-suricata.sh` — apt + ET-OT rules + systemd
- ✓ `scripts/install-dashboard.sh --target-host` — refactored to accept any host
- ✓ Purdue diagram updated to show planned `ops-host` chip

### Day-of (after Pi #4 is on the network)

```bash
# 1. Image fresh Pi OS Lite, hostname OPSHOST, ssh-copy-id
ssh-copy-id <imager-user>@OPSHOST.local

# 2. Standard user model + base packages (same as other Pis)
./scripts/bootstrap-users.sh   <imager-user>@OPSHOST.local
./scripts/bootstrap-ops-host.sh otadmin@OPSHOST.local         # ~10 min

# 3. Deploy services on ops-host
./scripts/install-suricata.sh  otadmin@OPSHOST.local          # ~3 min
./scripts/install-guacamole.sh otadmin@OPSHOST.local          # ~5 min
./scripts/install-dashboard.sh otadmin@OPSHOST.local --target-host=ops-host
                                                              # ~30 s

# 4. Disable dashboard on softplc-2 (becomes pure L1)
ssh otadmin@RASPLC02.local 'sudo systemctl disable --now otlab-dashboard'

# 5. Move tailscale subnet router from softplc-2 to ops-host
ssh otadmin@RASPLC02.local  'sudo tailscale set --advertise-routes='
ssh otadmin@OPSHOST.local   'sudo tailscale set --advertise-routes=10.20.30.0/24,10.20.40.0/24'
# Then approve in tailscale admin console

# 6. Apply firewall rulesets
./scripts/install-l1-firewall.sh otadmin@RASPLC02.local       # softplc-2's L1 policy
./scripts/install-l1-firewall.sh otadmin@RASPLC01.local       # softplc-1's L1 policy
./scripts/install-l1-firewall.sh otadmin@honeypot-host.local  # honeypot's L1 policy
```

### Verification

After cutover, the lab should show:

1. **Dashboard reachable** at `https://<ops-host>:8000/` (and via tailscale)
2. **Guacamole reachable** at `https://<ops-host>:8443/guacamole/`, login `otlab` / `P@ssw0rd!`, with pre-baked connection profiles for SSH-into-each-Pi
3. **Suricata alerts** flowing — run `test-modbus-write` from the dashboard's Test Library, see the alert appear in the new "IDS Alerts" panel within ~10 s
4. **Firewall enforced** — `nc -vz softplc-2:5020` from a non-master IP times out (drops, doesn't reject)
5. **Phase 1 master/slave loop** still healthy (link_ok=1) — softplc-1 → softplc-2 Modbus path is in the allowlist
6. **Purdue diagram** correctly shows ops-host as the L3 chip (no longer planned, now active)

## Risks + rollback

**Risk: lock yourself out by mis-applying firewall rules.**
- Mitigation: each `install-*-firewall.sh` script tests SSH reachability immediately after applying rules; if SSH fails, auto-rolls back via `iptables -F` triggered by an `at +1 minute` job that gets cancelled if the script completes successfully.

**Risk: dashboard's reboot/restart endpoints break because SSH path changes.**
- Mitigation: `install-dashboard.sh` regenerates otuser's SSH keypair on the new host and authorizes it on all 3 PLCs. The SSH ControlMaster sockets get a new ControlPath under the new host's `/home/otuser/lab/dashboard/.ssh-cm/`.

**Risk: tailscale subnet route conflict during cutover.**
- Mitigation: cut subnet routing over in two phases — first add ops-host's route advertisement, then remove softplc-2's. There's a 30-second window where both advertise the same route; tailscale handles this gracefully (load-balances).

**Rollback:** if anything goes wrong, re-enable the dashboard service on softplc-2 (`systemctl enable --now otlab-dashboard`) and re-advertise routes from softplc-2. Lab is back to current state in <60 seconds.

## What we're NOT doing in this evolution

- **No IDMZ (L3.5).** Real plants put a DMZ between L3 and L4 — patch staging, jump server, AV. For a teaching lab without an L4 corp IT zone, the IDMZ has no role. Document only.
- **No physical-VLAN segmentation.** Requires a managed switch (TP-Link Omada / Hirschmann / Cisco IE). Logical segmentation via host firewalls is sufficient for the teaching pattern. Upgrade path is documented.
- **No certificate authority.** Self-signed certs continue everywhere. Real PKI is out of scope.
- **No secrets manager.** Lab creds remain intentionally-public per project convention.

## Future evolution (post-Pi #4)

**With managed switch:** true VLAN segmentation, port mirroring for Suricata, MAC ACLs.

**With second L3 host:** active/standby HA pattern for the dashboard. Real plants have redundant operator HMIs.

**With L4:** add a corp-IT-emulation host (Windows VM with AD, mock email, file shares). IDMZ becomes meaningful. Demonstrates "Stuxnet / Industroyer cross-zone movement" exercises.

**With OPC UA + MQTT layers:** modernize the wire to include encrypted, authenticated alternatives to plain Modbus/DNP3.

For now: ops-host is the next milestone. Aaron returns home → Pi imaged → ~30 min to canonical L3 segmentation.
