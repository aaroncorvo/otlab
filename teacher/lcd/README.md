# OTLab SerLCD status display

Drives the **SparkFun Qwiic 16x2 SerLCD** plugged into the teacher Pi's
Cruiser carrier board. The display rotates two screens every 4 seconds:

```
┌────────────────┐     ┌────────────────┐
│otlab-teacher   │     │Tailscale:      │
│10.20.30.27     │  ⟷  │100.77.2.22     │
└────────────────┘     └────────────────┘
```

## Install

```bash
./teacher/lcd/install-lcd.sh otadmin@10.20.30.27
```

That enables I2C, installs `smbus2`, drops `otlab-lcd.py` into
`/opt/otlab/`, and starts `otlab-lcd.service`.

## Hardware notes (learned the hard way)

The Qwiic devices are **NOT on the ESP32** — they plug into the **Cruiser
carrier board's Qwiic connectors (J4/J6)**, which are wired to the
**CM5's I2C bus**, not the ESP32.

I2C bus discovery on this Cruiser + CM5 (Trixie):

| Step | What | Why |
|------|------|-----|
| `dtparam=i2c_arm=on` in `/boot/firmware/config.txt` | enables the ARM I2C controller | off by default on this image |
| `modprobe i2c-dev` + `/etc/modules-load.d/i2c-dev.conf` | creates `/dev/i2c-*` nodes | the controller exists in `/sys/bus/i2c/devices` but no char device without this module |
| **reboot once** | applies the dtparam | device-tree params only take effect at boot |

The Qwiic chain lands on **`/dev/i2c-2`** (not the conventional i2c-1).
Confirmed devices on bus 2:

| Addr | Device |
|------|--------|
| 0x18 | SparkFun Qwiic Single Relay |
| 0x48 | TMP117 high-precision temp |
| 0x5d | SparkFun Qwiic Motor Driver |
| 0x72 | SparkFun 16x2 SerLCD |

`/dev/i2c-13` and `/dev/i2c-14` are the HDMI DDC buses — they ACK on
every address (false positives). Ignore them.

## SerLCD (OpenLCD firmware) I2C protocol

No register addressing — you stream raw bytes:

```
clear        0x7C 0x2D
cursor       0xFE  (0x80 + 0x40*line + col)    line0->0x80, line1->0xC0
RGB light    0x7C 0x2B  R G B                   (0-255 per channel)
text         raw ASCII bytes (auto-advances cursor)
```

## Driving the other Qwiic devices

The relay (0x18), TMP117 (0x48), and motor driver (0x5d) are on the same
bus and can be driven from Linux the same way (smbus2 on `/dev/i2c-2`).
TMP117 is register-based (read 2 bytes from reg 0x00, /128 for °C). The
relay and motor driver use SparkFun's command protocols. Wiring those
into the dashboard / OpenPLC is a future task.

## Manage

```bash
ssh otadmin@10.20.30.27 'journalctl -u otlab-lcd -f'        # live logs
ssh otadmin@10.20.30.27 'sudo systemctl restart otlab-lcd'  # restart
```

Tune bus/address/interface/cadence via the `Environment=` lines in
`otlab-lcd.service`.
