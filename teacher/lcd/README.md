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

## Temperature screen (TMP117)

When the TMP117 (0x48) responds on the bus, the display adds a third
screen in the rotation:

```
┌────────────────┐
│Temperature     │
│23.5°C  74.3°F  │
└────────────────┘
```

The driver reads the TMP117 temperature register (0x00, signed 16-bit,
1 LSB = 0.0078125 °C) every cycle and only shows the temp screen when
the read succeeds — so an absent or unplugged sensor silently drops
back to the 2-screen rotation, no errors. Disable with
`OTLAB_TMP117_ADDR=0` in the service env.

## Qwiic is a daisy-chain — loose connector = downstream goes dark

The Qwiic devices are wired in series: `Pi -> LCD -> TMP117 -> relay ->
motor`. If a connector between two links comes loose, everything
*downstream* of it stops responding while upstream keeps working.

Symptom seen during bring-up: the first scan found all four devices,
but a later scan showed only 0x72 (LCD) ACKing — the TMP117/relay/motor
had dropped off. Fix: reseat every Qwiic cable, especially the joint
right after the LCD. Re-run the scan to confirm:

```bash
sudo python3 -c "
from smbus2 import SMBus, i2c_msg
b=SMBus(2)
for a in (0x18,0x48,0x5d,0x72):
    try: b.i2c_rdwr(i2c_msg.write(a,[])); print(hex(a),'ACK')
    except OSError: print(hex(a),'no ACK')
"
```

## Driving the other Qwiic devices

The relay (0x18) and motor driver (0x5d) are on the same bus and can be
driven from Linux the same way (smbus2 on `/dev/i2c-2`) once they're
reconnected. They use SparkFun's command protocols. Wiring those into
the dashboard / OpenPLC as PLC-commandable outputs is a future task.

## Manage

```bash
ssh otadmin@10.20.30.27 'journalctl -u otlab-lcd -f'        # live logs
ssh otadmin@10.20.30.27 'sudo systemctl restart otlab-lcd'  # restart
```

Tune bus/address/interface/cadence via the `Environment=` lines in
`otlab-lcd.service`.
