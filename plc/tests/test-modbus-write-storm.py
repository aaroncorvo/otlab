#!/usr/bin/env python3
"""test-modbus-write-storm.py — bombard sensor-sim with FC6 writes and
auto-clear at the end. Demonstrates:

  - Modbus has no rate limiting / no auth
  - What a flood looks like on the wire (compare with Live Wire Feed)
  - Why production PLCs need protocol-aware rate limits / firewalls

Default: 200 writes in ~10 seconds. Cleans up its own overrides at the end.
"""
import argparse, time, sys, random, urllib.request, json
from pymodbus.client import ModbusTcpClient

CONTROL_RESET = "http://10.20.30.47:5021/writes/reset"

def storm(host, port, count, delay):
    c = ModbusTcpClient(host, port=port, timeout=1)
    if not c.connect():
        print("CONNECT FAILED"); return False
    print(f"firing {count} FC6 writes at {host}:{port} (delay {delay*1000:.0f} ms each)…")
    sent, errors = 0, 0
    t0 = time.time()
    try:
        for i in range(count):
            addr = i % 4                     # spread writes across the 4 valid registers
            value = random.randint(0, 65535)
            try:
                r = c.write_register(address=addr, value=value, device_id=0)
                if r.isError(): errors += 1
                else:           sent += 1
            except Exception:
                errors += 1
            if delay: time.sleep(delay)
    finally:
        c.close()
    dt = time.time() - t0
    print(f"  sent={sent}  errors={errors}  in {dt:.1f}s  ({sent/dt:.1f} writes/sec)")
    return errors == 0

def cleanup():
    print("clearing sensor-sim overrides...")
    try:
        req = urllib.request.Request(CONTROL_RESET, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            print(f"  /writes/reset returned: {r.read().decode()[:100]}")
    except Exception as e:
        print(f"  cleanup failed: {e} (run 'curl -X POST http://10.20.30.47:5021/writes/reset' manually)")

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host",  default="10.20.30.47")
    ap.add_argument("--port",  type=int, default=5020)
    ap.add_argument("--count", type=int, default=200, help="total FC6 writes")
    ap.add_argument("--delay", type=float, default=0.05, help="seconds between writes")
    ap.add_argument("--no-cleanup", action="store_true", help="leave overrides in place")
    args = ap.parse_args()
    ok = storm(args.host, args.port, args.count, args.delay)
    if not args.no_cleanup:
        cleanup()
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
