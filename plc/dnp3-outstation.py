#!/usr/bin/env python3
"""dnp3-outstation.py — minimal DNP3 outstation for the OTLab.

Listens on TCP/20000 (the DNP3 standard port). Implements just enough of the
link layer + application layer to:

  - respond to Link-Layer RESET / TEST / REQUEST_LINK_STATUS frames
  - respond to a Read-Class-0123 application-level request with the active
    sensor-sim scenario's analog inputs (group 30, variation 5, 32-bit float)
  - Wireshark's DNP3 dissector parses traffic correctly
  - any DNP3 master scanner / Shodan-style fingerprinter flags it as DNP3
  - runs alongside Modbus (port 5020) on l1-plc-01 — same scenario data,
    different wire protocol

Pure stdlib + the asyncio runtime that's already loaded for sensor-sim.
About 250 lines of protocol code.

Default outstation address = 4, master address = 3 (DNP3 convention for
small substation networks). Override via env vars.

Limitations (this is a teaching outstation, not a production one):
  - No Secure Authentication (SAv5/SAv6) — that's the whole point of the
    'DNP3 unauthenticated control' risk demo
  - No event reporting (only static reads)
  - No time sync, no time-tagged events, no datasets
  - Slave-only: can't initiate unsolicited responses

Run:
  ./dnp3-outstation.py                            # default :20000, addr 4
  ./dnp3-outstation.py --port 20000 --outstation-addr 4 --master-addr 3
  ./dnp3-outstation.py --scenario-file path/to/scenario.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import struct
import sys
import time
import urllib.request

DEFAULT_PORT             = 20000
DEFAULT_OUTSTATION_ADDR  = 4
DEFAULT_MASTER_ADDR      = 3
DEFAULT_SCENARIO_FILE    = "/home/otuser/lab/scenarios/water-treatment.json"
SENSOR_SIM_SCENARIO_URL  = "http://127.0.0.1:5021/scenario"   # live scenario from sensor-sim

# DNP3 link-layer function codes (primary master->outstation)
LL_RESET_LINK_STATES   = 0x00
LL_TEST_LINK_STATES    = 0x02
LL_REQUEST_LINK_STATUS = 0x09
LL_UNCONFIRMED_USER    = 0x04

# DNP3 link-layer function codes (secondary outstation->master)
LL_ACK                 = 0x00
LL_NACK                = 0x01
LL_LINK_STATUS         = 0x0B

# DNP3 application function codes
APP_READ                  = 0x01
APP_RESPONSE              = 0x81
APP_UNSOLICITED_RESPONSE  = 0x82

log = logging.getLogger("dnp3-outstation")


# ── DNP3 CRC (polynomial 0x3D65) ─────────────────────────────────────────────

def _make_crc_table():
    poly = 0xA6BC  # reverse of 0x3D65
    tbl = []
    for b in range(256):
        crc = b
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        tbl.append(crc)
    return tbl

_CRC_TABLE = _make_crc_table()

def dnp3_crc(data: bytes) -> int:
    """Compute the DNP3 16-bit CRC over `data`."""
    crc = 0
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
    return (~crc) & 0xFFFF


def crc_bytes(data: bytes) -> bytes:
    return struct.pack("<H", dnp3_crc(data))


# ── DNP3 framing ─────────────────────────────────────────────────────────────

START_BYTES = b"\x05\x64"


def build_link_frame(ctrl: int, dest: int, src: int, user_data: bytes = b"") -> bytes:
    """Build a complete DNP3 link-layer frame (header + optional data segments).

    `ctrl` is the link control byte (DIR, PRM, FCB, FCV, FUNC).
    `dest` and `src` are 16-bit LE addresses.
    `user_data` is the higher-layer payload; we segment it into 16-byte chunks
    each followed by a 2-byte CRC (DNP3 link-layer convention).
    """
    length = 5 + len(user_data)   # length covers control + dest + src + user data
    header = bytes([length, ctrl]) + struct.pack("<HH", dest, src)
    frame  = START_BYTES + header + crc_bytes(header)

    # Segment user data into 16-byte chunks, CRC each one
    i = 0
    while i < len(user_data):
        chunk = user_data[i:i+16]
        frame += chunk + crc_bytes(chunk)
        i += 16
    return frame


def parse_link_frame(buf: bytes):
    """Return (ctrl, dest, src, user_data, frame_len) or None on incomplete/invalid."""
    if len(buf) < 10 or buf[0:2] != START_BYTES:
        return None
    length = buf[2]
    if length < 5:
        return None
    if dnp3_crc(buf[2:8]) != struct.unpack("<H", buf[8:10])[0]:
        return None
    ctrl = buf[3]
    dest = struct.unpack("<H", buf[4:6])[0]
    src  = struct.unpack("<H", buf[6:8])[0]
    user_data_len = length - 5
    expected_total = 10 + user_data_len + 2 * ((user_data_len + 15) // 16)
    if len(buf) < expected_total:
        return None
    user_data = b""
    pos = 10
    remaining = user_data_len
    while remaining > 0:
        chunk_len = min(16, remaining)
        chunk = buf[pos:pos+chunk_len]
        pos += chunk_len
        # crc
        if pos + 2 > len(buf):
            return None
        # We don't strictly verify chunk CRCs in this teaching outstation;
        # production code should. Skipping makes the parser robust against
        # student-crafted frames that omit CRCs.
        pos += 2
        user_data += chunk
        remaining -= chunk_len
    return ctrl, dest, src, user_data, expected_total


# ── Application layer ───────────────────────────────────────────────────────

def build_app_response(scenario: dict, app_seq: int = 0) -> bytes:
    """Build an APP_RESPONSE payload carrying the scenario's analog inputs.

    Group 30 var 5 = analog input, single-precision float. We pack one
    object per scenario register (skipping the heartbeat counter) with
    qualifier 0x01 (range, 8-bit start/stop, all online). IIN bytes set
    to 0x00 0x00 (no warnings).
    """
    # Application Control byte: FIN=1, FIR=1, CON=0, UNS=0, seq=app_seq
    app_ctrl = 0xC0 | (app_seq & 0x0F)
    # Application function: response
    func     = APP_RESPONSE
    iin      = b"\x00\x00"

    body = bytes([app_ctrl, func]) + iin
    regs = scenario.get("registers", []) if scenario else []
    analog_regs = [r for r in regs if r.get("waveform", {}).get("type") != "counter"]

    if analog_regs:
        # Object header: group 30, var 5, qualifier 0x01 (range 8-bit start/stop)
        start = 0
        stop  = len(analog_regs) - 1
        body += bytes([30, 5, 0x01, start, stop])
        # 5 bytes per object: 1 status flag + 4 IEEE-754 single-precision
        e = time.time() % 600.0  # bound elapsed for waveform sampling
        for r in analog_regs:
            value = _eval_waveform(r.get("waveform", {}), e)
            body += b"\x01" + struct.pack("<f", value)
    return body


def _eval_waveform(w: dict, e: float) -> float:
    t = w.get("type", "constant")
    if t == "sine":
        return w.get("offset", 0.0) + w.get("amp", 0.0) * math.sin(2*math.pi*e/max(0.001, w.get("period", 60.0)))
    if t == "cosine":
        return w.get("offset", 0.0) + w.get("amp", 0.0) * math.cos(2*math.pi*e/max(0.001, w.get("period", 60.0)))
    if t == "sawtooth":
        p = max(0.001, w.get("period", 60.0))
        return w.get("offset", 0.0) + w.get("amp", 0.0) * (e % p) / p
    return float(w.get("value", 0.0))


# ── Connection handler ──────────────────────────────────────────────────────

class Outstation:
    def __init__(self, scenario: dict, addr: int, master_addr: int):
        self.scenario    = scenario
        self.addr        = addr
        self.master_addr = master_addr
        self.app_seq     = 0
        self.connections = 0
        self.requests    = 0

    def handle_link_frame(self, ctrl: int, dest: int, src: int, user_data: bytes) -> bytes:
        func = ctrl & 0x0F
        # Verify destination is us
        if dest != self.addr:
            log.debug("frame for addr %d (we are %d), ignoring", dest, self.addr)
            return b""

        if func == LL_RESET_LINK_STATES:
            log.info("RESET_LINK from master %d", src)
            return build_link_frame(0x00, src, self.addr, b"")  # ACK
        if func == LL_TEST_LINK_STATES:
            log.info("TEST_LINK from master %d", src)
            return build_link_frame(0x00, src, self.addr, b"")
        if func == LL_REQUEST_LINK_STATUS:
            log.info("REQUEST_LINK_STATUS from master %d", src)
            return build_link_frame(LL_LINK_STATUS, src, self.addr, b"")
        if func == LL_UNCONFIRMED_USER and user_data:
            self.requests += 1
            return self.handle_app_request(user_data, src)
        log.debug("unhandled link func 0x%02x from %d", func, src)
        return b""

    def handle_app_request(self, payload: bytes, master_src: int) -> bytes:
        """Build a link-layer response carrying an application-layer reply.

        Strict parsing of the request would extract object groups + variations
        from the master's read. For teaching purposes we treat any read
        request as a Read-Class-0123 and respond with the scenario's analog
        inputs.
        """
        if len(payload) < 4:
            return b""
        app_ctrl = payload[0]
        # Pull the master's app sequence so our response matches
        seq = (app_ctrl + 1) & 0x0F if app_ctrl & 0x40 else 0
        # Build app response
        app_body = build_app_response(self.scenario, seq)
        # Add 1-byte transport header (FIN+FIR=both, seq=0)
        transport = bytes([0xC0])
        user_data = transport + app_body
        # Link layer: PRM=0 (response), DIR=1 (outstation->master), FUNC=00 (CONFIRMED_USER_DATA)
        link_ctrl = 0x80 | 0x04  # DIR=1, FUNC=4 (UNCONFIRMED_USER for simplicity)
        return build_link_frame(link_ctrl, master_src, self.addr, user_data)


async def serve_client(reader, writer, station: Outstation):
    peer = writer.get_extra_info("peername")
    station.connections += 1
    log.info("connection #%d from %s", station.connections, peer)
    buf = b""
    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break
            buf += data
            # Process as many complete frames as we can
            while True:
                # Find the next start sequence
                i = buf.find(START_BYTES)
                if i == -1:
                    buf = b""; break
                if i > 0:
                    buf = buf[i:]
                parsed = parse_link_frame(buf)
                if parsed is None:
                    break  # incomplete
                ctrl, dest, src, user_data, total = parsed
                response = station.handle_link_frame(ctrl, dest, src, user_data)
                if response:
                    writer.write(response)
                    await writer.drain()
                buf = buf[total:]
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    except Exception:
        log.exception("handler error for %s", peer)
    finally:
        try: writer.close(); await writer.wait_closed()
        except Exception: pass


# ── scenario loading (mirrors sensor-sim approach) ──────────────────────────

def load_scenario(path: str | None, fallback_url: str | None) -> dict:
    if path:
        try:
            with open(path) as f:
                s = json.load(f)
            log.info("loaded scenario %r from %s", s.get("id", "?"), path)
            return s
        except Exception as e:
            log.warning("scenario file %s unusable (%s) — trying live fetch", path, e)
    if fallback_url:
        try:
            with urllib.request.urlopen(fallback_url, timeout=2) as r:
                s = json.load(r)
            log.info("loaded scenario %r from %s", s.get("id", "?"), fallback_url)
            return s
        except Exception as e:
            log.warning("live scenario fetch failed: %s", e)
    log.warning("falling back to empty scenario — DNP3 will return zero analogs")
    return {"id": "empty", "registers": []}


# ── entry ────────────────────────────────────────────────────────────────────

async def amain(host, port, addr, master, scenario_path):
    scenario = load_scenario(scenario_path, SENSOR_SIM_SCENARIO_URL)
    station  = Outstation(scenario, addr, master)
    server = await asyncio.start_server(
        lambda r, w: serve_client(r, w, station), host=host, port=port
    )
    log.info("DNP3 outstation listening on %s:%d (addr=%d, master=%d, scenario=%r)",
             host, port, addr, master, scenario.get("id", "?"))
    async with server:
        await server.serve_forever()


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--outstation-addr", type=int,
                   default=int(os.environ.get("DNP3_OUTSTATION_ADDR", DEFAULT_OUTSTATION_ADDR)))
    p.add_argument("--master-addr", type=int,
                   default=int(os.environ.get("DNP3_MASTER_ADDR", DEFAULT_MASTER_ADDR)))
    p.add_argument("--scenario-file",
                   default=os.environ.get("DNP3_SCENARIO", DEFAULT_SCENARIO_FILE))
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    try:
        asyncio.run(amain(args.bind, args.port, args.outstation_addr, args.master_addr, args.scenario_file))
    except KeyboardInterrupt:
        log.info("interrupted, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
