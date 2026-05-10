#!/usr/bin/env python3
"""
sensor-sim.py — minimal Modbus TCP slave that pretends to be a small remote
sensor package on the lab segment.

This is the data source for Phase 1 of the OTLab. Runs on `l1-plc-01`
(10.20.30.47) on TCP port 5020 — chosen so it doesn't collide with OpenPLC
which owns port 502 on the same host. l1-plc-01's OpenPLC master polls this
slave (currently over loopback; future `l1-plc-02` backfill will move
sensor-sim onto a separate Pi and put the polling traffic back on the wire).
The master maps the registers below into l1-plc-01's own variable space via
its Slave Devices configuration.

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
import json
import logging
import math
import os
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_BIND = "0.0.0.0"
DEFAULT_PORT = 5020
DEFAULT_CONTROL_PORT = 5021
LOG_INTERVAL_S = 10.0

# Default scenario file. Override with --scenario-file or SENSOR_SIM_SCENARIO env.
# Respects $SCENARIOS_DIR + $SCENARIO env (set by the V1 container Dockerfile)
# so the same code works on the bare host (/home/otuser/lab/scenarios/) and
# inside the V1 container (/opt/otlab/scenarios/).
_SCEN_DIR = os.environ.get("SCENARIOS_DIR", "/home/otuser/lab/scenarios")
_SCEN_NAME = os.environ.get("SCENARIO", "water-treatment")
DEFAULT_SCENARIO_FILE = f"{_SCEN_DIR}/{_SCEN_NAME}.json"

# Built-in fallback scenario (matches the original hard-coded waveforms) used
# when no scenario file is reachable. Keeps the lab usable for first-boot
# before scripts have copied the scenarios/ tree.
BUILTIN_SCENARIO = {
    "id":          "water-treatment",
    "name":        "Maple Ridge Water Treatment (built-in fallback)",
    "vertical":    "Water & Wastewater",
    "description": "Built-in fallback scenario.",
    "registers": [
        {"addr": 0, "name": "TANK_LEVEL_PCT",  "scale": 10, "waveform": {"type": "sine",     "period": 60,  "amp": 25, "offset": 50}},
        {"addr": 1, "name": "WATER_TEMP_F",    "scale": 10, "waveform": {"type": "cosine",   "period": 120, "amp": 5,  "offset": 70}},
        {"addr": 2, "name": "DISCHARGE_PRESS", "scale": 10, "waveform": {"type": "sawtooth", "period": 300, "amp": 30, "offset": 50}},
        {"addr": 3, "name": "HEARTBEAT",       "scale": 1,  "waveform": {"type": "counter"}}
    ],
    "coils": [
        {"addr": 0, "name": "RUNNING",       "always": True},
        {"addr": 1, "name": "HI_TEMP_ALARM", "threshold": {"register": 1, "op": ">", "value_eng": 73.0}}
    ]
}

# Modbus exception codes
EXC_ILLEGAL_FUNCTION = 0x01
EXC_ILLEGAL_DATA_ADDRESS = 0x02
EXC_ILLEGAL_DATA_VALUE = 0x03

# Function codes we serve
FC_READ_COILS              = 0x01
FC_READ_DISCRETE_INPUTS    = 0x02
FC_READ_HOLDING_REGISTERS  = 0x03
FC_READ_INPUT_REGISTERS    = 0x04
FC_WRITE_SINGLE_COIL       = 0x05
FC_WRITE_SINGLE_REGISTER   = 0x06
FC_WRITE_MULTIPLE_COILS    = 0x0F
FC_WRITE_MULTIPLE_REGISTERS = 0x10

# Address ranges (0-based on the wire). 8 of each is plenty for the sim.
NUM_COILS = 8
NUM_REGISTERS = 8

log = logging.getLogger("sensor-sim")


# ── simulated process model ──────────────────────────────────────────────────


def load_scenario(path: str | None) -> dict:
    """Load a scenario JSON file, falling back to the built-in if missing."""
    if path:
        try:
            with open(path) as f:
                s = json.load(f)
            log.info("loaded scenario %r from %s", s.get("id", "?"), path)
            return s
        except FileNotFoundError:
            log.warning("scenario file not found: %s — using built-in fallback", path)
        except Exception as e:
            log.warning("scenario load error %s: %s — using built-in fallback", type(e).__name__, e)
    return dict(BUILTIN_SCENARIO)


class Sim:
    """Holds the simulated sensor values, computed on demand from elapsed
    time. Waveforms + thresholds + labels are loaded from a scenario JSON
    file so the same lab can run multiple OT verticals (water, power,
    pipeline, etc.) without changing code.

    Fault-injection state lets a controlling host (the dashboard) freeze
    the waveforms, freeze just the heartbeat (so l1-plc-01's link-liveness
    detector trips), or override the alarm coil. See the /control HTTP
    endpoint."""

    def __init__(self, scenario: dict | None = None):
        self.t0 = time.time()
        self.scenario = scenario or dict(BUILTIN_SCENARIO)
        # Pad register / coil definitions out to NUM_REGISTERS / NUM_COILS so
        # we can serve the full address range (unused slots return zero).
        self._reg_defs:  list[dict | None] = [None] * NUM_REGISTERS
        self._coil_defs: list[dict | None] = [None] * NUM_COILS
        for r in self.scenario.get("registers", []):
            if 0 <= r.get("addr", -1) < NUM_REGISTERS:
                self._reg_defs[r["addr"]] = r
        for c in self.scenario.get("coils", []):
            if 0 <= c.get("addr", -1) < NUM_COILS:
                self._coil_defs[c["addr"]] = c
        # Fault-injection flags. Mutable from the control HTTP thread.
        self.paused: bool = False
        self.hb_paused: bool = False
        self.force_alarm: bool = False
        # Snapshots taken at the moment a freeze engages. Reading thread
        # uses these instead of fresh waveform values.
        self._snap_regs: list[int] | None = None
        self._snap_coils: list[bool] | None = None
        self._snap_hb: int | None = None
        # Modbus write overrides — persistent until cleared. When a client
        # writes a coil or register via FC5/6/15/16, the value is stored
        # here and served on subsequent reads instead of the computed
        # waveform. Demonstrates the "Modbus has no auth" teaching lesson.
        self.reg_overrides: dict[int, int] = {}     # addr (0-based) -> 16-bit value
        self.coil_overrides: dict[int, bool] = {}   # addr (0-based) -> bool

    def elapsed(self) -> float:
        return time.time() - self.t0

    @staticmethod
    def _eval_waveform(waveform: dict, e: float) -> float:
        """Compute the engineering-units value from a waveform spec.
        Returns 0.0 for unknown waveform types or counter type (counter
        is special-cased by callers because it's an integer)."""
        wtype = waveform.get("type", "constant")
        if wtype == "sine":
            return waveform.get("offset", 0.0) + waveform.get("amp", 0.0) * \
                   math.sin(2 * math.pi * e / max(0.001, waveform.get("period", 60.0)))
        if wtype == "cosine":
            return waveform.get("offset", 0.0) + waveform.get("amp", 0.0) * \
                   math.cos(2 * math.pi * e / max(0.001, waveform.get("period", 60.0)))
        if wtype == "sawtooth":
            p = max(0.001, waveform.get("period", 60.0))
            return waveform.get("offset", 0.0) + waveform.get("amp", 0.0) * (e % p) / p
        if wtype == "constant":
            return float(waveform.get("value", 0.0))
        return 0.0

    def _compute_regs(self) -> list[int]:
        """Compute holding-register values from the active scenario's
        register definitions. Each register has its own waveform + scale
        factor so analog values can be carried as scaled integers per
        Modbus convention."""
        e = self.elapsed()
        regs = [0] * NUM_REGISTERS
        for addr, r in enumerate(self._reg_defs):
            if r is None:
                continue
            w = r.get("waveform", {})
            scale = r.get("scale", 1)
            if w.get("type") == "counter":
                regs[addr] = int(e) & 0xFFFF
            else:
                regs[addr] = int(round(self._eval_waveform(w, e) * scale)) & 0xFFFF
        return regs

    def _compute_coils(self) -> list[bool]:
        """Compute coil state from the active scenario's coil definitions.
        Each coil is either always-true (running indicators) or driven by
        a threshold against a register's engineering value."""
        regs = self._compute_regs()
        coils = [False] * NUM_COILS
        for addr, c in enumerate(self._coil_defs):
            if c is None:
                continue
            if c.get("always", False):
                coils[addr] = True
                continue
            t = c.get("threshold")
            if t and 0 <= t.get("register", -1) < NUM_REGISTERS:
                ref_def  = self._reg_defs[t["register"]] or {}
                ref_scale = ref_def.get("scale", 1)
                eng_value = regs[t["register"]] / ref_scale if ref_scale else regs[t["register"]]
                op = t.get("op", ">")
                v  = t.get("value_eng", 0)
                if   op == ">":  coils[addr] = eng_value >  v
                elif op == ">=": coils[addr] = eng_value >= v
                elif op == "<":  coils[addr] = eng_value <  v
                elif op == "<=": coils[addr] = eng_value <= v
                elif op == "==": coils[addr] = eng_value == v
        return coils

    def registers(self) -> list[int]:
        """16-bit unsigned values for FC 3 / FC 4. Honors fault-injection
        AND any persistent Modbus-write overrides (per-address)."""
        if self.paused and self._snap_regs is not None:
            regs = list(self._snap_regs)
        else:
            regs = self._compute_regs()
        if self.hb_paused and self._snap_hb is not None:
            regs[3] = self._snap_hb
        for addr, val in self.reg_overrides.items():
            if 0 <= addr < len(regs):
                regs[addr] = val & 0xFFFF
        return regs

    def coils(self) -> list[bool]:
        """Bit values for FC 1 / FC 2. Honors fault-injection AND
        per-coil Modbus-write overrides."""
        if self.paused and self._snap_coils is not None:
            coils = list(self._snap_coils)
        else:
            coils = self._compute_coils()
        if self.force_alarm:
            coils[1] = True
        for addr, val in self.coil_overrides.items():
            if 0 <= addr < len(coils):
                coils[addr] = bool(val)
        return coils

    # ── Modbus write hooks (FC5 / FC6 / FC15 / FC16) ─────────────────────────
    def write_coil(self, addr: int, on: bool) -> None:
        if 0 <= addr < NUM_COILS:
            self.coil_overrides[addr] = on
            log.info("FC5 write coil[%d] = %s", addr, on)

    def write_register(self, addr: int, val: int) -> None:
        if 0 <= addr < NUM_REGISTERS:
            self.reg_overrides[addr] = val & 0xFFFF
            log.info("FC6 write reg[%d] = %d", addr, val)

    def clear_writes(self) -> dict:
        n_regs = len(self.reg_overrides)
        n_coils = len(self.coil_overrides)
        self.reg_overrides.clear()
        self.coil_overrides.clear()
        log.info("writes cleared: %d regs, %d coils", n_regs, n_coils)
        return {'cleared_regs': n_regs, 'cleared_coils': n_coils}

    def writes_state(self) -> dict:
        return {
            'reg_overrides':  dict(self.reg_overrides),
            'coil_overrides': {a: bool(v) for a, v in self.coil_overrides.items()},
            'any_active':     bool(self.reg_overrides or self.coil_overrides),
        }

    # ── fault control (called from HTTP thread) ─────────────────────────────
    def faults_state(self) -> dict:
        return {
            "paused":      self.paused,
            "hb_paused":   self.hb_paused,
            "force_alarm": self.force_alarm,
            "any_active":  self.paused or self.hb_paused or self.force_alarm,
        }

    def scenario_summary(self) -> dict:
        """Full scenario data + a few synthesized fields. Returned by
        GET /scenario for the dashboard to render the scenario header,
        regulatory tags, risks, and walkthroughs."""
        return self.scenario

    def update_faults(self, new: dict) -> dict:
        # Snapshot at engagement edge so freezes capture the live values
        # at the moment they're activated.
        if "paused" in new:
            np = bool(new["paused"])
            if np and not self.paused:
                self._snap_regs  = self._compute_regs()
                self._snap_coils = self._compute_coils()
            self.paused = np
        if "hb_paused" in new:
            np = bool(new["hb_paused"])
            if np and not self.hb_paused:
                self._snap_hb = self._compute_regs()[3]
            self.hb_paused = np
        if "force_alarm" in new:
            self.force_alarm = bool(new["force_alarm"])
        log.info("faults updated -> %s", self.faults_state())
        return self.faults_state()


# ── Fault-injection control server (stdlib http.server in a thread) ──────────


class _ControlHandler(BaseHTTPRequestHandler):
    """Handles GET/POST /control on a tiny side-channel HTTP server. The
    dashboard drives fault injection through here. No auth — lab is
    intentionally open and this server only listens on the lab segment."""

    sim: Sim | None = None  # set by start_control_server before binding

    def log_message(self, fmt, *args):
        # Suppress default per-request stderr spam — we already log from
        # update_faults on every state change.
        pass

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/control":
            return self._send_json(200, self.sim.faults_state())
        if self.path == "/writes":
            return self._send_json(200, self.sim.writes_state())
        if self.path == "/scenario":
            return self._send_json(200, self.sim.scenario_summary())
        return self._send_json(404, {"err": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        try:
            data = json.loads(self.rfile.read(n).decode()) if n else {}
        except Exception:
            return self._send_json(400, {"err": "bad json"})

        if self.path == "/control":
            return self._send_json(200, self.sim.update_faults(data))
        if self.path == "/control/reset":
            return self._send_json(200, self.sim.update_faults({
                "paused": False, "hb_paused": False, "force_alarm": False,
            }))
        if self.path == "/writes/reset":
            return self._send_json(200, self.sim.clear_writes())
        return self._send_json(404, {"err": "not found"})


def start_control_server(sim: Sim, bind: str, port: int) -> None:
    _ControlHandler.sim = sim
    httpd = ThreadingHTTPServer((bind, port), _ControlHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="sensor-sim-control").start()
    log.info("control HTTP server on %s:%d", bind, port)


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


def make_write_echo(tx_id: int, unit_id: int, fc: int, address: int, value_or_count: int) -> bytes:
    """FC5/6/15/16 success responses echo the address + value-or-count."""
    pdu = struct.pack(">BHH", fc, address, value_or_count & 0xFFFF)
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

            elif fc == FC_WRITE_SINGLE_COIL:
                if len(pdu) < 5:
                    resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_VALUE)
                else:
                    address, value = struct.unpack(">HH", pdu[1:5])
                    if address >= NUM_COILS or value not in (0x0000, 0xFF00):
                        resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_ADDRESS)
                    else:
                        sim.write_coil(address, value == 0xFF00)
                        resp = make_write_echo(tx_id, unit_id, fc, address, value)

            elif fc == FC_WRITE_SINGLE_REGISTER:
                if len(pdu) < 5:
                    resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_VALUE)
                else:
                    address, value = struct.unpack(">HH", pdu[1:5])
                    if address >= NUM_REGISTERS:
                        resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_ADDRESS)
                    else:
                        sim.write_register(address, value)
                        resp = make_write_echo(tx_id, unit_id, fc, address, value)

            elif fc == FC_WRITE_MULTIPLE_COILS:
                if len(pdu) < 6:
                    resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_VALUE)
                else:
                    address, count, byte_count = struct.unpack(">HHB", pdu[1:6])
                    expected_bytes = (count + 7) // 8
                    if (address + count > NUM_COILS or count < 1 or count > 0x7B0
                            or byte_count != expected_bytes
                            or len(pdu) < 6 + byte_count):
                        resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_ADDRESS)
                    else:
                        data = pdu[6:6 + byte_count]
                        for i in range(count):
                            bit = (data[i // 8] >> (i % 8)) & 1
                            sim.write_coil(address + i, bool(bit))
                        resp = make_write_echo(tx_id, unit_id, fc, address, count)

            elif fc == FC_WRITE_MULTIPLE_REGISTERS:
                if len(pdu) < 6:
                    resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_VALUE)
                else:
                    address, count, byte_count = struct.unpack(">HHB", pdu[1:6])
                    if (address + count > NUM_REGISTERS or count < 1 or count > 123
                            or byte_count != count * 2
                            or len(pdu) < 6 + byte_count):
                        resp = make_exception_response(tx_id, unit_id, fc, EXC_ILLEGAL_DATA_ADDRESS)
                    else:
                        for i in range(count):
                            (val,) = struct.unpack(">H", pdu[6 + i*2 : 6 + i*2 + 2])
                            sim.write_register(address + i, val)
                        resp = make_write_echo(tx_id, unit_id, fc, address, count)

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


async def amain(bind: str, port: int, control_port: int, scenario_path: str | None) -> None:
    scenario = load_scenario(scenario_path)
    sim = Sim(scenario=scenario)
    log.info("sensor-sim listening on %s:%d (scenario=%r)", bind, port,
             scenario.get("id", "?"))

    if control_port > 0:
        start_control_server(sim, bind, control_port)

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
    p.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT,
                   help="HTTP control port for fault injection (0 = disabled)")
    p.add_argument("--scenario-file",
                   default=os.environ.get("SENSOR_SIM_SCENARIO", DEFAULT_SCENARIO_FILE),
                   help="Path to scenario JSON. Default: %(default)s. "
                        "Override with SENSOR_SIM_SCENARIO env or this flag.")
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    try:
        asyncio.run(amain(args.bind, args.port, args.control_port, args.scenario_file))
    except KeyboardInterrupt:
        log.info("interrupted, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
