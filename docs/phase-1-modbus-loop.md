# Phase 1: Modbus loop between the two real PLCs

Status: **half done.** The sensor-sim half (a Modbus TCP slave running on `softplc-2`) is live and reachable from `softplc-1` over the lab segment. OpenPLC integration on `softplc-1` (configuring it as a Modbus master polling the simulator, then writing a small ladder/ST program against the values) is the next sub-chunk.

Last updated: 2026-05-06.

## Goal

First time data flows between the two real PLC hosts on the lab network. Specifically:

```
            sensor-sim (Modbus TCP slave)            OpenPLC program (Modbus master)
            on softplc-2 :5020                       on softplc-1, polls softplc-2
                  │                                                ▲
                  │     reads HR + coils                           │
                  └────────────► via lab segment ──────────────────┘
                          10.20.30.0/24, eth0
```

Why this milestone matters: it proves end-to-end network plumbing between the two PLCs, validates that OpenPLC's Slave Devices feature works as documented, and produces the first packet capture of "real" lab Modbus traffic — useful as a teaching artifact (`reference/captures/phase1-sensor-sim-cross-pi.pcap`).

## What's done

### `plc/sensor-sim.py` — pure-stdlib Modbus TCP slave

A small async Modbus TCP server simulating a remote sensor package. Listens on TCP/5020 (chosen so it doesn't collide with OpenPLC, which owns TCP/502 on the same host).

**Address map:**

| Modbus addr | Symbol | Engineering value |
|---|---|---|
| Holding 40001 | `TANK_LEVEL_PCT` | sine wave 25.0 → 75.0 %, period 60 s |
| Holding 40002 | `WATER_TEMP_F` | cosine wave 65.0 → 75.0 °F, period 120 s |
| Holding 40003 | `DISCHARGE_PRESS` | sawtooth 50.0 → 80.0 PSI, period 300 s |
| Holding 40004 | `HEARTBEAT` | seconds-since-process-start, wraps at 65535 |
| Coil 1 | `RUNNING` | always `1` while the sim is up |
| Coil 2 | `HIGH_TEMP_ALARM` | `1` if water temp > 73.0 °F else `0` |

All values are 16-bit unsigned scaled integers (0.1 unit per LSB) for the analog registers. Out-of-range reads return Modbus exception code 0x02 (Illegal Data Address). Function codes 1, 2, 3, 4 are supported; everything else returns 0x01 (Illegal Function).

**Why pure stdlib instead of pymodbus:**

We tried pymodbus 3.13 first (the version pinned in `requirements.txt`). The client side works fine. The server side does not — pymodbus 3.13 is mid-migration to a new SimData/SimDevice API, and the deprecated `ModbusDeviceContext` codepath returns `ExcCodes.DEVICE_BUSY` for both reads and writes via `async_setValues` / `async_getValues`. The server's read handler immediately rejects that with `TypeError("Illegal external call to server.async_getValues")` because `DEVICE_BUSY` isn't a list. So the deprecated path doesn't just print a warning — it's lethal.

Three options surfaced:

1. Pin pymodbus to ≤3.6 (last version where the old API worked) — leaves us tied to a stale dependency
2. Switch to the new SimData/SimDevice config — works, but adds API churn risk for a still-evolving library
3. Implement the slice of Modbus TCP we need directly with `asyncio.start_server` and `struct` — about 60 lines of protocol code, no external dependencies, immune to upstream churn

Picked (3). It's also the right pedagogical choice: students of an ICS lab benefit from seeing the actual MBAP header + PDU bytes rather than them being abstracted away. The whole protocol slice fits in one readable file.

### `plc/sensor-sim.service` — systemd unit

Standard `Type=simple` unit, runs as `otadmin`, restarts on failure, enabled to start on boot. Logs (heartbeat ticks every 10s + any errors) go to the systemd journal:

```bash
journalctl -u sensor-sim -f
systemctl status sensor-sim
```

### `scripts/install-sensor-sim.sh` — one-shot installer

Pushes `sensor-sim.py` to `softplc-2:~/lab/sensor-sim.py`, the unit file to `/etc/systemd/system/`, runs `daemon-reload`, enables and (re)starts the service. Idempotent — re-run after edits to refresh.

```bash
./scripts/install-sensor-sim.sh
# defaults to otadmin@RASPLC02.local. Pass a different host arg if needed.
```

### `reference/captures/phase1-sensor-sim-cross-pi.pcap` — wire capture

Captured with `tcpdump -i eth0 -w` on `softplc-2` while `softplc-1` issued 4 Modbus reads. 19 packets, 1.7 KB. Useful pcap for:

- Showing students the Modbus TCP MBAP header (transaction ID, protocol ID, length, unit ID)
- Showing FC 3 request structure (start address + count) and response (byte count + register values, big-endian)
- Showing how Modbus rides on a single persistent TCP connection rather than reconnecting per request

Open in Wireshark — the dissector recognizes Modbus on any port if you right-click → Decode As → Modbus/TCP.

## Verification: cross-Pi probe

Run on `softplc-1` (`10.20.30.111`):

```bash
source ~/lab/.venv-modern/bin/activate
python3 -c '
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient("10.20.30.49", port=5020); c.connect()
hr = c.read_holding_registers(address=0, count=4, device_id=0)
co = c.read_coils(address=0, count=2, device_id=0)
print("hr:", hr.registers, " co:", co.bits[:2])
c.close()
'
```

Expected output (values change with elapsed time but should be in range):

```
hr: [350-750, 650-750, 500-800, 0-65535]   tank, temp, press, heartbeat
co: [True, True-or-False]                   running, high-temp-alarm
```

If reads succeed, Phase 1's wire half is healthy.

## What's NOT done yet (next sub-chunk)

OpenPLC on `softplc-1` doesn't yet poll the simulator. Plan:

1. **Configure OpenPLC's Slave Devices.** Open the web UI at `http://10.20.30.111:8080`, log in, go to Slave Devices, add a new device:
   - Name: `sensor-sim`
   - Type: Generic Modbus TCP Device
   - IP: `10.20.30.49`
   - Port: `5020`
   - Slave ID: `1` (sensor-sim accepts any unit ID since `single=True`-equivalent in our impl)
   - Holding Registers: Start `0`, Size `4` → maps to `%QW100..%QW103` (or wherever OpenPLC assigns)
   - Coils: Start `0`, Size `2` → `%QX100.0..%QX100.1`

2. **Write a small ST program** that does something visible with the polled values:
   - Trigger an OpenPLC variable when `HIGH_TEMP_ALARM` goes high
   - Mirror `TANK_LEVEL_PCT` to a gauge variable readable via OpenPLC's own Modbus TCP server (port 502)
   - Increment a local counter every time `HEARTBEAT` changes, as a "did we lose the link" check

3. **Smoke test:** poll `softplc-1`'s OpenPLC over Modbus TCP/502 from `softplc-2` (or laptop) and verify the mirrored values match what the sensor-sim is producing. That closes the loop: PLC #2 → simulator → PLC #1 → exposed back via Modbus TCP for any other client.

4. **Bonus:** capture a longer pcap during normal operation (a minute or so) and add it to `reference/captures/`. Real OpenPLC Modbus polls have a distinctive cadence that's worth showing students.

## Lessons from this sub-chunk

- **pymodbus 3.13 server path is broken for the deprecated context API.** Don't fight it; bypass it. The client API (read_holding_registers, read_coils) works fine.
- **Pure-stdlib Modbus TCP is a 60-line job.** It's small enough to keep, and it's better teaching material than a library wrapper.
- **Address conventions:** the sensor-sim treats wire addresses as 0-based (so `read_holding_registers(address=0, count=4)` returns 40001..40004 in the address map). pymodbus client also uses 0-based on the wire. OpenPLC's Slave Devices form may use 1-based labeling — worth double-checking when we get there.
- **systemd + sensor-sim is fast to set up.** No port conflict with OpenPLC because we picked TCP/5020 deliberately. If anyone bumps OpenPLC's port, both can coexist anywhere.
