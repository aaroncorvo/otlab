"""
plc/esp32/boot.py — runs at every boot.

MicroPython looks for `boot.py` in the device's filesystem and executes
it before anything else. This one:

  1. Brings up WiFi as a station, joins MFCTP using credentials from
     wifi_config.py.
  2. Pins a static IP per the lab IP plan (10.20.30.40+ for IoT).
  3. Prints status to the REPL so an mpremote watcher can see the device
     come up.

Per-device static IP is keyed off the chip's MAC address so the same
boot.py works on all three ESP32s without per-board edits. Add new
boards to STATIC_IPS by reading the MAC after first boot:

    >>> import machine, ubinascii
    >>> ubinascii.hexlify(machine.unique_id()).decode()
    '58e6c56f4280'
"""

import gc
import time
import network
import machine
import ubinascii

import wifi_config


# Per-device static IP map keyed by lower-case hex MAC (no colons).
# IP plan: .40-.49 reserved for IoT devices. Hostname is purely cosmetic
# (MicroPython's WLAN hostname is set best-effort; some APs ignore it).
STATIC_IPS = {
    "58e6c56f4280": ("10.20.30.40", "iot-1"),    # ESP32 #1 - vendor IIoT monitoring device
    # Add ESP32 #2 (HMI) and #3 (attacker) MACs here when their boards come up.
}

LAB_NETMASK = "255.255.255.0"
LAB_GATEWAY = "10.20.30.1"
LAB_DNS     = "10.20.30.1"


def _mac_str() -> str:
    return ubinascii.hexlify(machine.unique_id()).decode().lower()


def connect_wifi(timeout_s: float = 15.0) -> network.WLAN:
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    mac = _mac_str()
    if mac in STATIC_IPS:
        ip, hostname = STATIC_IPS[mac]
        try:
            wlan.config(hostname=hostname)   # may be ignored by some firmware builds
        except (OSError, ValueError):
            pass
        wlan.ifconfig((ip, LAB_NETMASK, LAB_GATEWAY, LAB_DNS))
        print(f"[boot] mac={mac} -> static {ip} ({hostname})")
    else:
        print(f"[boot] mac={mac} not in STATIC_IPS table; using DHCP")

    print(f"[boot] connecting to {wifi_config.WIFI_SSID}...")
    wlan.connect(wifi_config.WIFI_SSID, wifi_config.WIFI_PASSWORD)

    deadline = time.time() + timeout_s
    while not wlan.isconnected() and time.time() < deadline:
        time.sleep(0.5)

    if wlan.isconnected():
        ip, _, gw, _ = wlan.ifconfig()
        print(f"[boot] connected: ip={ip} gw={gw} rssi={wlan.status('rssi')}dBm")
    else:
        print("[boot] WIFI CONNECT FAILED")

    return wlan


# Run at import time so MicroPython's normal boot sequence kicks WiFi off.
gc.collect()
wlan = connect_wifi()
gc.collect()
