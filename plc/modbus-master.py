#!/usr/bin/env python3
"""modbus-master.py — pure-stdlib Modbus TCP master that polls a
configurable outstation in a tight loop.

Role in the V1 OTLab fabric: this is the legitimate master, polling
the virtual sensor-sim outstation on pcn-br0. Generates the canonical
"normal" Modbus traffic pattern that students see on the wire and that
attack/detect scenarios are built around. Replaces OpenPLC for the
master role in the virtual lab — OpenPLC instances (plc-1-virt,
plc-2-virt) keep their web UIs for IEC 61131-3 lessons, but the
actual on-the-wire master traffic comes from this container.

Why pure-stdlib + pymodbus instead of OpenPLC for V1:
  - OpenPLC v3 source-build needs a patched libmodbus + libopendnp3
    that aren't packaged on debian:bookworm-slim ARM64 → 4-hour rabbit
    hole to ship a working compile chain. Skipped for V1.
  - This is ~80 lines of Python, runs in a 158 MB image, and gives us
    deterministic + observable polling traffic for teaching scenarios.
  - Real-plant equivalent: Modicon master block in a control loop, or
    an AVEVA / Ignition driver. Conceptually identical for the wire-
    level lessons.

Wire pattern: every 100 ms, alternates FC3 (read 4 holding registers)
and FC2 (read 2 discrete inputs / coils as bits) against the slave.
~20 packets/second on pcn-br0 — same cadence OpenPLC's runtime would
produce.

Configuration via env (all optional):
  SLAVE_IP       outstation address                    (default 10.20.30.70)
  SLAVE_PORT     outstation port                       (default 5020)
  POLL_PERIOD_S  delay between polls in seconds        (default 0.1 = 100 ms)
  HR_COUNT       number of holding registers to read   (default 4)
  COIL_COUNT     number of coils to read               (default 2)
  LOG_INTERVAL_S how often to log a summary tick       (default 5.0)

Usage:
  python3 modbus-master.py
  SLAVE_IP=10.20.30.70 python3 modbus-master.py
"""

from __future__ import annotations
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException


# ── config ────────────────────────────────────────────────────────────
SLAVE_IP        = os.environ.get("SLAVE_IP",        "10.20.30.70")
SLAVE_PORT      = int(os.environ.get("SLAVE_PORT",  "5020"))
POLL_PERIOD_S   = float(os.environ.get("POLL_PERIOD_S",  "0.1"))
HR_COUNT        = int(os.environ.get("HR_COUNT",    "4"))
COIL_COUNT      = int(os.environ.get("COIL_COUNT",  "2"))
LOG_INTERVAL_S  = float(os.environ.get("LOG_INTERVAL_S", "5.0"))
DEVICE_ID       = int(os.environ.get("DEVICE_ID",   "0"))

# State file — written each tick, mounted as a shared docker volume so
# the dashboard container can read it without docker-socket access.
# Set STATE_FILE='' to disable.
STATE_FILE      = os.environ.get(
    "STATE_FILE",
    "/var/lib/otlab/mm-state/last.json",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s modbus-master: %(message)s",
)
log = logging.getLogger("modbus-master")


def open_client() -> Optional[ModbusTcpClient]:
    """Open a TCP connection. Returns None on connect failure (caller retries)."""
    c = ModbusTcpClient(SLAVE_IP, port=SLAVE_PORT, timeout=2)
    try:
        if c.connect():
            return c
    except Exception as e:
        log.warning("connect to %s:%d raised %s: %s", SLAVE_IP, SLAVE_PORT, type(e).__name__, e)
    return None


def write_state(polls_ok: int, polls_err: int, rate: float,
                hr: list[int], coils: list[bool], started_t: float) -> None:
    """Atomically write the latest tick state for the dashboard to read.

    Schema is stable — anything reading this file (dashboard, future
    Prometheus exporter, etc.) can rely on it. Atomic write via
    write-temp-then-rename so a partial read can never observe half-
    written JSON."""
    if not STATE_FILE:
        return
    try:
        state = {
            "ts":          datetime.now().isoformat(timespec='seconds'),
            "polls_ok":    polls_ok,
            "polls_err":   polls_err,
            "rate_per_s":  round(rate, 2),
            "hr":          hr,
            "coils":       [bool(c) for c in coils],
            "uptime_s":    round(time.time() - started_t, 1),
            "slave":       f"{SLAVE_IP}:{SLAVE_PORT}",
        }
        path = Path(STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state) + "\n")
        tmp.replace(path)
    except Exception as e:
        log.warning("state write failed: %s: %s", type(e).__name__, e)


def main() -> int:
    log.info(
        "starting: slave=%s:%d period=%.3fs HR=%d Coils=%d device_id=%d state=%s",
        SLAVE_IP, SLAVE_PORT, POLL_PERIOD_S, HR_COUNT, COIL_COUNT, DEVICE_ID,
        STATE_FILE or "(disabled)",
    )

    started_t = time.time()
    client: Optional[ModbusTcpClient] = None
    last_log_t = 0.0
    polls_ok = 0
    polls_err = 0
    last_hr: list[int] = []
    last_coils: list[bool] = []
    fc_alt = 3   # alternate FC3 (regs) ↔ FC2 (discretes)

    while True:
        if client is None:
            client = open_client()
            if client is None:
                # Backoff: re-attempt every 2s while sensor-sim isn't up
                time.sleep(2)
                continue

        try:
            if fc_alt == 3:
                r = client.read_holding_registers(address=0, count=HR_COUNT, device_id=DEVICE_ID)
                if r.isError():
                    raise ModbusException(str(r))
                last_hr = list(r.registers[:HR_COUNT])
                fc_alt = 2
            else:
                r = client.read_discrete_inputs(address=0, count=COIL_COUNT, device_id=DEVICE_ID)
                if r.isError():
                    raise ModbusException(str(r))
                last_coils = [bool(b) for b in r.bits[:COIL_COUNT]]
                fc_alt = 3
            polls_ok += 1
        except (ConnectionException, ModbusException, OSError) as e:
            polls_err += 1
            log.warning("poll error (%d): %s — reconnecting", polls_err, e)
            try:
                client.close()
            except Exception:
                pass
            client = None
            time.sleep(0.5)
            continue

        # Periodic summary tick (so the journal isn't 20 lines/sec) +
        # write state file for the dashboard to read.
        now = time.time()
        if now - last_log_t >= LOG_INTERVAL_S:
            rate = (polls_ok + polls_err) / max(LOG_INTERVAL_S, 0.001)
            log.info(
                "tick polls_ok=%d polls_err=%d rate=%.1f/s hr=%s coils=%s",
                polls_ok, polls_err, rate, last_hr, last_coils,
            )
            write_state(polls_ok, polls_err, rate, last_hr, last_coils, started_t)
            last_log_t = now
            polls_ok = polls_err = 0

        time.sleep(POLL_PERIOD_S)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        log.info("interrupted — bye")
        sys.exit(0)
