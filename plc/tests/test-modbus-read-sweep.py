#!/usr/bin/env python3
"""test-modbus-read-sweep.py — read every defined register + coil across both
Modbus endpoints in the lab. Useful as:

  - Reachability sanity (Phase 1 alive?)
  - Wireshark teaching artifact (FC1/2/3/4 round-trip)
  - Detection baseline (what's the "normal" master poll pattern?)

Run as otuser on softplc-2 (or any host with pymodbus + lab-segment access).
"""
import argparse, sys
from pymodbus.client import ModbusTcpClient

TARGETS = [
    ("sensor-sim @ softplc-2:5020", "10.20.30.49", 5020, 0),
    ("OpenPLC mirror @ softplc-1:502", "10.20.30.47", 502, 0),
]

def sweep(label, host, port, dev_id):
    print(f"\n=== {label} ===")
    c = ModbusTcpClient(host, port=port, timeout=2)
    if not c.connect():
        print(f"  CONNECT FAILED to {host}:{port}")
        return False
    try:
        for fc, name, fn in [
            (3, "FC3 read holding regs",   lambda: c.read_holding_registers(address=0, count=8, device_id=dev_id)),
            (4, "FC4 read input regs",     lambda: c.read_input_registers(address=0, count=8, device_id=dev_id)),
            (1, "FC1 read coils",          lambda: c.read_coils(address=0, count=8, device_id=dev_id)),
            (2, "FC2 read discrete inps",  lambda: c.read_discrete_inputs(address=0, count=8, device_id=dev_id)),
        ]:
            try:
                r = fn()
                if r.isError():
                    print(f"  {name:30s} EXCEPTION  {r}")
                else:
                    if fc in (1, 2):
                        vals = [int(b) for b in r.bits[:8]]
                    else:
                        vals = list(r.registers[:8])
                    print(f"  {name:30s} OK  {vals}")
            except Exception as e:
                print(f"  {name:30s} ERROR      {type(e).__name__}: {e}")
    finally:
        c.close()
    return True

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", choices=["all", "sensor-sim", "softplc-1"], default="all")
    args = ap.parse_args()
    ok = True
    for label, host, port, devid in TARGETS:
        if args.target == "sensor-sim" and "sensor-sim" not in label: continue
        if args.target == "softplc-1"  and "softplc-1"  not in label: continue
        if not sweep(label, host, port, devid):
            ok = False
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
