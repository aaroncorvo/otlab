# OTLab — MikroTik classroom router config

Configures a MikroTik (RouterOS 7.x) as the upstream L3 device for an
OTLab classroom. One paste, then fill in the 21 MAC addresses for your
specific gear.

## What it configures

- **Bridge + interfaces**: `bridge-classroom` on `ether2-5`, `ether1` = WAN
- **DHCP server**: classroom segment `192.168.10.0/24`, gateway `.1`
- **21 reservations**: teacher `.10`, students `.101`–`.120` (MAC→IP)
- **60 static routes**: 3 fabric layers × 20 students (`10.{75,30,50}.N.0/24` → student's classroom IP)
- **Firewall**:
  - Teacher → any student fabric: **allow**
  - Students → teacher (Loki :3100): **allow**
  - Student ↔ student (classroom AND fabric): **DENY**
  - Students → internet: **allow** (NAT'd on WAN egress)
- **DNS forwarder**, **NAT/MASQUERADE** on WAN

## Hardware tested

| Model | RouterOS | Notes |
|---|---|---|
| RB5009UG+S+IN | 7.13 | Recommended — gigabit, low power, fanless |
| hAP ax² (C52iG-5HaxD2HaxD) | 7.13 | Cheap travel option, has wifi if you need it |
| CCR2004-1G-12S+2XS | 7.13 | Overkill but fine; SFP-only WAN |

Older RouterOS 6.x works with minor syntax tweaks — mostly `/ip firewall
address-list` semantics. Stick to 7.x where possible.

## How to deploy

### Step 1 — capture MACs

Boot all 21 Pis with the OTLab Pi Imager image. Plug into the MikroTik.
They'll all get random leases from the spillover pool. Get the
hostname→MAC map:

```
[admin@MikroTik] > /ip dhcp-server lease print where status=bound
 # ADDRESS         MAC-ADDRESS       HOST-NAME           SERVER
 0 192.168.10.200  D8:3A:DD:11:22:33 otlab-teacher       otlab-dhcp
 1 192.168.10.201  D8:3A:DD:11:22:34 otlab-student-01    otlab-dhcp
 2 192.168.10.202  D8:3A:DD:11:22:35 otlab-student-02    otlab-dhcp
 ...
```

Copy the MAC column. You'll paste these into the .rsc file in the next
step.

### Step 2 — fill in the .rsc

Open `20-student-classroom.rsc` in any editor. Find/replace each
`AA:BB:CC:DD:EE:NN` placeholder with the real MAC from step 1:

| Placeholder | Replace with | Pi |
|---|---|---|
| `AA:BB:CC:DD:EE:10` | teacher's MAC | otlab-teacher → .10 |
| `AA:BB:CC:DD:EE:01` | student-01's MAC | otlab-student-01 → .101 |
| `AA:BB:CC:DD:EE:02` | student-02's MAC | otlab-student-02 → .102 |
| `AA:BB:CC:DD:EE:0A` | student-10's MAC | otlab-student-10 → .110 |
| `AA:BB:CC:DD:EE:15` | student-20's MAC | otlab-student-20 → .120 |

(Placeholders use hex 01–15 to keep them grep-able. Note: there's no
`:00` and the teacher uses `:10`.)

### Step 3 — paste into the router

Two options:

**A. Upload as a file** (recommended for the final config):
```
Files → Upload → 20-student-classroom.rsc
System → Scripts → Add → Source: `:import 20-student-classroom.rsc`
Run the script
```

**B. Paste sections directly in the terminal**:
```
[admin@MikroTik] > /
[paste the entire .rsc contents]
```

The script is idempotent on first run but will error on repeat (e.g.
"address already exists"). To re-run cleanly, first remove the OTLab
config:

```
/ip dhcp-server lease remove [find comment~"otlab"]
/ip route remove [find comment~"student-"]
/ip firewall filter remove [find comment~"otlab"]
/ip firewall address-list remove [find list~"otlab"]
/ip dhcp-server remove [find name=otlab-dhcp]
/ip pool remove [find name=otlab-pool]
/ip address remove [find comment~"OTLab"]
/interface bridge remove [find name=bridge-classroom]
```

### Step 4 — verify

```
# All 21 leases bound
/ip dhcp-server lease print where server=otlab-dhcp

# 60 student routes
/ip route print where comment~"student-"

# Firewall rules (should show 7 OTLab rules)
/ip firewall filter print where comment~"otlab"
```

From the teacher host (`192.168.10.10`):
```sh
ping 192.168.10.105       # student-05 classroom — should succeed
ping 10.30.5.1            # student-05 PCN fabric — should succeed
curl http://10.75.5.40/   # student-05 dashboard — should respond
```

From a student host (e.g. `otlab-student-03`):
```sh
ping 192.168.10.107       # student-07 classroom — should TIME OUT (blocked)
ping 10.30.7.1            # student-07 PCN fabric — should TIME OUT (blocked)
ping 192.168.10.10        # teacher — should succeed
curl http://192.168.10.10:3100/ready   # teacher SIEM — should return "ready"
```

## What goes wrong + first place to look

| Symptom | Fix |
|---|---|
| Student gets wrong IP | MAC in `.rsc` doesn't match Pi's actual MAC. Re-run step 1, edit, re-paste reservations |
| Teacher can ping student classroom IP but not student fabric (10.30.N.X) | Student's Pi is up but clab fabric not deployed. SSH into student, run `sudo containerlab inspect -t /home/otuser/lab/virtual/topologies/otlab.clab.yaml` — should show all containers running |
| Student-to-student still works | Firewall rules not loaded. `/ip firewall filter print where comment~"otlab"` should show 7 rules; if missing, re-paste section 5 |
| Logs not arriving at teacher SIEM | Promtail not running on student (`sudo systemctl status promtail-otlab` on the Pi) OR firewall blocks :3100 — check rule "otlab: student → teacher SIEM" exists and is above the deny rules |
| Pi gets DHCP lease but no internet | NAT not configured. `/ip firewall nat print` should show the masquerade rule. Check `ether1` (or your WAN interface) is in the `WAN` interface list |

## See also

- `../fortigate/` — FortiGate equivalent of this config *(planned PR)*
- `../../docs/classroom-installer.md` — full classroom install walkthrough
- `../../docs/classroom-network.md` — network map + design rationale
