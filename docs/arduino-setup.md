# Arduino IDE setup for the OTLab ESP32 + UNO work

One-time setup steps for the Windows laptop you'll use to flash sketches in `plc/esp32/<board>/` and (later) `plc/uno/<board>/`. Do this once; subsequent uploads are just open-the-sketch + click-upload.

## 1. Install the ESP32 board core

Arduino IDE doesn't know how to talk to ESP32s out of the box. Install Espressif's board support:

1. Open Arduino IDE.
2. **File → Preferences**.
3. In the **Additional Boards Manager URLs** field, paste:
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
   If the field already has other URLs, add this one separated by a comma.
4. Click **OK**.
5. **Tools → Board → Boards Manager**.
6. Type `esp32` in the search.
7. Find **"esp32" by Espressif Systems** and click **Install**. (Latest 3.x.x as of 2026 is fine.)
8. Wait — first install pulls ~200 MB of toolchain. Several minutes.

## 2. Install the CH340 USB-UART driver (Windows only)

The Lonely Binary ESP32-S3 N16R8 boards in this lab use a **CH340** chip for USB-serial bridging on the left ("UART") USB-C port. Windows doesn't ship CH340 drivers by default.

- Driver download: <https://www.wch-ic.com/downloads/CH341SER_EXE.html> (vendor's site, current as of 2026).
- Install, plug ESP32 in via the **left** USB-C port (labeled UART, not USB).
- Confirm in Device Manager → Ports (COM & LPT) you see something like **USB-SERIAL CH340 (COM5)**.

## 3. Per-sketch board settings

Every time you open one of these sketches for the first time on a fresh IDE install, set the **Tools** menu like this for ESP32-S3 N16R8 boards:

| Tools menu setting | Value | Why |
|---|---|---|
| Board | **ESP32S3 Dev Module** | Generic S3, fits Lonely Binary boards |
| USB CDC On Boot | **Disabled** | We use the CH340 UART on the left port, not the right USB-OTG port |
| CPU Frequency | 240 MHz (WiFi) | Default; full speed |
| Flash Mode | **QIO 80MHz** | Default for the N16R8 SKU |
| Flash Size | **16MB (128Mb)** | The N in N16R8 = 16 MB flash |
| Partition Scheme | **16M Flash (3MB APP/9.9MB FATFS)** | Keep room for OTA + a filesystem if we want it later |
| PSRAM | **OPI PSRAM** | The R in N16R8 = 8 MB **octal** PSRAM. Wrong setting here = boot crash. |
| Upload Speed | 921600 | Faster uploads |
| Erase All Flash Before Sketch Upload | (off, on first time) | Off keeps your sketch's filesystem; turn on if a flash is acting weird |

The sketch comments duplicate the critical ones (especially PSRAM) so you don't have to look them up.

## 4. Confirm the install

1. **File → Examples → 01.Basics → Blink** (Arduino's standard hello-world).
2. Set the board settings as above.
3. Plug ESP32 in. **Tools → Port** → pick the new COM port.
4. Click **Upload**.
5. Open **Tools → Serial Monitor**, set to **115200 baud**.
6. You won't see anything from Blink (it just toggles a pin), but the upload completing and the device rebooting cleanly proves the toolchain works end-to-end.

If upload fails with "A fatal error occurred: Failed to connect to ESP32-S3":
- Hold the **BOOT** button while clicking Upload, release after "Connecting..." appears.
- This puts the chip into download mode manually. Some boards don't auto-DTR/RTS reliably.

## 5. Workflow for OTLab sketches

```
on Windows:
  cd <wherever you cloned otlab>
  git pull
  open plc/esp32/iot-1/iot-1.ino in Arduino IDE
  upload
  open Serial Monitor (115200 baud) to watch boot log

on Mac (or wherever):
  ssh into a Pi on the lab segment, ping/poll the device to verify
```

## 6. Troubleshooting

- **Sketch won't compile, "WiFi.h: No such file or directory"** — wrong board selected. Set Board to **ESP32S3 Dev Module**, not Arduino Uno.
- **Crash loop with "rst:0x10 (RTCWDT_RTC_RESET)"** — wrong PSRAM mode. Set PSRAM = **OPI PSRAM** (not "Quad SPI" or "Disabled"). The N16R8 boards have octal PSRAM.
- **Upload starts then "fatal error: Failed to connect"** — hold BOOT while clicking upload (see section 4).
- **Sketch uploads but nothing on Serial Monitor** — check baud (115200), check you're on the right COM port, check **USB CDC On Boot = Disabled** (otherwise the chip serial routes to the *right* USB port instead of through the CH340 on the left).
- **Static IP not taking** — `WiFi.config()` must be called *before* `WiFi.begin()`. Already correct in the lab's sketches; flag if you write your own.

## 7. (Optional) UNO board core

For Phase 3's UNO work, the IDE comes with the AVR core preinstalled — no extra install needed. Just plug an UNO in, set Board → Arduino Uno, and you're good.

## Reference

- Arduino-ESP32 docs: <https://docs.espressif.com/projects/arduino-esp32/en/latest/>
- Lonely Binary N16R8 schematic + pinout: <https://www.lonelybinary.com/products/esp32-s3>
- CH340 driver (vendor): <https://www.wch-ic.com/downloads/CH341SER_EXE.html>
