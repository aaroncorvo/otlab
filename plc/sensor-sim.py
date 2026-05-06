#!/usr/bin/env python3
"""
sensor-sim.py — minimal Modbus TCP slave that pretends to be a small remote
sensor package on the lab segment.

This is the data source for Phase 1 of the OTLab. Runs on `softplc-2`
(10.20.30.49) on TCP port 5020 — chosen so it doesn't collide with OpenPLC
which owns port 502 on the same host. softplc-1's OpenPLC polls this slave
via its Slave Devices configuration, mapping the registers below into
softplc-1's own variable space.

Implementation note — why pure asyncio + struct instead of pymodbus:

We tried pymodbus 3.13 first. It works for the *client* side, but its server
side is mid-migration to a new SimData/SimDevice API and the old
`ModbusDeviceContext` codepath returns `DEVICE_BUSY` for both reads and
writes, breaking it as a usable server. Pinning to pymodbus 3.6 would also
work but ties us to a stale version. So instead this file implements just
the slice of Modbus TCP we need: the MBAP header, function codes 1 (Read
Coils), 2 (Read Discrete Inputs), 3 (Read Holding Registers), 4 (Read Input
Registers). About 60 lines of protocol code, no external deps beyond stdlib.
That's also better pedagogically — students of an ICS lab benefit from
seeing the actual protocol bytes, not abstracted away through a library.

Address map (Modbus, 1-based offsets, scaled integers):

    Holding registers (FC 3 / FC 4 input registers identical in this sim):
      40001  TANK_LEVEL_PCT     0.1 %   sine wave 25.0 → 75.0 %, period 60 s
      40002  WATER_TEMP_F       0.1 °F  cosine wave 65.0 → 75.0 °F, period 120 s
      40003  DISCHARGE_PRESS    0.1 PSI sawtooth 50.0 → 80.0 PSI, period 300 s
      40004  HEARTBEAT          int     seconds since process start

    Coils (FC 1 / FC 2 discrete inputs identical in this sim):
      00001  RUNNING            always 1 once the simulator is up
      00002  HIGH_TEMP_ALARM    1 if WATER_TEMP_F > 73.0, else 0

All other addresses return Illegal Data Address (exception code 0x02).

Run as a foreground process for testing, e.g. inside tmux:

    source ~/lab/.venv-modern/bin/activate   # only needed for paho/requests
    python3 ~/lab/sensor-sim.py              # has zero non-stdlib imports

Or as a systemd service — see plc/sensor-sim.service for the unit file.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import struct
import sys
import time

DEFAULT_BIND = "0.0.0.0"
DEFAULT_PORT = 5020
LOG_INTERVAL_S = 10.0

# Modbus exception codes
EXC_ILLEGAL_FUNCTION = 0x01
EXC_ILLEGAL_DATA_ADDRESS = 0x02
EXC_ILLEGAL_DATA_VALUE = 0x03

# Function codes we serve
FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04

# Address ranges (0-based on the wire). 8 of each is plenty for the sim.
NUM_COILS = 8
NUM_REGISTERS = 8

log = logging.getLogger("sensor-sim")


# ── simulated process model ──────────────────────────────────────────────────


class Sim:
    """Holds the simulated sensor values, computed on demand from elapsed
    time. No background task — values are fresh every read."""

    def __init__(self):
        self.t0 = time.time()

    def elapsed(self) -> float:
        return time.time() - self.t0

    def registers(self) -> list[int]:
        """16-bit unsigned values for FC 3 and FC 4 (we serve the same data
        on both — input regs and holding regs are conceptually different
        but for this simulator they reflect the same process)."""
        e = self.elapsed()

        tank_level_pct = 50.0 + 25.0 * math.sin(2 * math.pi * e / 60.0)
        water_temp_f   = 70.0 +  5.0 * math.cos(2 * math.pi * e / 120.0)
        pressure_psi   = 50.0 + 30.0 * (e % 300.0) / 300.0
        heartbeat      = int(e) & 0xFFFF

        return [
            int(round(tank_level_pct * 10)),    # 40001 / 30001
            int(round(water_temp_f   * 10)),    # 40002 / 30002
            int(round(pressure_psi   * 10)),    # 40003 / 30003
            heartbeat,                          # 40004 / 30004
            0, 0, 0, 0,                          # 40005-40008 reserved
        ]

    def coils(self) -> list[bool]:
        """Bit values for FC 1 and FC 2."""
        regs = self.registers()
        running = True
        high_temp_alarm = regs[1] > 730   # > 73.0 °F (scaled)
        return [running, high_temp_alarm, False, False, False, False, False, False]


# ── Modbus TCP framing ───────────────────────────────────────────────────────


async def read_exactly(reader: asyncio.StreamReader, n: int) -> bytes:
    """Like reader.readexactly but raises ConnectionError on EOF instead of
    asyncio.IncompleteReadError so callers can use one except clause."""
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        raise ConnectionError(f"client closed mid-frame ({len(e.partial)}/{n})") from e


def make_exception_response(tx_id: int, unit_id: int, fc: int, exc: int) -> bytes:
    """Build a Modbus TCP exception response."""
    pdu = struct.pack(">BB", fc | 0x80, exc)
    return struct.pack(">HHHB", tx_id, 0, len(pdu) + 1, unit_id) + pdu


def make_bits_response(tx_id: int, unit_id: int, fc: int, bits: list[bool]) -> bytes:
    """Pack a list of bools into Modbus FC1/FC2 response bytes (LSB-first per
    Modbus spec). `len(bits)` must match the count requested by the client."""
    byte_count = (len(bits) + 7) // 8
    bytes_out = bytearray(byte_count)
    for i, b in enumerate(bits):
        if b:
            bytes_out[i // 8] |= 1 << (i % 8)
    pdu = struct.pack(">BB", fc, byte_count) + bytes(bytes_out)
    return struct.pack(">HHHB", tx_id, 0, len(pdu) + 1, unit_id) + pdu


def make_regs_response(tx_id: int, unit_id: int, fc: int, regs: list[int]) -> bytes:
    """Pack a list of 16-bit values into Modbus FC3/FC4 response bytes."""
    byte_count = len(regs) * 2
    pdu = struct.pack(">BB", fc, byte_count) + b"".join(
        struct.pack(">H", r & 0xFFFF) for r in regs
    )
    return struct.pack(">HHHB", tx_id, 0, len(pdu) + 1, unit_id) + pdu


async def serve_client(reader, writer, sim: Sim):
    peer = writer.get_extra_info("peername")
    log.debug("connect from %s", peer)
    try:
        while True:
            # MBAP header is always 7 bytes: tx_id(2) proto(2) length(2) unit_id(1)
            header = await read_exactly(reader, 7)
            tx_id, proto_id, length, unit_id = struct.unpack(">HHHB", header)
            # length includes unit_id (1 byte already read) + PDU. So PDU = length - 1.
            pdu = await read_exactly(reader, length - 1)
            if proto_id != 0:
                log.warning("non-Modbus proto_id=%d from %s, closing", proto_id, peer)
                return

            fc = pdu[0]
            resp: bytes

            if fc in (FC_READ_COILS, FC_READ_DISCRETE_INPUTS):
                if len(pdu) < 5:
                    resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_VALUE)
                else:
                    address, count = struct.unpack(">HH", pdu[1:5])
                    if address + count > NUM_COILS or count < 1 or count > 2000:
                        resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_ADDRESS)
                    else:
                        bits = sim.coils()[address : address + count]
                        resp = make_bits_response(tx_id, unit_id, fc, bits)

            elif fc in (FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS):
                if len(pdu) < 5:
                    resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_VALUE)
                else:
                    address, count = struct.unpack(">HH", pdu[1:5])
                    if address + count > NUM_REGISTERS or count < 1 or count > 125:
                        resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_ADDRESS)
                    else:
                        regs = sim.registers()[address : address + count]
                        resp = make_regs_response(tx_id, unit_id, fc, regs)

            else:
                # Function code we don't support
                resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_FUNCTION)

            writer.write(resp)
            await writer.drain()

    except ConnectionError as e:
        log.debug("client %s disconnected: %s", peer, e)
    except Exception:
        log.exception("error handling client %s", peer)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ── log heartbeat task (so we know the process is alive in journal) ──────────


async def heartbeat_logger(sim: Sim) -> None:
    while True:
        await asyncio.sleep(LOG_INTERVAL_S)
        regs = sim.registers()
        coils = sim.coils()
        log.info(
            "tick t=%5.1fs hr=%s coils=%s",
            sim.elapsed(),
            regs[:4],
            [int(b) for b in coils[:2]],
        )


# ── entrypoint ───────────────────────────────────────────────────────────────


async def amain(bind: str, port: int) -> None:
    sim = Sim()
    log.info("sensor-sim listening on %s:%d", bind, port)

    server = await asyncio.start_server(
        lambda r, w: serve_client(r, w, sim),
        host=bind,
        port=port,
    )

    hb = asyncio.create_task(heartbeat_logger(sim))
    async with server:
        try:
            await server.serve_forever()
        finally:
            hb.cancel()


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--bind", default=DEFAULT_BIND)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    try:
        asyncio.run(amain(args.bind, args.port))
    except KeyboardInterrupt:
        log.info("interrupted, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
