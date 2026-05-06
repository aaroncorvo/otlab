# Phase 1: Modbus loop between the two real PLCs

Status: **complete.** sensor-sim runs on `softplc-2:5020` as a systemd service; `softplc-1`'s OpenPLC polls it every 100 ms via the Slave Devices feature, mirrors the values into local variables, and re-exposes them on its own Modbus TCP server (port 502) along with link-liveness telemetry. Anything else on the lab segment can read the sensor data through `softplc-1` as if it were a normal industrial PLC with field instruments attached.

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

Standard `Type=simple` unit, runs as `otuser` (the lab's non-privileged account), restarts on failure, enabled to start on boot. Logs (heartbeat ticks every 10s + any errors) go to the systemd journal:

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

## OpenPLC integration on softplc-1

### `plc/softplc1-sensor-monitor.st`

Small Structured Text program that:

- Maps the slave-device variables OpenPLC pulls in from sensor-sim (`%IW100..%IW103` for holding registers, `%IX100.0` and `%IX100.1` for discrete inputs)
- Mirrors them to local outputs (`%QW0..%QW3`, `%QX0.0`, `%QX0.1`) — anything in the `%Q` space is automatically exposed by OpenPLC's own Modbus TCP server on port 502
- Adds **link-liveness telemetry**: tracks whether sensor-sim's heartbeat counter is advancing. If it stops for more than ~3 seconds (30 scans at 100 ms each), it clears `out_link_ok` (`%QW4`) and increments `out_link_loss` (`%QW5`) on each drop. That's the kind of remote-endpoint health check real SCADA does for every field device.

Internal state variables (`last_hb`, `unchanged_scans`, `link_alive`) live in a separate `VAR ... END_VAR` block from the located ones because matiec rejects mixing located and non-located vars in a single block.

### Slave-device configuration

Stored in OpenPLC's `openplc.db` SQLite at `~/OpenPLC_v3/webserver/`. Single row in the `Slave_dev` table:

| Field | Value |
|---|---|
| `dev_name` | `sensor-sim` |
| `dev_type` | `TCP` |
| `slave_id` | 1 |
| `ip_address` | `10.20.30.49` |
| `ip_port` | `5020` |
| `di_start`, `di_size` | 0, 2 |
| `hr_read_start`, `hr_read_size` | 0, 4 |
| `pause` | 100 (ms) |

Plus `Settings.Start_run_mode = "true"` so the runtime auto-starts when systemd brings up the OpenPLC service.

### `mbconfig.cfg` regeneration

OpenPLC's Modbus master code reads `~/OpenPLC_v3/webserver/mbconfig.cfg` (a flat file derived from the `Slave_dev` table). The web UI generates it whenever the Slave Devices form is submitted. Configuring slave devices via direct DB INSERT — like our installer does — does *not* regenerate this file, so the runtime sees the configured device but no polling actually happens. Symptom: port 502 responds but mirrored values stay at zero forever.

The fix is to regenerate `mbconfig.cfg` ourselves. The installer script reproduces `webserver.py`'s `generate_mbconfig()` logic in 25 lines of Python.

### `scripts/bootstrap-openplc-role.sh`

Idempotent role-config script. The Phase 1 setup for softplc-1 is the canonical `softplc-1` role — running this script reproduces the full state from this repo (program file, slave device row, mbconfig.cfg, hardware target, Start_run_mode, optional password):

```bash
# canonical softplc-1 deployment, including password rotation:
OPENPLC_PASSWORD='P@ssw0rd!' \
    ./scripts/bootstrap-openplc-role.sh otadmin@RASPLC01.local softplc-1
```

What it does, in order: stop the OpenPLC service → pin hardware target = "rpi" → (if `OPENPLC_PASSWORD` set) bcrypt-hash and write the Users row → set `Start_run_mode=true` → role-specific: `scp` the `.st` file, upsert Programs row, write Slave_dev row, write `active_program` → compile via `compile_program.sh` → regenerate `mbconfig.cfg` → start the service → verify Modbus on `:502`.

Bootstrap pre-req: `scripts/bootstrap-pi.sh` against the same Pi first if OpenPLC isn't yet installed. See [`scripts/README.md`](../scripts/README.md) for the full deployment workflow.

To add new roles or slave-device combinations, edit the `case "$ROLE" in ...` block in `bootstrap-openplc-role.sh`.

### Verification

```bash
# from softplc-1 itself (or any host on the lab segment)
source ~/lab/.venv-modern/bin/activate
python3 -c '
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient("10.20.30.111", port=502); c.connect()
hr = c.read_holding_registers(address=0, count=6, device_id=0)
co = c.read_coils(address=0, count=2, device_id=0)
print("hr:", hr.registers)   # [tank, temp, press, hb, link_ok, link_loss]
print("co:", co.bits[:2])    # [running, alarm]
c.close()
'
```

Real output during normal operation:

```
    time      tank    temp    press    hb     link_ok  link_loss  running  alarm
  05:12:44     66.8%   65.3F   74.7P    1147     1        0         1        0
  05:12:46     70.0%   65.5F   74.9P    1148     1        0         1        0
  05:12:48     72.8%   65.8F   75.1P    1150     1        0         1        0
```

Heartbeat increments every 1-2 reads (matches sensor-sim's 1-second update period and our 2-second probe spacing). All other values are sensor-sim's live waveforms passed through OpenPLC's poll → ST mirror → local Modbus exposure.

**Pcap of normal polling:** `reference/captures/phase1-openplc-poll-loop.pcap` — 30 seconds, 588 packets, ~10 polls/second of FC 2 (discrete inputs) and FC 3 (holding registers) requests with TCP keepalive + ACK overhead. Useful Wireshark teaching artifact: shows the actual ICS poll cadence on the wire, with FC 3 / FC 2 alternating, persistent TCP connection, and uniform inter-poll spacing.

## What's next: physical I/O (Phase 2)

This phase proved the **software** loop. The next phase makes it **physical**:

- **softplc-1 pushbutton input**: wire a uxcell 12 mm momentary to the Freenove HAT (one terminal to a free GPIO, other to GND, INPUT_PULLUP in software). Map to `%IX` in OpenPLC's hardware layer. Update the ST program to write a Modbus coil softplc-2 will read.
- **softplc-2 relay-driven indicators**: write a custom OpenPLC hardware layer for the Waveshare 3-CH HAT (BCM 26/20/21, active-LOW). Wire AD16 dual-color indicator to CH1 (SPDT trick: NC=red, NO=green, COM=+24 V), LED strip to CH2 (SPST gate of the strip's own 12 V brick). softplc-2's ST program drives those relays based on coils softplc-1 wrote, plus the high-temp alarm bit `sensor-sim` already exposes.
- **End-to-end demo**: button press on softplc-1 → green light on softplc-2 (and vice versa). High-temp alarm triggered by tweaking sensor-sim's threshold → LED strip on. The full SCADA cause-and-effect chain on real hardware.

Blocked on the OMCH EDR-120-24 PSU arriving (the AD16 indicators we have are 24 V; the existing Mean Well in the lab is 12 V which won't drive them reliably). Software work — custom hardware layer, ST updates, button wiring on softplc-1's 5 V Freenove side — can move ahead in parallel.

See [`lab-architecture.md`](lab-architecture.md#physical-io-plan-per-soft-plc) for the per-host I/O matrix.

## Lessons captured

- **pymodbus 3.13 server path is broken for the deprecated context API.** Don't fight it; bypass it. The client API (read_holding_registers, read_coils) works fine.
- **Pure-stdlib Modbus TCP is a 60-line job.** It's small enough to keep, and it's better teaching material than a library wrapper.
- **Address conventions:** the sensor-sim treats wire addresses as 0-based (so `read_holding_registers(address=0, count=4)` returns 40001..40004 in the address map). pymodbus client also uses 0-based on the wire.
- **systemd + sensor-sim** doesn't conflict with OpenPLC because we picked TCP/5020 deliberately. If anyone bumps OpenPLC's port, both can coexist anywhere.
- **OpenPLC's web UI vs DB.** The web UI is the supported config path, but everything it does is reproducible by writing to `openplc.db`, dropping `.st` files into `st_files/`, calling `compile_program.sh`, and (this is the trap) **regenerating `mbconfig.cfg`** which only happens automatically when the web UI's Slave Devices form is submitted. Direct DB edits without regenerating mbconfig leave the runtime believing it has zero slave devices.
- **matiec quirks** worth knowing for any future ST program:
  - Variable declarations cannot carry default values (`x : INT := 0;` rejected). Vars init to zero/false anyway.
  - Located variables (with `AT %xx`) and non-located ones can't be mixed in one VAR block. Split into two.
  - Type strictness on arithmetic: `WORD + INT` is rejected. Use the same type both sides, or pick `INT` for vars that need integer math.
  - No em-dashes in comments. ASCII only.
