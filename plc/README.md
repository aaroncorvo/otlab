# plc/

PLC-side code: OpenPLC programs (Structured Text, ladder, function block diagram), pymodbus slave/server scripts, systemd unit files. Anything that runs *on* `softplc-1` or `softplc-2`.

## Current contents

| File | Runs on | Purpose |
|---|---|---|
| `sensor-sim.py` | softplc-2 | Modbus TCP slave on port 5020 simulating a remote sensor package. Pure stdlib (asyncio + struct). See [`docs/phase-1-modbus-loop.md`](../docs/phase-1-modbus-loop.md) for design notes. |
| `sensor-sim.service` | softplc-2 | systemd unit for `sensor-sim.py`. Goes in `/etc/systemd/system/`. |

Deploy with [`scripts/install-sensor-sim.sh`](../scripts/install-sensor-sim.sh) — copies both files into place and starts the service.

## Coming soon

- OpenPLC ST/ladder programs for Phase 1 step 2 (polling sensor-sim, exposing values via OpenPLC's own Modbus TCP server on `softplc-1:502`)
- Phase 2: Arduino UNO sketches for Modbus RTU over USB serial
