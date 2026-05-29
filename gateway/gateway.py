#!/usr/bin/env python3
"""
otlab-modbus-gateway — IoT-to-OT protocol translator.

Polls an ESP32 (running ESPHome REST API) every POLL_INTERVAL_S seconds,
maps sensor values into a Modbus TCP holding-register space, and serves
that to whatever in-fabric OT device wants to poll it (OpenPLC,
modbus-master, etc.).

This is the canonical pattern in real industrial automation: an "IoT
gateway" sits between modern HTTP/REST devices and legacy fieldbus
protocols. Real-world equivalents: Schneider EcoStruxure Building
Operator, Siemens SIMATIC IPC IoT Gateway, ICONICS Suite, Kepware
KEPServerEX.

Register map (first revision — extend as sensors get added):

    HR[0]  uptime_high          (uptime >> 16)        unsigned
    HR[1]  uptime_low           (uptime & 0xFFFF)     unsigned
    HR[2]  mcu_temperature_x10  (temp * 10)           signed
    HR[3]  wifi_rssi            (dBm)                 signed
    HR[4]  status_flag          0=stale 1=fresh       unsigned
    HR[5]  last_poll_age_ms     ms since last fetch   unsigned
    HR[6-15] reserved for future sensors

Env vars:
    ESP32_HOST              hostname/IP of the ESP32 (e.g. 10.20.30.201)
    MODBUS_LISTEN_PORT      Modbus TCP port to serve on (default 502)
    POLL_INTERVAL_S         REST polling cadence (default 1.0)
    REST_TIMEOUT_S          HTTP timeout per call (default 2.0)
    LOG_LEVEL               INFO / DEBUG / WARNING (default INFO)

Health/observability — logs to stdout:
    [poll] every interval, one line: uptime, temp, rssi, fetch latency
    [modbus] every Modbus request — read/write address + count
    [error] any REST or Modbus error, with context
"""

import asyncio
import logging
import os
import signal
import struct
import sys
import time

import httpx
from pymodbus.datastore import ModbusServerContext, ModbusSlaveContext, ModbusSequentialDataBlock
from pymodbus.server import StartAsyncTcpServer

# ── config from env ──────────────────────────────────────────────────
ESP32_HOST = os.environ.get("ESP32_HOST", "")
MODBUS_LISTEN_PORT = int(os.environ.get("MODBUS_LISTEN_PORT", "502"))
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "1.0"))
REST_TIMEOUT_S = float(os.environ.get("REST_TIMEOUT_S", "2.0"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("otlab-modbus-gateway")

if not ESP32_HOST:
    log.error("ESP32_HOST env var is required (e.g. 10.20.30.201)")
    sys.exit(1)

# ── Modbus data store ────────────────────────────────────────────────
# 16 holding registers, default 0. We update them from the polling loop.
NUM_REGISTERS = 16
hr_block = ModbusSequentialDataBlock(0, [0] * NUM_REGISTERS)
# zero_mode=True so client PDU address 0 maps to our datablock address 0.
# Default (False) adds 1 to incoming addresses (legacy 1-indexed register
# numbering) which shifts everything by 1 from the client's perspective.
slave_ctx = ModbusSlaveContext(hr=hr_block, zero_mode=True)
server_ctx = ModbusServerContext(slaves=slave_ctx, single=True)


# ── helpers ──────────────────────────────────────────────────────────
def _signed_to_unsigned16(v: int) -> int:
    """Convert signed int (-32768..32767) to unsigned register (0..65535)."""
    if v < 0:
        v += 65536
    return v & 0xFFFF


def _store(addr: int, value: int) -> None:
    """Write one holding register, with logging on change."""
    current = hr_block.getValues(addr, 1)[0]
    value = value & 0xFFFF
    if current != value:
        log.debug("HR[%d]: %d -> %d", addr, current, value)
    hr_block.setValues(addr, [value])


# ── polling loop ─────────────────────────────────────────────────────
async def poll_esp32():
    """Hit ESP32 REST endpoints + update Modbus registers."""
    base = f"http://{ESP32_HOST}"
    endpoints = {
        "uptime": "/sensor/uptime",
        "mcu_temperature": "/sensor/mcu_temperature",
        "wifi_rssi": "/sensor/wifi_rssi",
    }

    async with httpx.AsyncClient(timeout=REST_TIMEOUT_S) as client:
        while True:
            start = time.monotonic()
            results = {}
            errors = 0
            for name, path in endpoints.items():
                try:
                    r = await client.get(base + path)
                    r.raise_for_status()
                    results[name] = r.json().get("value")
                except Exception as e:
                    errors += 1
                    log.warning("REST fetch %s failed: %s", name, e)
                    results[name] = None

            fetch_ms = int((time.monotonic() - start) * 1000)

            # Map → registers (skip if value is None)
            try:
                if (uptime := results.get("uptime")) is not None:
                    u = int(uptime)
                    _store(0, (u >> 16) & 0xFFFF)
                    _store(1, u & 0xFFFF)

                if (mcu_t := results.get("mcu_temperature")) is not None:
                    _store(2, _signed_to_unsigned16(int(float(mcu_t) * 10)))

                if (rssi := results.get("wifi_rssi")) is not None:
                    _store(3, _signed_to_unsigned16(int(rssi)))

                _store(4, 1 if errors == 0 else 0)   # status flag
                _store(5, fetch_ms)                  # last fetch age (ms)
            except Exception as e:
                log.error("register-update failed: %s (results=%s)", e, results)

            log.info(
                "[poll] uptime=%s temp=%s rssi=%s fetch_ms=%d errors=%d",
                results.get("uptime"),
                results.get("mcu_temperature"),
                results.get("wifi_rssi"),
                fetch_ms,
                errors,
            )

            # Sleep until next interval
            elapsed = time.monotonic() - start
            sleep_for = max(0, POLL_INTERVAL_S - elapsed)
            await asyncio.sleep(sleep_for)


# ── main ─────────────────────────────────────────────────────────────
async def main():
    log.info(
        "otlab-modbus-gateway starting: esp32=%s, modbus=:%d, poll=%.1fs",
        ESP32_HOST,
        MODBUS_LISTEN_PORT,
        POLL_INTERVAL_S,
    )

    # Run the polling loop in the background; await StartAsyncTcpServer
    # which blocks until the server stops.
    poll_task = asyncio.create_task(poll_esp32())

    try:
        await StartAsyncTcpServer(
            context=server_ctx,
            address=("0.0.0.0", MODBUS_LISTEN_PORT),
        )
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("shutting down (KeyboardInterrupt)")
