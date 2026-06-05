#!/usr/bin/env python3
"""otlab-modbus-io — expose the physical Qwiic I/O as a Modbus TCP slave.

The ladder PLC (otlab-plc) and the control page (otlab-qwiic) already drive
the real hardware over a private REST API. This bridge puts that same
hardware on the wire as a **standard Modbus TCP device** so the rest of the
OT world can see and touch it:

    OpenPLC / SCADA / OTLab dashboard  ──Modbus/TCP:502──►  this bridge
                                                               │ REST
                                                               ▼
                                              otlab-qwiic  ──I2C──►  TMP117
                                                                     relay
                                                                     motor

Why this matters in the lab:
  * The student's ladder logic now shows up on a real fieldbus — the same
    protocol the virtual fabric (OpenPLC, modbus-master) already speaks.
  * Suricata sees genuine Modbus traffic on :502 to alert on (read/write
    function codes, register scans) — real packets for the IDS lessons.
  * One clean register contract ties the physical half of the lab to the
    virtual half.

It is bidirectional:
  * READ  registers are refreshed every poll from otlab-qwiic /api/state.
  * WRITE registers are watched every poll; when a Modbus master changes
    one, we actuate the hardware via otlab-qwiic (relay on/off, motor speed).

Register map  (holding registers, FC 3 read / FC 6,16 write, zero-indexed)

    ── live state (read-only in practice; refreshed by the poll loop) ──
    HR[0]   temp_c x10        signed   258  = 25.8 degC
    HR[1]   temp_f x10        signed   784  = 78.4 degF
    HR[2]   relay_state       0 / 1
    HR[3]   motor_a_speed     signed   -100..100 %
    HR[4]   motor_b_speed     signed   -100..100 %
    HR[5]   fresh_flag        1 = last poll ok, 0 = stale
    HR[6]   poll_age_ms       ms since the last good poll
    HR[7..9] reserved

    ── commands (a Modbus master writes these; we actuate on change) ──
    HR[10]  relay_cmd         0 = off, 1 = on        -> POST /api/relay
    HR[11]  motor_a_cmd       signed -100..100 %      -> POST /api/motor A
    HR[12]  motor_b_cmd       signed -100..100 %      -> POST /api/motor B

Signed values use two's-complement in the 16-bit register (e.g. -50 reads
as 65486). Most Modbus clients decode this for you as "INT16".

Env:
    OTLAB_MODBUS_PORT   Modbus TCP listen port      (default 502)
    OTLAB_QWIIC_URL     I/O REST base               (default http://127.0.0.1:8090)
    POLL_INTERVAL_S     poll cadence                (default 0.4)
    REST_TIMEOUT_S      HTTP timeout per call       (default 2.0)
    DASH_USER/DASH_PASS basic auth for the REST API (default otlab / P@ssw0rd!)
    LOG_LEVEL           INFO / DEBUG                 (default INFO)
"""
import asyncio
import base64
import json
import logging
import os
import time
import urllib.request

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

# ── config ────────────────────────────────────────────────────────────
MODBUS_PORT     = int(os.environ.get("OTLAB_MODBUS_PORT", "502"))
QWIIC_URL       = os.environ.get("OTLAB_QWIIC_URL", "http://127.0.0.1:8090").rstrip("/")
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "0.4"))
REST_TIMEOUT_S  = float(os.environ.get("REST_TIMEOUT_S", "2.0"))
DASH_USER       = os.environ.get("DASH_USER", "otlab")
DASH_PASS       = os.environ.get("DASH_PASS", "P@ssw0rd!")
LOG_LEVEL       = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("otlab-modbus-io")

# register addresses
R_TEMP_C, R_TEMP_F, R_RELAY, R_MOTA, R_MOTB, R_FRESH, R_AGE = range(7)
W_RELAY, W_MOTA, W_MOTB = 10, 11, 12
NUM_REGISTERS = 24

# pymodbus 3.8 removed the `zero_mode` kwarg from ModbusSlaveContext but
# KEPT the legacy default: the slave context still maps a client's wire
# address N to datablock index N+1. We confirmed this empirically (a write
# to wire HR11 landed in datablock[12]). To make the PUBLISHED register map
# above match what a Modbus master (OpenPLC, SCADA) actually reads/writes on
# the wire, we apply a +1 store offset on every access. So wire HR0 == our
# logical temp_c, exactly as documented.
STORE_OFFSET = 1
hr_block  = ModbusSequentialDataBlock(0, [0] * NUM_REGISTERS)
slave_ctx = ModbusSlaveContext(hr=hr_block)
server_ctx = ModbusServerContext(slaves=slave_ctx, single=True)


# ── REST helpers (basic auth, same creds as the qwiic service) ────────
def _auth_header():
    tok = base64.b64encode(f"{DASH_USER}:{DASH_PASS}".encode()).decode()
    return f"Basic {tok}"


def rest_get(path):
    req = urllib.request.Request(QWIIC_URL + path)
    req.add_header("Authorization", _auth_header())
    with urllib.request.urlopen(req, timeout=REST_TIMEOUT_S) as r:
        return json.loads(r.read().decode())


def rest_post(path, body):
    req = urllib.request.Request(QWIIC_URL + path,
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", _auth_header())
    with urllib.request.urlopen(req, timeout=REST_TIMEOUT_S) as r:
        return json.loads(r.read().decode())


# ── 16-bit signed <-> register helpers ────────────────────────────────
def u16(v):
    """signed int -> unsigned register value"""
    v = int(v)
    if v < 0:
        v += 65536
    return v & 0xFFFF


def s16(v):
    """register value -> signed int"""
    v = int(v) & 0xFFFF
    return v - 65536 if v >= 32768 else v


def reg_get(addr):
    return hr_block.getValues(addr + STORE_OFFSET, 1)[0]


def reg_set(addr, value):
    hr_block.setValues(addr + STORE_OFFSET, [u16(value)])


# ── the bridge loop: state -> read regs, write regs -> hardware ───────
async def bridge_loop():
    # Seed the command registers from the current state so we don't fire a
    # spurious actuation on the very first scan, and so a master that reads
    # before writing sees the real current setpoints.
    last_cmd = {"relay": None, "mota": None, "motb": None}
    try:
        s = await asyncio.to_thread(rest_get, "/api/state")
        reg_set(W_RELAY, 1 if s.get("relay") else 0)
        reg_set(W_MOTA, s.get("motor_a", 0))
        reg_set(W_MOTB, s.get("motor_b", 0))
        last_cmd = {"relay": 1 if s.get("relay") else 0,
                    "mota": s16(reg_get(W_MOTA)),
                    "motb": s16(reg_get(W_MOTB))}
        log.info("seeded command registers from current state: %s", last_cmd)
    except Exception as e:
        log.warning("could not seed from /api/state: %s", e)

    while True:
        start = time.monotonic()

        # 1) WRITE side: did a Modbus master change a command register?
        try:
            want_relay = 1 if reg_get(W_RELAY) else 0
            want_mota  = s16(reg_get(W_MOTA))
            want_motb  = s16(reg_get(W_MOTB))

            if want_relay != last_cmd["relay"]:
                rest_post("/api/relay", {"on": bool(want_relay)})
                log.info("[cmd] relay -> %s", "ON" if want_relay else "off")
                last_cmd["relay"] = want_relay
            if want_mota != last_cmd["mota"]:
                rest_post("/api/motor", {"channel": "A", "speed": want_mota})
                log.info("[cmd] motor A -> %d%%", want_mota)
                last_cmd["mota"] = want_mota
            if want_motb != last_cmd["motb"]:
                rest_post("/api/motor", {"channel": "B", "speed": want_motb})
                log.info("[cmd] motor B -> %d%%", want_motb)
                last_cmd["motb"] = want_motb
        except Exception as e:
            log.warning("command actuation failed: %s", e)

        # 2) READ side: refresh live state into the read registers.
        try:
            s = await asyncio.to_thread(rest_get, "/api/state")
            tc = s.get("temp_c")
            tf = s.get("temp_f")
            reg_set(R_TEMP_C, round(tc * 10) if tc is not None else 0)
            reg_set(R_TEMP_F, round(tf * 10) if tf is not None else 0)
            reg_set(R_RELAY, 1 if s.get("relay") else 0)
            reg_set(R_MOTA, s.get("motor_a", 0))
            reg_set(R_MOTB, s.get("motor_b", 0))
            reg_set(R_FRESH, 1)
            reg_set(R_AGE, 0)
            log.debug("[poll] temp=%.2fC/%.1fF relay=%s motA=%s motB=%s",
                      tc or 0, tf or 0, s.get("relay"),
                      s.get("motor_a"), s.get("motor_b"))
        except Exception as e:
            reg_set(R_FRESH, 0)
            age = reg_get(R_AGE)
            reg_set(R_AGE, min(65535, age + int(POLL_INTERVAL_S * 1000)))
            log.warning("state poll failed: %s", e)

        elapsed = time.monotonic() - start
        await asyncio.sleep(max(0.0, POLL_INTERVAL_S - elapsed))


# ── main ──────────────────────────────────────────────────────────────
async def main():
    log.info("otlab-modbus-io starting: modbus=:%d qwiic=%s poll=%.2fs",
             MODBUS_PORT, QWIIC_URL, POLL_INTERVAL_S)
    task = asyncio.create_task(bridge_loop())
    try:
        await StartAsyncTcpServer(context=server_ctx,
                                  address=("0.0.0.0", MODBUS_PORT))
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("shutting down (KeyboardInterrupt)")
