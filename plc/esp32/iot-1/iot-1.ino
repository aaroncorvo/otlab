/*
 * iot-1.ino — ESP32 #1 firmware for the OTLab.
 *
 * Phase 3 wireless tier, "vendor IIoT monitoring device" persona.
 * Lives at 10.20.30.40 (static), MAC 58:e6:c5:6f:42:80 (Lonely Binary
 * ESP32-S3 N16R8 #1).
 *
 * Tonight's scope: bring up WiFi, pin static IP, heartbeat over Serial.
 * Future versions of this sketch will expose a Modbus TCP slave so
 * softplc-1 (or anyone else) can poll the device as if it were a real
 * vendor sensor.
 *
 * IDE setup: see /docs/arduino-setup.md in this repo for the one-time
 * board-core install + per-sketch settings.
 *
 * Required Tools menu settings for this sketch:
 *   Board:                  ESP32S3 Dev Module
 *   USB CDC On Boot:        Disabled        (we use the CH340 UART, left USB-C)
 *   Flash Size:             16MB (128Mb)
 *   Partition Scheme:       16M Flash (3MB APP/9.9MB FATFS)
 *   PSRAM:                  OPI PSRAM       (this is the key one — N16R8 = octal)
 *   Upload Speed:           921600
 *   Port:                   whatever shows up when the board is plugged in
 *
 * Wrong PSRAM mode crashes at boot with "rst:0x10 (RTCWDT_RTC_RESET)" or
 * heap allocation failures. If you see anything like that: re-check the
 * PSRAM setting first.
 */

#include <WiFi.h>

#include "wifi_secrets.h"

// Lab IP plan: ESP32 #1 = iot-1 = 10.20.30.40
static const IPAddress STATIC_IP (10,  20, 30,  40);
static const IPAddress GATEWAY   (10,  20, 30,   1);
static const IPAddress SUBNET    (255, 255, 255, 0);
static const IPAddress DNS       (10,  20, 30,   1);

static const char* HOSTNAME = "iot-1";

static const unsigned long WIFI_CONNECT_TIMEOUT_MS = 15000;
static const unsigned long HEARTBEAT_INTERVAL_MS   = 10000;

static unsigned long last_heartbeat_ms = 0;

void setup() {
  Serial.begin(115200);
  delay(500);  // give the USB-UART side a moment to enumerate
  Serial.println();
  Serial.println(F("[boot] iot-1 starting"));

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);

  // WiFi.config() applies a static IP. It must be called *before* WiFi.begin()
  // for the static config to take effect on the first DHCP request.
  if (!WiFi.config(STATIC_IP, GATEWAY, SUBNET, DNS)) {
    Serial.println(F("[boot] WiFi.config() failed; falling back to DHCP"));
  }

  Serial.printf("[boot] joining %s\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_CONNECT_TIMEOUT_MS) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[boot] connected: ip=%s rssi=%d dBm mac=%s\n",
      WiFi.localIP().toString().c_str(),
      WiFi.RSSI(),
      WiFi.macAddress().c_str()
    );
  } else {
    Serial.printf("[boot] CONNECT FAILED (status=%d) — will keep trying in loop()\n",
      WiFi.status()
    );
  }
}

void loop() {
  unsigned long now = millis();

  // Periodic status line so a serial monitor / journal sees the device alive
  if (now - last_heartbeat_ms >= HEARTBEAT_INTERVAL_MS) {
    last_heartbeat_ms = now;
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("[hb] up=%lus ip=%s rssi=%d dBm heap=%u\n",
        now / 1000,
        WiFi.localIP().toString().c_str(),
        WiFi.RSSI(),
        ESP.getFreeHeap()
      );
    } else {
      Serial.printf("[hb] up=%lus wifi DOWN (status=%d), reconnecting...\n",
        now / 1000, WiFi.status()
      );
      WiFi.reconnect();
    }
  }

  delay(50);
}
