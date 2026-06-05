#!/usr/bin/env python3
"""otlab-lcd — drive a SparkFun Qwiic 16x2 SerLCD from the teacher Pi.

The SerLCD (OpenLCD firmware, I2C addr 0x72) is plugged into the
Cruiser carrier board's Qwiic connector (J4/J6), which is wired to the
CM5's I2C bus (/dev/i2c-1 by default once dtparam=i2c_arm=on is set).

Rotates two screens every SCREEN_SECS:

    ┌────────────────┐     ┌────────────────┐
    │otlab-teacher   │     │Tailscale:      │
    │10.20.30.27     │     │100.77.2.22     │
    └────────────────┘     └────────────────┘

OpenLCD I2C protocol (no register addressing — raw byte stream):
    clear      : 0x7C 0x2D
    cursor     : 0xFE (0x80 + 0x40*line + col)   line0->0x80, line1->0xC0
    RGB light  : 0x7C 0x2B R G B                  (0-255 per channel)
    text       : raw ASCII bytes (auto-advance)

Env:
    OTLAB_LCD_BUS    I2C bus number          (default 1)
    OTLAB_LCD_ADDR   I2C address (hex/int)   (default 0x72)
    OTLAB_LCD_IFACE  preferred IP interface  (default eth1)
    OTLAB_LCD_SECS   seconds per screen      (default 4)
"""
import os
import socket
import subprocess
import sys
import time

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:
    print("smbus2 not installed: sudo pip3 install --break-system-packages smbus2",
          file=sys.stderr)
    sys.exit(2)

BUS_NUM   = int(os.environ.get("OTLAB_LCD_BUS", "1"))
ADDR      = int(os.environ.get("OTLAB_LCD_ADDR", "0x72"), 0)
IFACE     = os.environ.get("OTLAB_LCD_IFACE", "eth1")
SCREEN_SECS = float(os.environ.get("OTLAB_LCD_SECS", "4"))
# TMP117 high-precision temp on the same Qwiic bus. Set TMP117_ADDR=0
# to disable the temperature screen.
TMP117_ADDR = int(os.environ.get("OTLAB_TMP117_ADDR", "0x48"), 0)

CLEAR   = [0x7C, 0x2D]
RGB     = lambda r, g, b: [0x7C, 0x2B, r & 0xFF, g & 0xFF, b & 0xFF]
DEGREE  = 0xDF   # HD44780 charset degree symbol


def _send(bus, data):
    bus.i2c_rdwr(i2c_msg.write(ADDR, data))


def lcd_clear(bus):
    _send(bus, CLEAR)
    time.sleep(0.01)


def lcd_backlight(bus, r, g, b):
    _send(bus, RGB(r, g, b))
    time.sleep(0.01)


def _encode(c):
    """Map a char to an HD44780 byte. '°' -> the charset degree symbol;
    printable ASCII passes through; anything else becomes a space."""
    if c == "°":
        return DEGREE
    o = ord(c)
    return o if 32 <= o < 128 else 0x20


def lcd_line(bus, line, text):
    pos = 0x80 + (0x40 * line)
    _send(bus, [0xFE, pos])
    time.sleep(0.004)
    t = text[:16].ljust(16)
    _send(bus, [_encode(c) for c in t])
    time.sleep(0.004)


def read_tmp117(bus):
    """Read the TMP117 temperature register (0x00). Returns °C as a float
    or None if the sensor isn't present / read fails.

    TMP117: temp register is signed 16-bit, 1 LSB = 0.0078125 °C."""
    if not TMP117_ADDR:
        return None
    try:
        d = bus.read_i2c_block_data(TMP117_ADDR, 0x00, 2)
        raw = (d[0] << 8) | d[1]
        if raw >= 32768:
            raw -= 65536
        return raw * 0.0078125
    except Exception:
        return None


def get_ip():
    """Prefer the configured lab interface; fall back to outbound IP."""
    for iface in (IFACE, "eth0", "end0"):
        try:
            out = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", iface],
                capture_output=True, text=True, timeout=3,
            ).stdout
            for tok in out.split():
                if tok.count(".") == 3 and "/" in tok:
                    return tok.split("/")[0]
        except Exception:
            pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "no-ip"


def get_tailscale():
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        return out.splitlines()[0] if out else "offline"
    except Exception:
        return "n/a"


def open_bus():
    return SMBus(BUS_NUM)


def main():
    print(f"otlab-lcd: bus={BUS_NUM} addr=0x{ADDR:02x} iface={IFACE} "
          f"secs={SCREEN_SECS}", flush=True)

    bus = None
    backlight_set = False
    # Screens cycle in order. The temp screen is auto-skipped when the
    # TMP117 isn't present (read returns None), so unplugging the sensor
    # just drops back to a 2-screen rotation.
    screen = 0
    while True:
        try:
            if bus is None:
                bus = open_bus()
                backlight_set = False
            if not backlight_set:
                # Warm amber to match the lab's ember theme.
                lcd_backlight(bus, 255, 120, 20)
                backlight_set = True

            host = socket.gethostname()

            # Build the active screen list dynamically so the temp panel
            # appears only when the TMP117 actually reads.
            temp_c = read_tmp117(bus)

            screens = [
                ("host", lambda: (host, get_ip())),
                ("ts",   lambda: ("Tailscale:", get_tailscale())),
            ]
            if temp_c is not None:
                temp_f = temp_c * 9.0 / 5.0 + 32.0
                screens.append(
                    ("temp", lambda: ("Temperature",
                                      f"{temp_c:.1f}°C  {temp_f:.1f}°F"))
                )

            screen %= len(screens)
            _, render = screens[screen]
            l0, l1 = render()
            lcd_clear(bus)
            lcd_line(bus, 0, l0)
            lcd_line(bus, 1, l1)
            screen += 1
            time.sleep(SCREEN_SECS)
        except OSError as e:
            # LCD unplugged or bus glitch — drop the handle and retry.
            print(f"otlab-lcd: i2c error {e}; reopening in 2s", flush=True)
            try:
                if bus:
                    bus.close()
            except Exception:
                pass
            bus = None
            time.sleep(2)
        except KeyboardInterrupt:
            break

    if bus:
        try:
            lcd_clear(bus)
            bus.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
