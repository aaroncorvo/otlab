"""
plc/esp32/wifi_config.py — lab WiFi credentials, deliberately not secret.

Imported by boot.py on every ESP32 in this lab. The MFCTP network is the
shared WiFi for the OTLab teaching environment; attendees are given these
credentials as part of the exercise. Tracking them in version control is
intentional so anyone reproducing the lab from this repo gets a working
device after `mpremote cp`.

For DEF CON hygiene: rotate per-event so creds don't leak between
cohorts. The lab WiFi password is the only shared secret in this repo
and changing it is the only credential rotation needed before any
public training session.
"""

WIFI_SSID = "MFCTP"
WIFI_PASSWORD = "P@ssw0rd!"  # noqa - intentionally tracked, see module docstring
