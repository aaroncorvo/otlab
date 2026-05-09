#!/usr/bin/env python3
"""test-modbus-write.py — single Modbus FC5/FC6 write to a chosen target.
Wraps the dashboard's Write Playground from the CLI for scriptable demos.

Examples:
  ./test-modbus-write.py --target sensor-sim --kind reg  --addr 1 --value 850
  ./test-modbus-write.py --target sensor-sim --kind coil --addr 1 --value 1
  ./test-modbus-write.py --target softplc-1  --kind reg  --addr 0 --value 9999
"""
import argparse, sys
from pymodbus.client import ModbusTcpClient

TARGETS = {
    "sensor-sim": ("10.20.30.49", 5020, 0),  # device_id 0 for sensor-sim's pure-stdlib
    "softplc-1":  ("10.20.30.47", 502, 0),
}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True, choices=list(TARGETS))
    ap.add_argument("--kind",   required=True, choices=["coil", "reg"])
    ap.add_argument("--addr",   required=True, type=int)
    ap.add_argument("--value",  required=True, type=int)
    args = ap.parse_args()

    host, port, devid = TARGETS[args.target]
    c = ModbusTcpClient(host, port=port, timeout=2)
    if not c.connect():
        sys.exit(f"connect failed to {host}:{port}")
    try:
        if args.kind == "coil":
            r = c.write_coil(address=args.addr, value=bool(args.value), device_id=devid)
        else:
            r = c.write_register(address=args.addr, value=args.value & 0xFFFF, device_id=devid)
        if r.isError():
            print(f"WRITE FAILED: {r}"); sys.exit(2)
        print(f"OK: {args.kind}[{args.addr}]={args.value} → {host}:{port}")
    finally:
        c.close()

if __name__ == "__main__":
    main()
