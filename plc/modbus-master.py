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
  SLAVE_IP        outstation address                   (default 10.20.30.70)
  SLAVE_PORT      outstation port                      (default 5020)
  POLL_PERIOD_S   delay between polls in seconds       (default 0.1 = 100 ms)
  HR_COUNT        number of holding registers to read  (default 4)
  COIL_COUNT      number of coils to read              (default 2)
  LOG_INTERVAL_S  how often to log a summary tick      (default 5.0)

  Secondary slave (the Modbus gateway in front of the ESP32). Disabled
  when GATEWAY_IP is unset; the main wire pattern at SLAVE_IP is
  unchanged either way.

  GATEWAY_IP            secondary slave IP             (default "" = off)
  GATEWAY_PORT          secondary slave port           (default 502)
  GATEWAY_HR_COUNT      holding regs to read           (default 6)
  GATEWAY_POLL_EVERY_N  poll once per N main ticks     (default 10 = 1 Hz at 100 ms)

Usage:
  python3 modbus-master.py
  SLAVE_IP=10.20.30.70 GATEWAY_IP=10.20.30.180 python3 modbus-master.py
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

# Secondary slave (Modbus gateway). Disabled when GATEWAY_IP is empty.
GATEWAY_IP            = os.environ.get("GATEWAY_IP", "").strip()
GATEWAY_PORT          = int(os.environ.get("GATEWAY_PORT", "502"))
GATEWAY_HR_COUNT      = int(os.environ.get("GATEWAY_HR_COUNT", "6"))
GATEWAY_POLL_EVERY_N  = max(1, int(os.environ.get("GATEWAY_POLL_EVERY_N", "10")))

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
                hr: list[int], coils: list[bool], started_t: float,
                gw: Optional[dict] = None) -> None:
    """Atomically write the latest tick state for the dashboard to read.

    Schema is stable. Anything reading this file (dashboard, future
    Prometheus exporter, etc.) can rely on it. Atomic write via
    write-temp-then-rename so a partial read can never observe half-
    written JSON.

    `gw` is an optional sub-dict with the most recent gateway poll
    (sensor values decoded from HR[0..5]). None when the gateway slave
    is disabled."""
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
        if gw is not None:
            state["gw"] = gw
        path = Path(STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state) + "\n")
        tmp.replace(path)
    except Exception as e:
        log.warning("state write failed: %s: %s", type(e).__name__, e)


def poll_gateway(client_holder: dict) -> Optional[dict]:
    """One-shot poll of the modbus-gateway slave (ESP32 sensor bridge).

    Returns a dict with decoded sensor fields, or None if the poll
    failed. Manages its own ModbusTcpClient stored in `client_holder['c']`
    so a transient gateway restart doesn't leak sockets. Errors are
    logged but never raised; the main loop is unaffected."""
    if not GATEWAY_IP:
        return None
    c = client_holder.get("c")
    try:
        if c is None or not c.connected:
            c = ModbusTcpClient(GATEWAY_IP, port=GATEWAY_PORT, timeout=2)
            if not c.connect():
                log.warning("gateway %s:%d connect failed", GATEWAY_IP, GATEWAY_PORT)
                client_holder["c"] = None
                return None
            client_holder["c"] = c

        r = c.read_holding_registers(address=0, count=GATEWAY_HR_COUNT)
        if r.isError():
            log.warning("gateway poll error: %s", r)
            return None

        regs = list(r.registers[:GATEWAY_HR_COUNT])
        # Decode the canonical gateway register map (see gateway/README.md):
        #   HR[0..1] uptime hi/lo (uint32)
        #   HR[2]    mcu_temp * 10 (int16)
        #   HR[3]    wifi_rssi (int16, dBm)
        #   HR[4]    status_flag (1=fresh, 0=stale)
        #   HR[5]    last_poll_age_ms (uint16)
        def s16(u: int) -> int:
            return u - 65536 if u >= 32768 else u
        uptime_s = (regs[0] << 16) | regs[1] if len(regs) >= 2 else None
        temp_c   = s16(regs[2]) / 10.0 if len(regs) >= 3 else None
        rssi     = s16(regs[3]) if len(regs) >= 4 else None
        status   = regs[4] if len(regs) >= 5 else None
        age_ms   = regs[5] if len(regs) >= 6 else None
        return {
            "slave":         f"{GATEWAY_IP}:{GATEWAY_PORT}",
            "raw_hr":        regs,
            "uptime_s":      uptime_s,
            "mcu_temp_C":    temp_c,
            "wifi_rssi_dBm": rssi,
            "status_flag":   status,
            "last_poll_ms":  age_ms,
        }
    except Exception as e:
        log.warning("gateway poll exception: %s: %s", type(e).__name__, e)
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
        client_holder["c"] = None
        return None


def main() -> int:
    log.info(
        "starting: slave=%s:%d period=%.3fs HR=%d Coils=%d device_id=%d state=%s",
        SLAVE_IP, SLAVE_PORT, POLL_PERIOD_S, HR_COUNT, COIL_COUNT, DEVICE_ID,
        STATE_FILE or "(disabled)",
    )
    if GATEWAY_IP:
        log.info(
            "gateway slave enabled: %s:%d HR=%d every %d ticks",
            GATEWAY_IP, GATEWAY_PORT, GATEWAY_HR_COUNT, GATEWAY_POLL_EVERY_N,
        )
    else:
        log.info("gateway slave disabled (GATEWAY_IP unset)")

    started_t = time.time()
    client: Optional[ModbusTcpClient] = None
    last_log_t = 0.0
    polls_ok = 0
    polls_err = 0
    last_hr: list[int] = []
    last_coils: list[bool] = []
    fc_alt = 3   # alternate FC3 (regs) <-> FC2 (discretes)

    # Secondary slave (gateway) state: persistent client + last decoded
    # sensor values + tick counter for paced polling.
    gw_client_holder: dict = {"c": None}
    last_gw: Optional[dict] = None
    tick = 0

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

        # Secondary slave poll — paced to GATEWAY_POLL_EVERY_N main ticks
        # so the deterministic wire pattern at SLAVE_IP stays intact.
        tick += 1
        if GATEWAY_IP and tick % GATEWAY_POLL_EVERY_N == 0:
            gw = poll_gateway(gw_client_holder)
            if gw is not None:
                last_gw = gw

        # Periodic summary tick (so the journal isn't 20 lines/sec) +
        # write state file for the dashboard to read.
        now = time.time()
        if now - last_log_t >= LOG_INTERVAL_S:
            rate = (polls_ok + polls_err) / max(LOG_INTERVAL_S, 0.001)
            gw_log = ""
            if last_gw is not None:
                gw_log = " gw_temp=%s gw_rssi=%s" % (
                    last_gw.get("mcu_temp_C"), last_gw.get("wifi_rssi_dBm"),
                )
            log.info(
                "tick polls_ok=%d polls_err=%d rate=%.1f/s hr=%s coils=%s%s",
                polls_ok, polls_err, rate, last_hr, last_coils, gw_log,
            )
            write_state(polls_ok, polls_err, rate, last_hr, last_coils,
                        started_t, gw=last_gw)
            last_log_t = now
            polls_ok = polls_err = 0

        time.sleep(POLL_PERIOD_S)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        log.info("interrupted — bye")
        sys.exit(0)
