// wifi_secrets.h — lab WiFi credentials.
//
// Intentionally tracked. The MFCTP network is the OTLab teaching WiFi;
// attendees are given these credentials as part of the exercise. Rotate
// per DEF CON event so creds don't leak between cohorts.
//
// Each ESP32 sketch ships its own copy of this header — minor duplication,
// but it keeps each sketch directory self-contained for the Arduino IDE.

#pragma once

#define WIFI_SSID     "MFCTP"
#define WIFI_PASSWORD "P@ssw0rd!"
