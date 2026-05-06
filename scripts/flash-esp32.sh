#!/usr/bin/env bash
# flash-esp32.sh — fully provision a Lonely Binary ESP32-S3 N16R8 board
# from scratch: erase flash, write MicroPython firmware, push boot.py and
# wifi_config.py, reset, verify it joins MFCTP.
#
# The ESP32 must already be plugged into a USB port on the host Pi via
# its left "UART" USB-C connector. Run from your laptop with the OTLab
# repo as cwd.
#
# Usage:
#   ./scripts/flash-esp32.sh [PI_HOST] [SERIAL_DEV]
#
# Defaults: softplc-2 mgmt IP + /dev/ttyUSB0.

set -euo pipefail

PI_HOST="${1:-otadmin@RASPLC02.local}"
SERIAL="${2:-/dev/ttyUSB0}"

# Pin firmware version so reproducing the lab in a year still works.
FW_FILE="ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin"
FW_URL="https://micropython.org/resources/firmware/${FW_FILE}"

echo "==> stage firmware + scripts on $PI_HOST"
ssh "$PI_HOST" "mkdir -p ~/lab/esp32-stage"
ssh "$PI_HOST" "[ -f /tmp/${FW_FILE} ] || curl -sL -o /tmp/${FW_FILE} '${FW_URL}'"
ssh "$PI_HOST" "ls -la /tmp/${FW_FILE}"
scp plc/esp32/boot.py plc/esp32/wifi_config.py "$PI_HOST:~/lab/esp32-stage/"

echo
echo "==> identify chip on $SERIAL"
ssh "$PI_HOST" "source ~/lab/.venv-modern/bin/activate && esptool --port '$SERIAL' --chip esp32s3 chip-id" 2>&1 | head -10

echo
echo "==> erase flash (a few seconds)"
ssh "$PI_HOST" "source ~/lab/.venv-modern/bin/activate && esptool --port '$SERIAL' --chip esp32s3 erase-flash" 2>&1 | tail -4

echo
echo "==> flash MicroPython at 0x0 (~30 s)"
ssh "$PI_HOST" "source ~/lab/.venv-modern/bin/activate && esptool --port '$SERIAL' --chip esp32s3 --baud 460800 write-flash 0x0 /tmp/${FW_FILE}" 2>&1 | tail -5

echo
echo "==> push boot.py + wifi_config.py to the device"
ssh "$PI_HOST" "source ~/lab/.venv-modern/bin/activate && cd ~/lab/esp32-stage && \
  mpremote connect '$SERIAL' cp wifi_config.py :wifi_config.py && \
  mpremote connect '$SERIAL' cp boot.py :boot.py"

echo
echo "==> hard reset and verify WiFi join (~10 s)"
ssh "$PI_HOST" "source ~/lab/.venv-modern/bin/activate && mpremote connect '$SERIAL' reset"
sleep 8
ssh "$PI_HOST" "source ~/lab/.venv-modern/bin/activate && mpremote connect '$SERIAL' exec '
import machine, ubinascii, network
mac = ubinascii.hexlify(machine.unique_id()).decode()
wlan = network.WLAN(network.STA_IF)
print(\"mac:\", mac)
print(\"connected:\", wlan.isconnected())
print(\"ifconfig:\", wlan.ifconfig())
'"

echo
echo "Done. If the MAC is not in plc/esp32/boot.py STATIC_IPS, the device"
echo "is on a DHCP lease right now. Add it to STATIC_IPS for a pinned IP, then re-run."
