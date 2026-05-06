# Maple Ridge ICS Training Lab

Build documentation — working draft
Last updated: 2026-05-06

## What this is

A multi-Pi industrial cybersecurity training lab built for DEF CON-style teaching. The lab presents a coherent water treatment plant ("Maple Ridge Treatment Plant, Springfield Water Authority") with multiple subsystems running across mixed-vendor PLC equipment, supplemented by a multi-vendor honeypot fabric. Students get to scan, fingerprint, attack, and capture forensics against a believable OT environment — without endangering any real water utility.

The lab is being built in phases. Phase 0 (provisioning) and the full honeypot deployment are complete. Phase 1 (real PLC integration) is the next milestone.

## Architecture overview

### Physical hosts

Three Raspberry Pi hosts make up the current lab. All three share two networks: the lab segment (`10.20.30.0/24`) on physical Ethernet, and the management segment (`YOUR-MGMT-NETWORK/24`) over WiFi for SSH and apt access.

| Host | Hardware | Role | Lab IP | Mgmt IP |
|---|---|---|---|---|
| `softplc-1` (RASPLC01) | Pi 5 8GB + Freenove GPIO Terminal Block HAT | Soft PLC #1 (OpenPLC) | 10.20.30.111 | RASPLC01.local |
| `softplc-2` (RASPLC02) | Pi 5 8GB + Waveshare PCIe-to-M.2 USB HAT+ + KingSpec NVMe + Waveshare 3-CH Relay HAT | Soft PLC #2 (OpenPLC), attack workstation | 10.20.30.49 | RASPLC02.local |
| `honeypot-host` | Pi 3 Model B+ | Conpot Docker host | 10.20.30.48 | honeypot-host.local |

A Pi 1 was originally considered for honeypot duty but deprioritized due to RAM constraints; the Pi 3 B+ is the right floor for running three Conpot containers simultaneously (~360MB RSS of 905MB available).

### Field hardware on hand

This is the hardware inventory accumulated for later phases. Most of it isn't wired yet.

- **PLCs:** Velocio Ace 1600 (real PLC for Phase 3), 2× Arduino UNO R3 (planned as Modbus RTU slaves)
- **Microcontrollers:** 3× Lonely Binary ESP32-S3 N16R8 Gold (firmware platforms for Phase 2), TI MSP430 LaunchPad, BastWAN
- **HMI / I/O:** 4×4 keypad, 2× uxcell AD16 24V dual-color indicators, 2× uxcell 12mm pushbuttons, 12V LED strip, hiBCTR 4-Channel Relay Shield for UNO, Simple PLC Development Module
- **Serial / Modbus:** 10× DiGiYes MAX485, Waveshare RS485-to-ETH (B) Modbus Gateway, CP2102 USB-TTL
- **Power:** NVVV EDR-120-12 (12V/10A), OMCH EDR-120-24 (on order), Jienk 2-in/10-out distribution blocks (×2)
- **Sensors:** ELEGOO 37-in-1 Sensor Kit V2.0
- **Mounting:** Tecmojo 4U 10" Mini Server Rack, GeeekPi DIN rail bracket

### Physical I/O plan per soft-PLC

The two soft-PLCs each carry a different HAT with a distinct field-I/O role. Field wiring is a future phase (no physical loads connected as of 2026-05-06), but the architecture below is locked in so software, addressing, and future hardware purchases stay aligned.

#### `softplc-1` — Freenove GPIO Terminal Block HAT (I/O concentrator)

The Freenove is a passthrough — every BCM pin on a screw terminal, no relays, no LEDs of its own. softplc-1's role is *reading* field inputs (and driving low-current logic-level outputs if anything calls for it).

| Function | Wiring | OpenPLC variable |
|---|---|---|
| Pushbutton 1 | uxcell 12mm momentary, dry contact between screw terminal `IO17` and `GND`. INPUT_PULLUP in software so pin reads HIGH at rest, LOW when pressed. | `%IX1.0` (TBD — depends on OpenPLC's Pi hardware-layer pin map) |
| Reserved for sensors from the ELEGOO kit (DHT11 temp/humidity, photoresistor, PIR, etc.) | Direct GPIO + voltage divider where needed | TBD |

The pushbutton's built-in 3–6 V LED ring is unused for now; can later be lit from a Pi GPIO via 220 Ω resistor as a "system ready" indicator if we want.

softplc-1 has no way to switch high-current loads directly — anything visual it commands has to either (a) ride on softplc-2's relay HAT via Modbus or (b) wait for a UNO with a relay shield to come online in Phase 3.

#### `softplc-2` — Waveshare 3-CH Relay HAT (actuator host)

3 SPDT relays (HLS8L-DC5V-S-C, 5 A contacts), photo-isolated, each with COM/NO/NC screw terminals. GPIO mapping per the HAT's silkscreen (printed in wiringPi numbers; converted to BCM here):

| HAT label | wiringPi | BCM | Active level | Planned load |
|---|---|---|---|---|
| CH1 (P25) | wiringPi 25 | BCM 26 | active-LOW | AD16 dual-color indicator (24 V): SPDT trick — COM=+24 V, NC=red, NO=green. Single relay, mutual-exclusion guaranteed by physics. Default state (relay de-energized) lights red, so "system off" = red. |
| CH2 (P28) | wiringPi 28 | BCM 20 | active-LOW | LED strip (12 V): SPST gate. COM=+12 V from the strip's own brick, NO=strip + lead. We just interrupt the existing 12 V circuit. |
| CH3 (P29) | wiringPi 29 | BCM 21 | active-LOW | Spare for whatever Phase 2+ adds (siren, second indicator, fan, etc.). |

The Relay_JMP six-pin block on the HAT must stay populated for the Pi to actually drive the coils — they ship installed but get bumped during handling. First debug step if a relay won't click.

OpenPLC's "Raspberry Pi" hardware target picks up these GPIOs via its built-in driver. We'll need a custom hardware layer to override the default pin map so `%QX0.0..%QX0.2` map to BCM 26/20/21 (active LOW). That's part of Phase 2.

#### Why this asymmetry

It's intentional and pedagogical. Real OT plants are full of mixed-vendor, mixed-capability gear: one PLC has remote I/O, another sits next to its actuators, a third reads from a smart instrument over fieldbus. The lab mirrors this by giving each soft-PLC a different physical-I/O surface. Phase 2's wiring will demonstrate "PLC-A reads a button, sends a Modbus write to PLC-B, PLC-B's relay closes, light comes on" — the entire SCADA cause-and-effect chain on real hardware.

`sensor-sim` (Phase 1) doesn't go away when real I/O comes online. It stays as:
- a no-rack-required demo data source for travel / desk testing
- a teaching example of "what a remote sensor over Modbus looks like"
- a controllable input for stress tests (kill it to verify alarm behavior; modify the waveforms to test edge cases)

### Network design

Two network segments in parallel:

**Lab segment** — `10.20.30.0/24`, on `eth0`

Address allocation:

| Range | Purpose |
|---|---|
| `.10-.19` | PLCs |
| `.20-.29` | Vendor PLCs (real branded gear) |
| `.30-.39` | HMIs |
| `.40-.49` | IoT devices |
| `.50-.59` | Honeypots |
| `.60-.69` | Attacker tooling |
| `.100+` | Workstations |

**Lab WiFi** — SSID `MFCTP`, password `P@ssw0rd!`. Bridged onto the same Layer 2 broadcast domain as `eth0`, so wireless clients lease addresses out of the same `10.20.30.0/24` pool from the same DHCP server. Verified 2026-05-06 with ESP32 #1 leasing `10.20.30.204` and reaching all wired hosts directly. Credentials are deliberately public — the lab is a teaching environment and attendees are given the codes as part of the exercise; rotate per DEF CON event.

**Management segment** — `YOUR-MGMT-NETWORK/24`, on `wlan0` (home WiFi). Used for SSH from the laptop and `apt`/`pip` installs only. All PLC, honeypot, attack, and IIoT traffic stays on the lab segment (wired or via MFCTP).

**Modbus addressing convention:**

- UNO #1 = slave ID `1`
- UNO #2 = slave ID `2`
- Velocio = slave ID `3`

**Known network housekeeping issue:** all three Pis have `eth0` advertising itself as the default gateway (`10.20.30.1`) but `eth0` has no upstream internet route. Lower interface metric (100) than `wlan0` (600) means default route prefers `eth0` and breaks outbound. Manual fix per Pi:

```bash
sudo ip route del default via 10.20.30.1 dev eth0
```

A permanent DHCP-side fix is deferred. Not blocking lab work.

### Software baseline

Both Pi 5s run **Pi OS Lite Bookworm** with **OpenPLC** installed. The Pi 3 B+ runs **Debian Trixie** with **Docker**.

Each Pi has two Python virtual environments:

- `~/lab/.venv` — pymodbus 2.5.3, owned by OpenPLC; do not modify
- `~/lab/.venv-modern` — pymodbus 3.13.0, used for lab scripts and probes

Always `source ~/lab/.venv-modern/bin/activate` before running attacker / client tooling.

## The honeypot fabric: Maple Ridge Treatment Plant

### Cover identity

All three honeypots share a single facility identity:

| Field | Value |
|---|---|
| Operator | Springfield Water Authority |
| Facility | Maple Ridge Treatment Plant |
| Address | 1247 Reservoir Rd |
| Operations contact | scada@springfieldwater.gov |

The plant story: Maple Reservoir feeds into clarification → filtration → chlorination → clearwell storage → distribution. Three controllers manage three subsystems. This is intentionally typical for a small municipal water utility — flat `10.20.30.0/24` process LAN, three different vendor brands accumulated through different capital projects, modest segmentation. A defender doing reconnaissance should immediately recognize this as a real-looking OT environment.

### Three personas

| IP | Vendor | Subsystem | Hostname (sysName) | SystemDescription |
|---|---|---|---|---|
| 10.20.30.50 | Siemens S7-200 | Distribution pumps | `PS4-CPU01` | Maple Ridge Plant - distribution pump control |
| 10.20.30.51 | Schneider Modicon M340 | Chemical room HVAC | `HVAC-M340` | Maple Ridge Plant - chemical room HVAC |
| 10.20.30.52 | Allen-Bradley CompactLogix 5370 L33ER | Chlorination dosing | `CHEM-LGX01` | Maple Ridge Plant - chlorination dosing controller |

### Per-vendor SNMP enterprise OID

A defender pulling sysObjectID via SNMP gets a vendor-coherent answer per honeypot:

| Honeypot | sysObjectID OID | Resolves to |
|---|---|---|
| Siemens | `1.3.6.1.4.1.4196.1.1.5.4` | Siemens AG / SIMATIC S7 |
| Schneider | `1.3.6.1.4.1.3833.1.7.1` | Schneider Electric / Modicon |
| Allen-Bradley | `1.3.6.1.4.1.5188.1.1.18.3` | Rockwell Automation / Allen-Bradley |

The Honeynet OID (`1.3.6.1.4.1.20408`) that ships in stock Conpot — and would identify these as honeypots to anyone who Googles the OID — has been **eliminated** via per-persona pysnmp library patches.

### Per-vendor protocol mix

Each persona only exposes protocols that real gear of that family actually speaks.

| Protocol | Siemens | Schneider | Allen-Bradley |
|---|---|---|---|
| HTTP / 80 | ✅ | ✅ | ✅ |
| SNMP / 161 (UDP) | ✅ | ✅ | ✅ |
| Modbus / 502 | ✅ | ✅ | ❌ |
| S7Comm / 102 | ✅ | ❌ | ❌ |
| EtherNet/IP / 44818 | ❌ | ❌ | ✅ |
| BACnet / 47808 | ❌ | ❌ | ❌ |
| IPMI / 623 | ❌ | ❌ | ❌ |
| FTP / 21 | ❌ | ❌ | ❌ |
| TFTP / 69 | ❌ | ❌ | ❌ |

Three coherent fingerprints. The "industrial control + management" surface is preserved on each honeypot, with vendor-native control protocol differentiating them. An Nmap scan with version detection paints three distinctly-shaped devices.

### Process data per persona

All values are static and scenario-coherent. They tell one consistent water plant story across the three subsystems. Values are scaled integers over Modbus (e.g., 14.2 ft = 142, encoded as 0.1 ft per LSB).

#### Siemens / Distribution pumps (10.20.30.50)

**Discrete outputs (coils, slave 1, %Q):**

| Address | Symbol | Value | Meaning |
|---|---|---|---|
| `Q0.0` | `PUMP1_RUN_CMD` | 1 | Pump 1 commanded on (duty) |
| `Q0.1` | `PUMP2_RUN_CMD` | 0 | Pump 2 standby |
| `Q0.2` | `DISCHARGE_VLV_OPEN` | 1 | Valve open |
| `Q0.3` | `ALARM_HORN` | 0 | No alarm |

**Discrete inputs (slave 1, %I):**

| Address | Symbol | Value |
|---|---|---|
| `I0.0` | `PUMP1_RUNNING_FB` | 1 |
| `I0.1` | `PUMP2_RUNNING_FB` | 0 |
| `I0.2` | `CLEARWELL_LOW_SW` | 0 |
| `I0.3` | `CLEARWELL_HIGH_SW` | 0 |
| `I0.4` | `PUMP1_FAULT` | 0 |
| `I0.5` | `PUMP2_FAULT` | 0 |

**Analog inputs (slave 2, AIW, scaled int):**

| Address | Symbol | Raw | Engineering |
|---|---|---|---|
| 30001 | `CLEARWELL_LEVEL` | 142 | 14.2 ft |
| 30002 | `DISCHARGE_PRESS` | 650 | 65.0 PSI |
| 30003 | `PUMP1_CURRENT` | 187 | 18.7 A |
| 30004 | `PUMP2_CURRENT` | 0 | 0.0 A |
| 30005 | `FLOW_RATE` | 1875 | 187.5 GPM |

**Holding registers (slave 2, VW, setpoints):**

| Address | Symbol | Raw | Engineering |
|---|---|---|---|
| 40001 | `PUMP1_SPEED_SP` | 78 | 78% VFD |
| 40002 | `PUMP2_SPEED_SP` | 0 | 0% |
| 40003 | `PRESSURE_SP` | 650 | 65.0 PSI |
| 40004 | `LEVEL_LOW_LIMIT` | 80 | 8.0 ft |
| 40005 | `LEVEL_HIGH_LIMIT` | 200 | 20.0 ft |

The internal consistency: `PUMP1_RUN_CMD = PUMP1_RUNNING_FB` (commanded and confirmed), `DISCHARGE_PRESS = PRESSURE_SP` (at setpoint), level 14.2 sits between low limit 8.0 and high limit 20.0 (well-buffered), pump 1 current 18.7 A with pump 2 at 0.0 A (consistent with duty/standby pairing).

#### Schneider / Chemical room HVAC (10.20.30.51)

**Coils (%M):**

| Address | Symbol | Value |
|---|---|---|
| `%M1` | `EXHAUST_FAN_RUN` | 1 |
| `%M2` | `MAKEUP_DAMPER_OPEN` | 1 |
| `%M3` | `ROOM_LIGHTS` | 1 |
| `%M4` | `CL2_ALARM_ACTIVE` | 0 |

**Discrete inputs (%I):**

| Address | Symbol | Value |
|---|---|---|
| `%I1` | `FAN_RUNNING_FB` | 1 |
| `%I2` | `DOOR_CLOSED_SW` | 1 |
| `%I3` | `DAMPER_OPEN_FB` | 1 |
| `%I4` | `CL2_HIGH_ALARM` | 0 |

**Input registers (%IW, scaled int):**

| Address | Symbol | Raw | Engineering |
|---|---|---|---|
| `%IW1` | `ROOM_TEMP` | 680 | 68.0 °F |
| `%IW2` | `ROOM_RH` | 425 | 42.5 % |
| `%IW3` | `CL2_PPM` | 5 | 0.05 ppm |
| `%IW4` | `ROOM_PRESS` | 65511 | -0.25 inH2O (signed two's complement) |
| `%IW5` | `FAN_SPEED_FB` | 60 | 60 % |

**Holding registers (%MW):**

| Address | Symbol | Raw | Engineering |
|---|---|---|---|
| `%MW1` | `TEMP_SP` | 700 | 70.0 °F |
| `%MW2` | `FAN_SPEED_CMD` | 60 | 60 % |
| `%MW3` | `CL2_ALARM_LIMIT` | 100 | 1.00 ppm |
| `%MW4` | `NEG_PRESS_SP` | 65516 | -0.20 inH2O |

The negative pressure encoding deserves attention. Modbus is unsigned-16-bit on the wire, so signed values are encoded as two's complement: -0.25 inH2O = -25 in scaled units = 65536 - 25 = 65511. Decoding requires the client to know to interpret values ≥ 32768 as negative. This is real-world Modbus behavior and a small teaching opportunity.

The room is held at slightly more negative pressure (-0.25) than its setpoint (-0.20), which is normal control loop overshoot. The Cl2 reading 0.05 ppm is well below the alarm at 1.00 ppm. Negative pressure is a safety design — the chemical room is held below atmospheric so any chlorine leak vents through the exhaust system, not into occupied plant areas.

#### Allen-Bradley / Chlorination dosing (10.20.30.52)

> **Note:** AB has Modbus disabled, so these values aren't queryable via Modbus. They surface only on the HTTP pages (which use `<condata>` placeholders pulling from the same databus). The data is still loaded into the template for architectural consistency and in case any future EtherNet/IP work needs it.

**Coils:**

| Address | Symbol | Value |
|---|---|---|
| 1 | `PUMPA_RUN` | 1 |
| 2 | `PUMPB_RUN` | 0 |
| 3 | `MIXER_RUN` | 1 |
| 4 | `LOW_TANK_ALARM` | 0 |

**Discrete inputs:**

| Address | Symbol | Value |
|---|---|---|
| 10001 | `PUMPA_RUNNING_FB` | 1 |
| 10002 | `PUMPB_RUNNING_FB` | 0 |
| 10003 | `LEAK_DETECT` | 0 |

**Analog inputs (scaled int):**

| Address | Symbol | Raw | Engineering |
|---|---|---|---|
| 30001 | `TANK_LEVEL_PCT` | 730 | 73.0 % |
| 30002 | `CHLORINE_RESIDUAL` | 80 | 0.80 mg/L |
| 30003 | `PUMPA_STROKE_RATE` | 45 | 45 strokes/min |
| 30004 | `PUMPB_STROKE_RATE` | 0 | 0 strokes/min |
| 30005 | `pH` | 740 | 7.40 |

**Holding registers:**

| Address | Symbol | Raw | Engineering |
|---|---|---|---|
| 40001 | `RESIDUAL_SP` | 80 | 0.80 mg/L (target = actual) |
| 40002 | `PUMPA_STROKE_SP` | 45 | 45 |
| 40003 | `PUMPB_STROKE_SP` | 45 | 45 |
| 40004 | `LOW_TANK_ALARM_PCT` | 200 | 20.0 % |

Residual setpoint equals actual reading (control loop on target). pH 7.40 is normal for treated drinking water. Pump A duty / Pump B standby pattern matches the distribution pump pattern at the Siemens controller — consistent plant design philosophy.

### HTTP page deception

Each persona serves five vendor-themed HTML pages: `index`, `identification`, `diagnostics`, `variables` (or vendor-specific equivalent), and `login`. All pages use Conpot's `<condata>` placeholders to pull live values from the databus, so HTTP, SNMP, and Modbus all tell the same story.

The pages don't try to be pixel-perfect clones of real Siemens/Schneider/Rockwell admin UIs. They aim for vendor-typical visual identity and, more importantly, **vendor-correct terminology**:

| Page concept | Siemens | Schneider | Allen-Bradley |
|---|---|---|---|
| Process variable view | "Variable Status" (Q/I/AIW/VW addressing) | "Modbus Diagnostics" (%M/%I/%IW/%MW addressing) | "Tag Database" (named tags like `PumpA.Run`) |
| Diagnostic events | "Diagnostic Buffer" (`16#` hex event IDs) | "Event Log" (`HVAC.PID_TEMP` source labels) | "Diagnostics" (Major/Minor faults, Tasks) |
| Fault model | n/a | n/a | "Major fault: NONE / Minor fault: NONE" |
| Vendor branding | SIMATIC, navy blue + orange accent | Schneider Electric / FactoryCast, green | ALLEN-BRADLEY, black/red |

The terminology layer is the deepest part of the deception. Industrial folks who recognize "Tag Database" or "%MW" or "16# 4302" will read the brand from the vocabulary alone. Even imperfect visual styling is overshadowed by vendor-correct language.

**Login forms:** every persona has a login page with a working-looking form. Submitting credentials returns the same "Access denied" page (because Conpot's static-content model serves the same HTML for GET and POST), and every attempt is logged in `conpot.json` for forensic analysis.

### Deployment file layout

Everything lives under `~/conpot/compose/` on `honeypot-host`. The whole deployment is reproducible: scp this directory to any arm64 or amd64 Docker host, run `docker compose up -d`, and you get the full Maple Ridge fabric.

```
~/conpot/compose/
├── docker-compose.yml                      # 76 lines, macvlan parent eth0,
│                                           # 3 services with restart: unless-stopped
├── templates-siemens/                      # PS4-CPU01 persona
│   ├── template.xml                        # cover identity + databus values
│   ├── modbus/modbus.xml                   # enabled=True
│   ├── s7comm/s7comm.xml                   # enabled=True
│   ├── http/
│   │   ├── http.xml                        # 11 URL nodes registered
│   │   └── htdocs/
│   │       ├── index.html                  # SIMATIC-themed landing
│   │       ├── identification.html
│   │       ├── diagnostics.html
│   │       ├── variables.html
│   │       └── login.html
│   ├── snmp/                               # enabled=True
│   ├── enip/enip.xml                       # enabled=False
│   ├── bacnet/bacnet.xml                   # enabled=False
│   ├── ipmi/ipmi.xml                       # enabled=False
│   ├── ftp/ftp.xml                         # enabled=False
│   └── tftp/tftp.xml                       # enabled=False
├── templates-schneider/                    # HVAC-M340 persona
│   ├── template.xml
│   ├── modbus/modbus.xml                   # enabled=True
│   ├── s7comm/s7comm.xml                   # enabled=False
│   ├── http/
│   │   ├── http.xml
│   │   └── htdocs/
│   │       ├── index.html                  # FactoryCast-themed
│   │       ├── identification.html         # "Module Diagnostics"
│   │       ├── diagnostics.html            # "Event Log"
│   │       ├── variables.html              # "Modbus Diagnostics"
│   │       └── login.html
│   └── (enip/bacnet/ipmi/ftp/tftp all enabled=False)
├── templates-allenbradley/                 # CHEM-LGX01 persona
│   ├── template.xml
│   ├── enip/enip.xml                       # enabled=True
│   ├── modbus/modbus.xml                   # enabled=False
│   ├── s7comm/s7comm.xml                   # enabled=False
│   ├── http/
│   │   ├── http.xml
│   │   └── htdocs/
│   │       ├── index.html                  # RSLogix-themed
│   │       ├── identification.html         # "Module Information / CIP Identity Object"
│   │       ├── diagnostics.html            # "Tasks", "Major fault / Minor fault"
│   │       ├── variables.html              # "Tag Database"
│   │       └── login.html
│   └── (bacnet/ipmi/ftp/tftp all enabled=False)
├── pysnmp-overrides/                       # per-vendor SNMP MIB patches
│   ├── __SNMPv2-MIB.py                     # original (kept as backup)
│   ├── __SNMPv2-MIB-siemens.py             # Siemens enterprise OID
│   ├── __SNMPv2-MIB-schneider.py           # Schneider enterprise OID
│   └── __SNMPv2-MIB-allenbradley.py        # Rockwell enterprise OID
├── config-overrides/
│   └── conpot.cfg                          # fetch_public_ip = False
└── logs/                                   # forensic capture (UID 2000)
    ├── siemens/
    │   ├── conpot.json                     # structured event log
    │   ├── conpot.log                      # human-readable log
    │   ├── ftp/                            # (unused, FTP disabled)
    │   └── tftp/                           # (unused, TFTP disabled)
    ├── schneider/{conpot.json, conpot.log, ...}
    └── allenbradley/{conpot.json, conpot.log, ...}
```

### Operations cheatsheet

All commands run from `honeypot-host`, in `~/conpot/compose/`.

**Bring everything up:**
```bash
docker compose up -d
```

**Tear everything down:**
```bash
docker compose down
```

**Restart one persona** (after a template edit, etc.):
```bash
docker compose restart honeypot-siemens
# or honeypot-schneider, or honeypot-allenbradley
```

**Watch logs:**
```bash
docker compose logs -f honeypot-siemens
# or tail the JSON file directly
tail -f ~/conpot/compose/logs/siemens/conpot.json
```

**Container status:**
```bash
docker compose ps
```

**Macvlan caveat:** `honeypot-host` itself **cannot reach** `10.20.30.50/51/52` — only `softplc-1`, `softplc-2`, or any other host on the lab segment can. This is a Linux macvlan kernel limitation, not a misconfiguration. Always test honeypot reachability from one of the other Pis.

### Validation tests (cross-Pi)

Run these from `softplc-2` (or any host other than `honeypot-host`) to confirm the deception fabric is healthy.

**SNMP vendor coherence:**
```bash
snmpwalk -v2c -c public 10.20.30.50 1.3.6.1.2.1.1.2.0
# expect 1.3.6.1.4.1.4196.1.1.5.4 (Siemens)
snmpwalk -v2c -c public 10.20.30.51 1.3.6.1.2.1.1.2.0
# expect 1.3.6.1.4.1.3833.1.7.1 (Schneider)
snmpwalk -v2c -c public 10.20.30.52 1.3.6.1.2.1.1.2.0
# expect 1.3.6.1.4.1.5188.1.1.18.3 (Rockwell)
```

**Facility coherence:**
```bash
for ip in 10.20.30.50 10.20.30.51 10.20.30.52; do
  snmpwalk -v2c -c public $ip 1.3.6.1.2.1.1.6.0
done
# expect: all three return "Maple Ridge Treatment Plant, 1247 Reservoir Rd"
```

**Per-vendor protocol mix** (TCP only — SNMP/UDP won't show):
```bash
for ip in 10.20.30.50 10.20.30.51 10.20.30.52; do
  echo "--- $ip ---"
  for port in 80 502 102 44818; do
    timeout 1 bash -c "echo > /dev/tcp/$ip/$port" 2>/dev/null && echo "  $port OPEN"
  done
done
# expect:
#   .50: 80, 502, 102 OPEN (Siemens: HTTP, Modbus, S7Comm)
#   .51: 80, 502 OPEN     (Schneider: HTTP, Modbus)
#   .52: 80, 44818 OPEN   (AB: HTTP, EtherNet/IP)
```

**HTTP page sweep:**
```bash
for ip in 10.20.30.50 10.20.30.51 10.20.30.52; do
  for page in index identification diagnostics variables login; do
    curl -sL --connect-timeout 3 http://$ip/$page.html | grep -oE '<title>[^<]+</title>' | head -1
  done
done
```
Should return 15 vendor-distinct titles.

**Full process snapshot via Modbus** (Siemens + Schneider only):
```bash
source ~/lab/.venv-modern/bin/activate
python3 <<'PY'
from pymodbus.client import ModbusTcpClient
def s16(u): return u - 65536 if u >= 32768 else u

# Siemens
c = ModbusTcpClient('10.20.30.50', port=502); c.connect()
ai = c.read_input_registers(address=30001, count=5, device_id=2).registers
print(f"Siemens clearwell:    {ai[0]/10:.1f} ft")
print(f"Siemens disch press:  {ai[1]/10:.1f} PSI")
print(f"Siemens flow:         {ai[4]/10:.1f} GPM")
c.close()

# Schneider
c = ModbusTcpClient('10.20.30.51', port=502); c.connect()
ai = c.read_input_registers(address=30001, count=5, device_id=2).registers
print(f"Schneider room temp:  {ai[0]/10:.1f} F")
print(f"Schneider Cl2:        {ai[2]/100:.2f} ppm")
print(f"Schneider room press: {s16(ai[3])/100:+.2f} inH2O")
c.close()
PY
```

Expected output:
```
Siemens clearwell:    14.2 ft
Siemens disch press:  65.0 PSI
Siemens flow:         187.5 GPM
Schneider room temp:  68.0 F
Schneider Cl2:        0.05 ppm
Schneider room press: -0.25 inH2O
```

### Operational footprint

- **Image:** `ghcr.io/telekom-security/conpot:24.04.1` (arm64, ~310MB on disk)
- **Container UID/GID:** 2000:2000 (host log directories must be `chown 2000:2000`)
- **Memory:** ~120MB RSS per Conpot container, ~360MB total of 905MB Pi 3 B+ RAM
- **Network:** macvlan parent `eth0`, three static IPs in `10.20.30.0/24` range
- **Restart policy:** `unless-stopped` — survives Pi reboot automatically

### Forensic capture

Each persona writes its own log files. All attack traffic is captured.

- **Structured JSON** at `~/conpot/compose/logs/<vendor>/conpot.json` — one line per event, easy to grep or parse with `jq`. Records HTTP requests with full headers, Modbus PDUs (function code, slave ID, address, count, data), SNMP queries (OID, community), and login attempts (username, password, source IP).
- **Human-readable text** at `~/conpot/compose/logs/<vendor>/conpot.log` — same events, free-form text format for eyeballing.

**Examples — extract login attempts across all personas:**
```bash
for vendor in siemens schneider allenbradley; do
  echo "=== $vendor login attempts ==="
  grep -i 'login\|password' ~/conpot/compose/logs/$vendor/conpot.json | head
done
```

**Find all source IPs that touched a honeypot:**
```bash
jq -r '.remote[0]' ~/conpot/compose/logs/siemens/conpot.json | sort -u
```

## Phase plan

### Phase 0: provisioning ✅ COMPLETE

- Pi OS Lite Bookworm on both Pi 5s
- Debian Trixie on Pi 3 B+
- OpenPLC running on both Pi 5s
- Docker on Pi 3 B+
- Two pip venvs per Pi (`.venv` for OpenPLC's pymodbus 2.5.3, `.venv-modern` for lab work with pymodbus 3.13.0)
- All three Pis bridged on the lab segment

### Honeypot deployment ✅ COMPLETE

Documented above. Single-facility cover, three vendor personas, vendor-coherent protocols and SNMP OIDs, full vendor-themed HTTP UIs, scenario-coherent process data, forensic logging.

### Phase 1: Modbus loop between the two real PLCs ✅ COMPLETE

First time data flows between the two real PLC hosts. Documented in detail at [phase-1-modbus-loop.md](phase-1-modbus-loop.md).

- `softplc-2` runs `sensor-sim` on TCP/5020 (~250-line pure-stdlib Modbus TCP slave; pymodbus 3.13's deprecated server context was broken so we wrote our own).
- `softplc-1`'s OpenPLC is configured as a Modbus master via Slave Devices, polling sensor-sim every 100 ms.
- A small Structured Text program (`plc/softplc1-sensor-monitor.st`) mirrors the values into local `%QW` / `%QX` variables — automatically exposed on softplc-1's own port-502 server — and tracks heartbeat liveness for link-loss telemetry.
- Two pcaps captured for teaching artifacts.

### Phase 2: physical I/O on the soft-PLCs

Wire actual buttons and lights to the soft-PLCs so the Phase 1 data flow drives visible physical state. This is the first phase that makes the lab tangible — until now, everything has been bytes on a wire. Concretely:

1. **softplc-1 — pushbutton input** on the Freenove. Wire one uxcell 12 mm momentary between a screw terminal (e.g. IO17) and GND. Configure OpenPLC's hardware layer so that pin maps to a `%IX` variable. ST program update: when the button is pressed, set a Modbus coil that softplc-2 reads.
2. **softplc-2 — relay HAT custom hardware layer.** OpenPLC's stock Pi hardware target doesn't know about the Waveshare 3-CH HAT's specific pin map (BCM 26/20/21) or its active-LOW polarity. Need a custom hardware layer (`./scripts/hardware_layers/raspberrypi.cpp` overrides) that maps `%QX0.0..%QX0.2` correctly. ST program: drive CH1 based on a system state coil (red = idle, green = button pressed); drive CH2 based on the high-temp alarm bit from sensor-sim (LED strip on when alarm).
3. **Field wiring.** AD16 indicator: 24V supply → CH1 COM, CH1 NC → red lead, CH1 NO → green lead, AD16 commons → 24V negative. LED strip: cut the +12 V wire from its brick, splice both ends into CH2 COM and CH2 NO. (Detailed wiring already designed earlier in the project; needs the 24V PSU to arrive before this can light up.)
4. **End-to-end demo:** press the button on softplc-1 → softplc-2's green light turns on. Release → red. Force the temperature alarm via a write to sensor-sim's holding registers (rewrite the simulator briefly) → LED strip turns on. That's the SCADA cause-and-effect chain, end to end, on real hardware.

This phase is currently blocked on the 24 V PSU (OMCH EDR-120-24, ordered) and on writing the custom hardware layer. Pushbutton wiring and the ST program changes can happen as soon as the PSU is in hand and a quiet hour shows up.

### Phase 3: UNO Modbus RTU + ESP32 firmware (in progress)

All ESP32 + UNO firmware is **Arduino IDE on Aaron's Windows laptop** (which is on the lab WiFi). The Windows machine plugs the ESP32/UNO in directly via USB; sketches live in `plc/esp32/<board>/` and `plc/uno/<board>/` in this repo and are pulled to the Windows laptop via `git pull`. First-time IDE setup is documented at [`arduino-setup.md`](arduino-setup.md). Verification probes run from a Pi on the lab segment over the network after each flash.

**Why Arduino over MicroPython:** the original bring-up used MicroPython, which worked, but Arduino is the right long-term toolchain for this lab. Reasons: consistency with the UNO half of Phase 3 (Arduino-native), authenticity (real commercial IIoT firmware is overwhelmingly C/C++), DEF CON reproducibility (Arduino IDE is the lowest-common-denominator tool for the audience), and the eventual ESP32 #3 (WiFi sniffer/attacker) needs ESP-IDF anyway. The MicroPython artifacts were removed in commit; the architectural findings (MFCTP bridges to wired lab segment, ESP32 #1 = `iot-1` at `10.20.30.40`, MAC-keyed addressing scheme) carry over unchanged because they're facts about the network, not the firmware.

**ESP32 #1 (`iot-1` at `10.20.30.40`, MAC `58:e6:c5:6f:42:80`):** sketch [`plc/esp32/iot-1/iot-1.ino`](../plc/esp32/iot-1/iot-1.ino) written and committed. WiFi + static IP + heartbeat over Serial. Modbus TCP slave behavior (the actual "vendor IIoT monitoring device" role) is the next iteration. Pending: Aaron pulls on Windows + uploads via IDE.

**ESP32 #2 (`hmi-1` at `.30`)** and **ESP32 #3 (`attacker-1` at `.60`)** — boards untouched, sketches not yet written.

**UNO Modbus RTU** — UNO #1 with the hiBCTR 4-channel relay shield becomes a Modbus RTU slave over USB serial (or RS-485 via a MAX485). Hardware on hand, sketch not written.

### Phase 4: CP2102 + RS-485 bridge into OpenPLC

Connect the UNO RTU slave to OpenPLC via the Waveshare RS485-to-ETH gateway and one of the MAX485 modules. Mixed-medium Modbus (TCP + RTU through one bridge) becomes part of the lab — the realistic plant pattern where a gateway translates between the IT-side network and the field-side serial bus.

### Phase 5: Mount everything in the rack

Tecmojo 4U rack, DIN rails, power distribution (12 V + 24 V Mean Wells), Jienk distribution blocks, fuse holders, ground bus, optional E-stop, network switch, all the soft-PLCs and UNOs DIN-mounted. The lab becomes a portable physical artifact suitable for DEF CON travel.

### Phase 6: Attack tooling labs

Build out attack scenarios as standalone exercises: scanning, fingerprinting, Modbus reads, Modbus writes, S7 enumeration, EtherNet/IP enumeration, replay attacks against the honeypots, defending real PLCs vs decoy honeypots.

## Outstanding polish (not blocking phase work)

These are honest deferrals — known issues that don't break anything but would be nice to clean up.

- **Honeycloud string in `conpot.cfg`:** `[hpfriends] host = hpfriends.honeycloud.net` is in the config even though the `[hpfriends]` section is `enabled = False`. The string is a tell if anyone exfiltrates the config file. Cosmetic.
- **Static event timestamps in HTTP diagnostics pages:** the fake event-log entries on each persona's diagnostics page have hardcoded dates from early May 2026. They'll look "stale" if anyone accesses the lab months from now. Could be made dynamic via `<condata source="eval" key="..." />`.
- **Default-route bug on all three Pis** (`eth0` advertises gateway, no upstream). Manual `ip route del` workaround works. Permanent fix is DHCP-side or a static route override in `/etc/dhcpcd.conf` — not yet applied.
- **Login attempt analytics tooling:** the JSON logs capture login attempts, but no script yet aggregates them across personas for DEF CON metrics.
- **Conpot "random data on restart" baseline avoided:** static values are now hardcoded. If we ever want measurement-noise realism (analog values jittering around setpoints), we'd swap to `<value type="function">` databus entries. Path A picked deliberately for predictable demos.

## Reference appendix

### Hostname/IP cross-reference

| Hostname (DNS) | Hostname (lab) | Lab IP | Mgmt IP | Role |
|---|---|---|---|---|
| `softplc-1` | RASPLC01 | 10.20.30.111 | RASPLC01.local | OpenPLC #1 |
| `softplc-2` | RASPLC02 | 10.20.30.49 | RASPLC02.local | OpenPLC #2 / attacker |
| `honeypot-host` | (n/a) | 10.20.30.48 | honeypot-host.local | Conpot Docker host |
| `honeypot-siemens` | (containerized) | 10.20.30.50 | (n/a) | Conpot Siemens persona |
| `honeypot-schneider` | (containerized) | 10.20.30.51 | (n/a) | Conpot Schneider persona |
| `honeypot-allenbradley` | (containerized) | 10.20.30.52 | (n/a) | Conpot Allen-Bradley persona |

### Modbus addressing reference

Standard Modbus function-code-prefixed addressing used throughout:

| Address range | Type | Function codes |
|---|---|---|
| 00001-09999 | Coils (writable bits) | FC1 read, FC5/15 write |
| 10001-19999 | Discrete inputs (read-only bits) | FC2 read |
| 30001-39999 | Input registers (read-only words) | FC4 read |
| 40001-49999 | Holding registers (writable words) | FC3 read, FC6/16 write |

In pymodbus 3.x, `read_coils(address=1)` requests coil 00001 (relative offset 1, 1-based). For analog reads, `read_input_registers(address=30001)` requests the first input register.

Scaled integer convention used in this lab: 0.1 unit per LSB (so 14.2 ft = 142). Two's complement for signed analog values that can go negative (room pressure -0.25 inH2O = 65511).

### Vendor enterprise OID reference

| Vendor | Enterprise OID prefix | Used for |
|---|---|---|
| Siemens AG | `1.3.6.1.4.1.4196` | sysObjectID = `4196.1.1.5.4` (S7-200) |
| Schneider Electric | `1.3.6.1.4.1.3833` | sysObjectID = `3833.1.7.1` (Modicon M340) |
| Rockwell / Allen-Bradley | `1.3.6.1.4.1.5188` | sysObjectID = `5188.1.1.18.3` (CompactLogix L33ER) |
| Honeynet (avoid - identifies as honeypot) | `1.3.6.1.4.1.20408` | (eliminated via per-persona pysnmp patches) |

---

*End of working draft. This document is intended to be updated as phases progress.*
