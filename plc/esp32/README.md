# plc/esp32/

Arduino sketches for the three Lonely Binary ESP32-S3 N16R8 boards in the lab.

## Status (2026-05-06)

| Board | Role | Lab IP | MAC | Sketch | Status |
|---|---|---|---|---|---|
| ESP32 #1 | `iot-1` — vendor IIoT monitoring device | 10.20.30.40 | `58:e6:c5:6f:42:80` | [`iot-1/iot-1.ino`](iot-1/iot-1.ino) | WiFi join + heartbeat working as MicroPython during initial bring-up; pivoting to Arduino. Re-flash from Windows Arduino IDE pending. |
| ESP32 #2 | `hmi-1` — operator HMI w/ keypad | 10.20.30.30 | TBD | not yet written | Board untouched |
| ESP32 #3 | `attacker-1` — WiFi sniff / probe platform | 10.20.30.60 | TBD | not yet written | Board untouched |

## Architecture finding: MFCTP bridges to the wired lab segment

The lab WiFi (`MFCTP`) is bridged onto the same Layer 2 broadcast domain as the wired `eth0` lab segment. Verified by booting ESP32 #1 with DHCP — it leased `10.20.30.204` (later pinned to `.40`) and could reach `l1-plc-01` (`10.20.30.47`) and the wired honeypots (`.50/.51/.52`) directly. So our wired and wireless tiers share `10.20.30.0/24` with a single DHCP server (gateway `10.20.30.1`, DNS `10.20.30.1`). No dual-homing or routing kludges.

## Toolchain

**Arduino IDE on Windows**, plugged into the lab network. ESP32s plug directly into the Windows laptop's USB (left "UART" port on the board, via the CH340 USB-serial bridge). Sketches live in this directory; you `git pull`, open the `.ino`, click upload, watch the Serial Monitor. Network behavior is verified separately by SSH-ing to a Pi on the lab segment and probing the device.

**First-time IDE setup:** see [`/docs/arduino-setup.md`](../../docs/arduino-setup.md). Install the ESP32 board core (Espressif's URL goes in Boards Manager), the CH340 Windows driver, and configure the per-sketch Tools-menu values once. Critical setting that bites if wrong: **PSRAM = OPI PSRAM** for the N16R8 boards (they have octal-mode PSRAM, not quad).

## Layout

```
plc/esp32/
├── README.md           you are here
├── iot-1/
│   ├── iot-1.ino       sketch — WiFi + static IP + heartbeat (Modbus TCP slave to come)
│   └── wifi_secrets.h  shared lab WiFi creds (deliberately tracked — see file comment)
├── hmi-1/              (planned)
└── attacker-1/         (planned)
```

Each board gets its own subdirectory with its own `.ino` and a copy of `wifi_secrets.h`. Tiny duplication, but it keeps each sketch self-contained the way Arduino IDE expects.

## Per-board static IP scheme

The static IP is hardcoded in each sketch (`STATIC_IP` constants near the top of the `.ino`). When adding new boards:

1. First flash with whatever IP the sketch has (or temporarily DHCP).
2. Find the MAC the board presents (in the boot log: `mac=...`, or `arp` on a Pi after the device joins).
3. Assign per the lab IP plan: ESP32s = `.30/.40/.60` for hmi/iot/attacker respectively.
4. Edit the sketch's `STATIC_IP` constant, push, re-flash.

## Deploy workflow

```
# on Windows laptop
cd <otlab repo>
git pull
# open plc/esp32/iot-1/iot-1.ino in Arduino IDE
# (first time only) set Tools menu per docs/arduino-setup.md
# Tools > Port > select the COM port for the ESP32
# Click Upload
# Open Tools > Serial Monitor at 115200 baud — watch the boot log

# on Mac/Pi to verify network:
ssh otadmin@RASPLC01.local 'ping -c 3 10.20.30.40'   # l1-plc-01 pings ESP32
```

## What each sketch will eventually do

- **`iot-1/iot-1.ino`** — Modbus TCP slave on port 502 exposing a few simulated sensor values. Acts as a "vendor monitoring device" that l1-plc-01 (or attendees) can read. Currently just WiFi + heartbeat.
- **`hmi-1/hmi-1.ino`** — keypad input + small OLED/web HMI, sends Modbus TCP writes to l1-plc-01 to flip control bits. Demonstrates the IT/OT bridge anti-pattern.
- **`attacker-1/attacker-1.ino`** — WiFi sniffing / deauth / packet injection. Probably ESP-IDF rather than Arduino if we want full radio control; TBD.

## Reference

- [`/docs/arduino-setup.md`](../../docs/arduino-setup.md) — first-time IDE + driver install
- [`/docs/lab-architecture.md`](../../docs/lab-architecture.md) — overall lab design, IP plan, phase plan
- ESP32 Arduino core docs: <https://docs.espressif.com/projects/arduino-esp32/en/latest/>
- Lonely Binary N16R8 product page: <https://www.lonelybinary.com/products/esp32-s3>
