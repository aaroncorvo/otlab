#!/usr/bin/env python3
"""modbus-read.py — quick CLI to read/write the otlab-modbus-io bridge.

Read the live register map:
    python3 modbus-read.py 10.20.30.27

Drive the hardware over Modbus (proves the bridge is bidirectional):
    python3 modbus-read.py 10.20.30.27 --relay on
    python3 modbus-read.py 10.20.30.27 --motor-a 40
    python3 modbus-read.py 10.20.30.27 --relay off --motor-a 0

Needs: pip install pymodbus  (or apt install python3-pymodbus)
"""
import argparse
import sys

try:
    from pymodbus.client import ModbusTcpClient
except Exception:
    sys.exit("pymodbus not installed (apt install python3-pymodbus)")


def s16(v):
    return v - 65536 if v >= 32768 else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--relay", choices=["on", "off"])
    ap.add_argument("--motor-a", type=int, metavar="PCT")
    ap.add_argument("--motor-b", type=int, metavar="PCT")
    a = ap.parse_args()

    c = ModbusTcpClient(a.host, port=a.port)
    if not c.connect():
        sys.exit(f"could not connect to {a.host}:{a.port}")

    if a.relay is not None:
        c.write_register(10, 1 if a.relay == "on" else 0)
        print(f"wrote relay {a.relay} (HR10)")
    if a.motor_a is not None:
        c.write_register(11, a.motor_a & 0xFFFF)
        print(f"wrote motor A {a.motor_a}% (HR11)")
    if a.motor_b is not None:
        c.write_register(12, a.motor_b & 0xFFFF)
        print(f"wrote motor B {a.motor_b}% (HR12)")

    rr = c.read_holding_registers(0, count=13)
    if rr.isError():
        sys.exit(f"read error: {rr}")
    r = rr.registers
    print(f"  temp     {s16(r[0])/10:6.1f} C   {s16(r[1])/10:6.1f} F")
    print(f"  relay    {r[2]}")
    print(f"  motor A  {s16(r[3]):4d} %    motor B  {s16(r[4]):4d} %")
    print(f"  fresh    {r[5]}   poll_age {r[6]} ms")
    print(f"  cmd regs relay={r[10]} motA={s16(r[11])} motB={s16(r[12])}")
    c.close()


if __name__ == "__main__":
    main()
