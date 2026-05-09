#!/usr/bin/env python3
"""test-dnp3-scan.py — probe the DNP3 outstation on softplc-2:20000.

What it does:
  1. TCP connect to confirm the port is open
  2. Send a Link-Layer REQUEST_LINK_STATUS frame (DNP3 keepalive)
  3. Send a Read-Class-0 application request and parse the analog values
     out of the response

Real-world parallel: this is the same first-contact pattern an attacker
or a SCADA master would use. Wireshark dissects every frame as DNP3
since the framing is RFC-correct.

Pure stdlib + a copy of the link-layer CRC table.
"""
import socket, struct, sys, argparse

DEFAULT_HOST   = "10.20.30.49"
DEFAULT_PORT   = 20000
MASTER_ADDR    = 3
OUTSTATION_ADDR = 4
START = b"\x05\x64"

# DNP3 CRC table (poly 0x3D65 reversed = 0xA6BC)
def _make_table():
    poly = 0xA6BC; tbl = []
    for b in range(256):
        crc = b
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        tbl.append(crc)
    return tbl
_TBL = _make_table()
def crc(data):
    c = 0
    for b in data:
        c = (c >> 8) ^ _TBL[(c ^ b) & 0xFF]
    return (~c) & 0xFFFF

def crc_b(data): return struct.pack("<H", crc(data))

def build_frame(ctrl, dest, src, user_data=b""):
    length = 5 + len(user_data)
    header = bytes([length, ctrl]) + struct.pack("<HH", dest, src)
    f = START + header + crc_b(header)
    i = 0
    while i < len(user_data):
        ch = user_data[i:i+16]
        f += ch + crc_b(ch)
        i += 16
    return f

def parse_frame(buf):
    if len(buf) < 10 or buf[0:2] != START: return None, b""
    length = buf[2]; ctrl = buf[3]
    if dnp3_crc_check_fail(buf[2:8], buf[8:10]): return None, b""
    user_data_len = length - 5
    expected = 10 + user_data_len + 2 * ((user_data_len + 15) // 16)
    if len(buf) < expected: return None, buf
    user_data = b""; pos = 10; rem = user_data_len
    while rem > 0:
        ch_len = min(16, rem)
        user_data += buf[pos:pos+ch_len]
        pos += ch_len + 2
        rem -= ch_len
    return (ctrl, user_data), buf[expected:]

def dnp3_crc_check_fail(data, crc_bytes):
    return crc(data) != struct.unpack("<H", crc_bytes)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    print(f"=== probing DNP3 @ {args.host}:{args.port} ===")
    try:
        s = socket.create_connection((args.host, args.port), timeout=3)
    except Exception as e:
        sys.exit(f"  TCP connect failed: {e}")
    print("  TCP OPEN ✓")

    # 1. Link-Layer REQUEST_LINK_STATUS (func 0x09, PRM=1, DIR=0)
    print("\n--- send REQUEST_LINK_STATUS ---")
    f = build_frame(0x49, OUTSTATION_ADDR, MASTER_ADDR, b"")
    s.sendall(f)
    print(f"  TX {len(f)}b: {f.hex()}")
    s.settimeout(2)
    try:
        resp = s.recv(256)
        print(f"  RX {len(resp)}b: {resp.hex()}")
        parsed, _ = parse_frame(resp)
        if parsed:
            ctrl, ud = parsed
            print(f"  parsed link ctrl=0x{ctrl:02x} (LINK_STATUS expected)")
    except socket.timeout:
        print("  TIMEOUT (some outstations only respond to USER_DATA)")

    # 2. App-layer Read Class 0 (group 60, var 1, qualifier 0x06 = all-of-class)
    print("\n--- send READ Class 0 ---")
    transport = bytes([0xC0])
    app_ctrl  = 0xC0
    app_func  = 0x01      # READ
    obj_hdr   = bytes([60, 1, 0x06])
    app_payload = bytes([app_ctrl, app_func]) + obj_hdr
    user_data   = transport + app_payload
    # link ctrl: PRM=1, DIR=0, FUNC=4 (UNCONFIRMED USER DATA)
    f = build_frame(0x44, OUTSTATION_ADDR, MASTER_ADDR, user_data)
    s.sendall(f)
    print(f"  TX {len(f)}b: {f.hex()}")
    try:
        resp = s.recv(1024)
        print(f"  RX {len(resp)}b: {resp.hex()}")
        parsed, _ = parse_frame(resp)
        if parsed:
            _ctrl, ud = parsed
            if len(ud) > 5 and ud[1] == 0x81:
                print(f"  parsed APP_RESPONSE iin={ud[2]:02x}{ud[3]:02x}")
                # group 30 var 5 = 32-bit FP analog
                if len(ud) > 9 and ud[4] == 30 and ud[5] == 5:
                    start, stop = ud[7], ud[8]
                    n = stop - start + 1
                    pos = 9
                    print(f"  group 30 var 5: {n} analog inputs")
                    for i in range(n):
                        if pos + 5 > len(ud): break
                        flag = ud[pos]
                        val  = struct.unpack("<f", ud[pos+1:pos+5])[0]
                        print(f"    [{start+i}] flag=0x{flag:02x}  value={val:.2f}")
                        pos += 5
    except socket.timeout:
        print("  TIMEOUT")

    s.close()
    print("\nDetection lesson:")
    print("  Wireshark dissects all of the above as DNP3 (port 20000 default).")
    print("  Any DNP3 from a non-master IP = high-confidence-malicious.")
    print("  In real utility networks, deploy DNP3 Secure Authentication (SAv5 or SAv6).")

if __name__ == "__main__":
    main()
