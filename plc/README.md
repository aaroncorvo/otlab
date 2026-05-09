# plc/

PLC-side code: OpenPLC programs (Structured Text, ladder, function block diagram), pymodbus slave/server scripts, systemd unit files. Anything that runs *on* `l1-plc-01` (and, when the backfill arrives, `l1-plc-02`).

## Current contents

| File | Runs on | Purpose |
|---|---|---|
| `sensor-sim.py` | l1-plc-01 | Modbus TCP slave on port 5020 simulating a remote sensor package. Pure stdlib (asyncio + struct). See [`docs/phase-1-modbus-loop.md`](../docs/phase-1-modbus-loop.md) for design notes. |
| `sensor-sim.service` | l1-plc-01 | systemd unit for `sensor-sim.py`. Goes in `/etc/systemd/system/`. |
| `dnp3-outstation.py` | l1-plc-01 | DNP3 outstation on TCP/20000. Same scenario data, different wire protocol. |
| `dnp3-outstation.service` | l1-plc-01 | systemd unit for `dnp3-outstation.py`. |
| `softplc1-sensor-monitor.st` | l1-plc-01 | OpenPLC Structured Text program — polls sensor-sim, mirrors registers. |
| `scenarios/*.json` | l1-plc-01 | Scenario substrates (water-treatment / power-substation / natural-gas-pipeline). The active scenario controls sensor-sim + dnp3-outstation waveforms. |
| `tests/test-*.{py,sh}` | run from anywhere on the lab segment | Attack/recon test library — auto-discovered by the dashboard's Test Library panel. |

Deploy with:
- [`scripts/install-sensor-sim.sh`](../scripts/install-sensor-sim.sh) — copies sensor-sim + scenarios + tests onto an L1 PLC and starts the service.
- [`scripts/install-dnp3.sh`](../scripts/install-dnp3.sh) — same for DNP3 outstation.
- [`scripts/bootstrap-l1-plc-role.sh`](../scripts/bootstrap-l1-plc-role.sh) — configures OpenPLC for the master role on l1-plc-01.

## Coming soon

- l1-plc-02 backfill: when a 4th Pi lands, sensor-sim + dnp3-outstation move off l1-plc-01 onto the new box, restoring the master ↔ outstation network split.
- Phase 2: Arduino UNO sketches for Modbus RTU over USB serial (see [`esp32/`](esp32/) — placeholder for now).
