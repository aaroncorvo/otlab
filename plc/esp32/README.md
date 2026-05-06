# plc/esp32/

MicroPython firmware + scripts for the three Lonely Binary ESP32-S3 N16R8 boards in the lab.

## Status (2026-05-06)

- **ESP32 #1** — `iot-1` at `10.20.30.40`, MAC `58:e6:c5:6f:42:80`. MicroPython 1.28.0 (SPIRAM_OCT) flashed; `boot.py` auto-joins MFCTP and pins the static IP. Verified bidirectional reachability (pings from softplc-1, raw-Modbus reads of sensor-sim).
- **ESP32 #2** — Not yet flashed. Planned role: keypad-based operator HMI at `10.20.30.30`.
- **ESP32 #3** — Not yet flashed. Planned role: attacker / WiFi sniff platform at `10.20.30.60`.

## Architecture finding: MFCTP bridges to the wired lab segment

The lab WiFi (`MFCTP`) is bridged onto the same Layer 2 broadcast domain as the wired `eth0` lab segment. Verified by booting the ESP32 with DHCP — it leased `10.20.30.204` and could reach `softplc-1` (10.20.30.111) and the wired honeypots (`.50/.51/.52`) directly. So our wired and wireless tiers share `10.20.30.0/24` with a single DHCP server (gateway `10.20.30.1`, DNS `10.20.30.1`).

## Files

| File | Purpose |
|---|---|
| `wifi_config.py` | SSID + password for MFCTP. **Intentionally tracked** — these are public lab credentials given to attendees. Rotate per DEF CON event. |
| `boot.py` | Runs at every device boot. Joins WiFi, applies static IP based on the chip's MAC, prints status. Same `boot.py` works on all three ESP32s; per-device IPs come from the `STATIC_IPS` table. |

## Flashing a fresh ESP32-S3

Plug the ESP32 into a USB port on a Pi (typically `softplc-2`, which has the `~/lab/.venv-modern` venv with `esptool` and `mpremote` already installed). The board uses a CH340 USB-UART bridge; once plugged in it appears as `/dev/ttyUSB0` (or `/dev/ttyUSB1` if other serial devices are present).

From your laptop:

```bash
./scripts/flash-esp32.sh otadmin@RASPLC02.local /dev/ttyUSB0
```

That script (in `scripts/`) does the full provisioning: erase flash, write the latest MicroPython SPIRAM_OCT firmware, copy `wifi_config.py` and `boot.py` to the device, reset, and verify it joins MFCTP.

After flashing, the new MAC needs to be added to `boot.py`'s `STATIC_IPS` table to get a stable lab IP. Find the MAC by running:

```bash
ssh otadmin@RASPLC02.local 'source ~/lab/.venv-modern/bin/activate && mpremote exec "import machine, ubinascii; print(ubinascii.hexlify(machine.unique_id()).decode())"'
```

…then edit `boot.py`, push the updated `STATIC_IPS` map, and reboot.

## REPL access

```bash
ssh otadmin@RASPLC02.local 'source ~/lab/.venv-modern/bin/activate && mpremote'
# (inside) connect /dev/ttyUSB0 repl
```

To exit the REPL: `Ctrl-X`. To soft-reset on-device: `Ctrl-D`.

## Coming next

Tonight got the device alive on the network. Real "vendor IIoT monitoring" behavior comes in the next chunk: a small MicroPython Modbus TCP slave (similar in shape to `plc/sensor-sim.py` but on-device), exposing one or two simulated sensor values. That makes ESP32 #1 a fourth Modbus endpoint on the lab network alongside softplc-1, softplc-2, and the three Conpot honeypots.

## Reference

- MicroPython downloads: <https://micropython.org/download/ESP32_GENERIC_S3/>
- Lonely Binary N16R8 board details: <https://www.lonelybinary.com/products/esp32-s3>
- mpremote tool docs: <https://docs.micropython.org/en/latest/reference/mpremote.html>
