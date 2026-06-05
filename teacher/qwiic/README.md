# OTLab Qwiic physical I/O

Control surface for the SparkFun Qwiic actuators/sensors plugged into the
teacher Pi's Cruiser carrier. Serves a control page + REST API on `:8090`.

```
┌─ Physical I/O (http://10.20.30.27:8090/) ─────────┐
│  TMP117 Temperature   27.2 °C  81.0 °F            │
│  Relay   [ ON ] [ OFF ]            state: OFF      │
│  Wind Turbine Motor   ◀─────●─────▶  speed slider  │
│         [Spin] [Full] [Reverse] [STOP] [Apply]     │
└────────────────────────────────────────────────────┘
```

Login: `otlab` / `P@ssw0rd!` (HTTP basic auth).

## Install

```bash
./teacher/qwiic/install-qwiic.sh otadmin@10.20.30.27
```

Installs `flask` + `smbus2`, drops `otlab-qwiic.py` in `/opt/otlab/`,
enables `otlab-qwiic.service`. Linked from the teacher Links Hub.

## Hardware

These devices are on **I2C bus 1** (the LCD is on bus 2 — see
`teacher/lcd/`). Both are separate Qwiic ports on the Cruiser carrier.

| Addr | Device | Direction |
|------|--------|-----------|
| 0x18 | Qwiic Single Relay | output (on/off) |
| 0x48 | TMP117 temp | input |
| 0x5d | Qwiic Motor Driver (SCMD) | output (drives the wind turbine) |

## REST API

| Method | Path | Body | Effect |
|--------|------|------|--------|
| GET | `/api/state` | — | `{temp_c, temp_f, relay, motor_a, motor_b, motor_ready}` |
| POST | `/api/relay` | `{"on": true}` | relay on/off |
| POST | `/api/motor` | `{"channel":"A","speed":60}` | drive motor (speed -100..100 %) |
| POST | `/api/motor/stop` | — | stop both motors |

## Device protocols

```
Relay 0x18 : write byte 0x01 (on) / 0x00 (off); read reg 0x05 for state
TMP117 0x48: temp reg 0x00, signed 16-bit, 1 LSB = 0.0078125 °C
SCMD 0x5d  : ID reg 0x01 == 0xA9; enable reg 0x70 = 0x01;
             Motor A drive reg 0x20, Motor B reg 0x21;
             drive byte 128 = stop, 255 = full fwd, 0 = full reverse
             speed% -> drive: 128 + round(speed * 127/100)
```

## Wind turbine demo

The motor drives Motor A (reg 0x20). The control page exposes a speed
slider plus Spin / Full / Reverse / STOP. The driver outputs are enabled
once (`0x70 = 0x01`) on first motor command and on service start; the
service stops both motors on startup for safety.

## Next: into the OT fabric

The natural follow-up is to bridge these into Modbus (like the ESP32
gateway) so OpenPLC can read the TMP117 and command the relay/motor as
real PLC I/O — closing the loop end to end. Tracked as a future task.

## Manage

```bash
ssh otadmin@10.20.30.27 'journalctl -u otlab-qwiic -f'
ssh otadmin@10.20.30.27 'sudo systemctl restart otlab-qwiic'
```
