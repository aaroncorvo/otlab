# OTLab Modbus I/O bridge

Puts the physical Qwiic hardware on the wire as a **standard Modbus TCP
slave** so the rest of the OT world — OpenPLC, SCADA, the OTLab dashboard,
and Suricata — can see and touch the same temperature/relay/motor that the
ladder PLC drives.

```
OpenPLC / SCADA / dashboard  ──Modbus/TCP :502──►  otlab-modbus-io
                                                       │ REST :8090
                                                       ▼
                                       otlab-qwiic  ──I2C──►  TMP117 (temp)
                                                              relay
                                                              motor (turbine)
```

This is the canonical "protocol gateway" pattern: a box that turns a
modern REST device into a legacy fieldbus other OT gear already speaks. It
also closes the loop between the **physical** half of the lab (the Cruiser
board's Qwiic devices) and the **virtual** half (the ContainerLab fabric,
OpenPLC, modbus-master) — they now share one Modbus contract.

> Verified live on the teacher Pi: a Modbus master read `HR0 = 259`
> (25.9 °C) and `HR1 = 787` (78.7 °F), then **wrote `HR11 = 35`** and the
> real wind-turbine motor spun to 35%, and **wrote `HR10 = 1`** and the
> real relay clicked on. Bidirectional, on real hardware.

## Install

```bash
./teacher/modbus/install-modbus.sh otadmin@10.20.30.27
```

Requires the `otlab-qwiic` I/O service (the bridge reaches the hardware
through its REST API). Installs `python3-pymodbus`, drops the service, and
serves Modbus TCP on **:502**.

## Register map

Holding registers, **zero-indexed wire addresses** (FC 3 read, FC 6/16
write). Signed values are 16-bit two's-complement — read them as `INT16`.

### Live state — read these (refreshed every ~0.4 s)

| HR | meaning | encoding |
|----|---------|----------|
| 0 | temperature °C | ×10 signed (`259` = 25.9 °C) |
| 1 | temperature °F | ×10 signed (`787` = 78.7 °F) |
| 2 | relay state | 0 / 1 |
| 3 | motor A speed | signed −100..100 % |
| 4 | motor B speed | signed −100..100 % |
| 5 | fresh flag | 1 = last poll ok, 0 = stale |
| 6 | poll age | ms since last good poll |

### Commands — write these (the bridge actuates on change)

| HR | meaning | encoding |
|----|---------|----------|
| 10 | relay command | 0 = off, 1 = on |
| 11 | motor A command | signed −100..100 % |
| 12 | motor B command | signed −100..100 % |

> Addressing note: pymodbus 3.8 dropped the `zero_mode` kwarg but kept the
> legacy +1 wire→store offset internally. The bridge compensates
> (`STORE_OFFSET = 1`) so the wire addresses above are exactly what a
> master uses — HR0 really is temperature. No surprises for OpenPLC.

## Quick check

```bash
# read the live map
python3 teacher/modbus/modbus-read.py 10.20.30.27

# drive the hardware over Modbus
python3 teacher/modbus/modbus-read.py 10.20.30.27 --motor-a 40
python3 teacher/modbus/modbus-read.py 10.20.30.27 --relay on
python3 teacher/modbus/modbus-read.py 10.20.30.27 --relay off --motor-a 0
```

## Wiring it into OpenPLC (virtual fabric reads the real hardware)

In the OpenPLC web UI (**Slave Devices → Add new device**):

- Device type: **Generic Modbus TCP Device**
- IP: the Pi running this bridge (e.g. `10.20.30.27`), port **502**
- Holding registers (read): start **0**, size **7** → temp °C, temp °F,
  relay, motor A, motor B, fresh, poll-age
- Holding registers (write): map your logic to **10** (relay), **11**
  (motor A), **12** (motor B)

Now an OpenPLC program can read the real TMP117 and command the real
turbine — the physical Cruiser board becomes a field device in the virtual
plant. (See `scripts/openplc-add-gateway-slave.sh` for the scripted-insert
pattern used with the ESP32 gateway.)

## Two masters, one relay

The ladder PLC (`otlab-plc`) and a Modbus master can both command the
relay/motor. They don't deadlock — each just issues REST calls — but the
**last writer wins**. In class, either let the ladder logic own the
outputs (read-only Modbus for monitoring/IDS) or stop the ladder engine and
drive from Modbus. The read registers (HR0–6) always reflect the true
current state regardless of who is driving.

## Security teaching value

Port 502 now carries real Modbus traffic on the lab network, so the
Suricata sensors have authentic frames to alert on: register scans, writes
to coils/holding registers, malformed PDUs. It's a genuine ICS protocol to
attack and defend, not a simulation.
