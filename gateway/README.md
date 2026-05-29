# OTLab — Modbus TCP Gateway

Translates ESP32 REST API readings into Modbus TCP holding registers
so the in-fabric OT devices (OpenPLC, `modbus-master`, `sensor-sim`)
can read sensor data from a physical ESP32 board as if it were a real
PLC.

This is the canonical pattern in real industrial OT modernization:
an "IoT gateway" sits between modern HTTP/REST devices and legacy
fieldbus protocols. Real-world equivalents: Schneider EcoStruxure
Building Operator, Siemens SIMATIC IoT Gateway, ICONICS Suite,
Kepware KEPServerEX. Teaching this pattern is itself a learning
outcome.

## Where it lives in the fabric

Per student N, the gateway runs as a clab container at `10.30.N.180`
on `pcn-br0` (the student's internal Process Control Network). It polls
the student's ESP32 over the classroom WiFi (egress NAT'd through the
firewall container), and exposes a Modbus TCP slave on `:502`.

```
[ESP32 on classroom WiFi]            (REST :80)
        ▲
        │ HTTP GET /sensor/<x> every POLL_INTERVAL_S
        │
modbus-gateway @ 10.30.N.180  ←── poll ◀── modbus-master @ 10.30.N.43
   (this container)                ◀── OpenPLC #1 @ 10.30.N.60
                                   ◀── OpenPLC #2 @ 10.30.N.61
```

## Register map (v1)

| Holding Register | Meaning | Type | Range |
|---|---|---|---|
| `HR[0]` | uptime_high (= uptime >> 16) | unsigned 16-bit | 0 .. 65535 |
| `HR[1]` | uptime_low (= uptime & 0xFFFF) | unsigned 16-bit | 0 .. 65535 |
| `HR[2]` | mcu_temperature × 10 | signed 16-bit | -3000 .. 1500 (typical) |
| `HR[3]` | wifi_rssi | signed 16-bit | -100 .. 0 (dBm) |
| `HR[4]` | status_flag | unsigned 16-bit | 0=last poll errored, 1=fresh |
| `HR[5]` | last_poll_age_ms | unsigned 16-bit | ms taken by last REST fetch |
| `HR[6]` .. `HR[15]` | reserved | — | — |

To add a sensor:
1. Plug it into the ESP32, add the YAML block in
   `teacher/esphome/otlab-esp-<n>.yaml`, OTA-flash.
2. Add a corresponding REST endpoint name to the `endpoints` dict in
   `gateway.py`, and a `_store(addr, ...)` line in the polling loop.
3. Document the new register in this README.

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `ESP32_HOST` | *(required)* | hostname/IP of the ESP32 to poll (e.g. `10.20.30.201` or `otlab-esp-student-01.local`) |
| `MODBUS_LISTEN_PORT` | `502` | Modbus TCP port to serve |
| `POLL_INTERVAL_S` | `1.0` | how often to fetch from the ESP32 |
| `REST_TIMEOUT_S` | `2.0` | HTTP request timeout per call |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |

## Build

```sh
docker build -t otlab/modbus-gateway:latest -f gateway/Dockerfile gateway/
```

(The student-side `install-virtual-lab.sh` builds it as part of the
classroom-mode deploy.)

## Manual test

After the fabric is deployed, poll the gateway from inside the
`modbus-master` container (or any container on `pcn-br0`):

```sh
ssh otadmin@<student-pi> 'sudo docker exec clab-otlab-modbus-master python3 -c "
from pymodbus.client.tcp import ModbusTcpClient
c = ModbusTcpClient(\"10.30.1.180\", port=502)
c.connect()
r = c.read_holding_registers(0, 6)
if r.isError():
    print(\"FAIL:\", r)
else:
    print(\"uptime:\", (r.registers[0] << 16) | r.registers[1])
    print(\"mcu_temp:\", r.registers[2] / 10.0, \"°C\")
    print(\"rssi:\", r.registers[3] if r.registers[3] < 32768 else r.registers[3] - 65536, \"dBm\")
    print(\"status_flag:\", r.registers[4])
    print(\"last_poll_age_ms:\", r.registers[5])
"'
```

## Integrating with OpenPLC

Edit OpenPLC #1's slave-device config (via `bootstrap-l1-plc-role.sh`
or the OpenPLC web UI) to add a Modbus TCP slave entry:

- Slave name: `esp32-via-gateway`
- Slave type: Modbus TCP
- IP: `10.30.<N>.180`
- Port: `502`
- Slave ID: `1`
- Holding registers: start `0`, count `6`

OpenPLC's `softplc1-sensor-monitor.st` (or any ladder logic) can then
read these registers and act on them — e.g. set an output coil if the
MCU temperature crosses a threshold, raise a fault alarm if the
status_flag drops to 0.

## See also

- `teacher/esphome/` — ESPHome firmware for the ESP32 board itself
- `virtual/topologies/otlab.clab.yaml.tmpl` — the topology this gateway
  is added to
