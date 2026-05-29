# OTLab — ESPHome I/O devices

Three ESP32-S3-N16R8 boards (Lonely Binary "Gold Edition" kit) deployed
as networked I/O devices for the classroom. Each one runs ESPHome
firmware, joins the OT-lab WiFi, exposes a REST API + web UI, and is
reachable from anywhere on the tailnet.

## Hardware

| Item | Spec |
|---|---|
| Board | Lonely Binary ESP32-S3-N16R8 Gold Edition |
| MCU | ESP32-S3 (dual-core Xtensa LX7 @ 240 MHz) |
| Flash | 16 MB QIO |
| PSRAM | 8 MB octal |
| Wireless | 2.4 GHz Wi-Fi (802.11 b/g/n) + Bluetooth LE 5.0 |
| USB | CH340 USB-UART (1a86:7522) for flashing/serial; ESP32-S3 native USB-OTG also present |
| Status LED | WS2812 RGB on GPIO48 |
| Buttons | BOOT (GPIO0) + RESET |
| Pinout | 44 GPIO broken out via 0.1" headers + supplied screw-terminal / push-terminal / pluggable terminal expansion boards |

Per the kit: 3 boards total, one attached to each of teacher, student-01, and student-02 via USB.

## File layout

| File | Purpose |
|---|---|
| `otlab-esp-common.yaml` | Shared base config (WiFi, web server, status LED, internal sensors). Per-device YAMLs include this via the `packages:` mechanism. |
| `otlab-esp-teacher.yaml` | Teacher's ESP32. Substitutes hostname + friendly name only. |
| `otlab-esp-student-01.yaml` | Student 1's ESP32. |
| `otlab-esp-student-02.yaml` | Student 2's ESP32. |
| `secrets.yaml` | Wi-Fi credentials + ESPHome API key + OTA password. Lab convention (intentionally public — rotate when used outside the lab). |

## Deploying

### 1. ESPHome Dashboard on the teacher Pi (one-time)

```bash
./scripts/install-esphome-dashboard.sh otadmin@otlab-teacher
```

After this, open **http://otlab-teacher:6052** in your browser. The dashboard auto-detects YAMLs in `/config/` (which is bind-mounted to `/home/otadmin/teacher/esphome/` on the Pi, kept in sync with this directory).

### 2. First-time USB flash (per ESP32)

```bash
./scripts/flash-esp32.sh otadmin@otlab-teacher       otlab-esp-teacher.yaml
./scripts/flash-esp32.sh otadmin@10.20.30.49        otlab-esp-student-01.yaml
./scripts/flash-esp32.sh otadmin@10.20.30.47        otlab-esp-student-02.yaml
```

Each command compiles the YAML inside a one-shot `esphome/esphome` container running on the target Pi (which has the ESP32 attached via USB), then flashes via CH340 auto-reset over `/dev/ttyUSB0`. Takes ~5 min the first time (image pull + IDF compile).

After flashing, the ESP32 reboots, joins MFCTP Wi-Fi, gets a DHCP lease from TP-Link, and starts answering at:

- `http://otlab-esp-teacher.local/` (mDNS)
- `http://otlab-esp-student-01.local/`
- `http://otlab-esp-student-02.local/`

Or by the DHCP-assigned IP shown in your TP-Link's lease table.

### 3. Subsequent updates — all OTA via the Dashboard

After the first flash, USB is no longer needed. Edit the YAML in the dashboard (or in this repo + re-rsync), click "INSTALL", pick **"Wirelessly"**, and ESPHome OTA-flashes via the API. ~30 sec per device.

## What the base config gives you

Out of the box, every device exposes:

| Endpoint | What |
|---|---|
| `GET  /` | Web UI showing all sensors + controls |
| `GET  /sensor/uptime` | Seconds since boot |
| `GET  /sensor/wifi_rssi` | Wi-Fi signal strength (dBm) |
| `GET  /sensor/mcu_temperature` | ESP32-S3 internal temperature |
| `GET  /text_sensor/ip_address` | Current IP |
| `GET  /text_sensor/ssid` | Connected SSID |
| `GET  /binary_sensor/boot_button` | BOOT button state |
| `POST /light/status_led/turn_on?r=255&g=0&b=0` | Set RGB LED |
| `POST /light/status_led/turn_off` | Turn LED off |
| `POST /button/restart/press` | Reboot the device |

All accessible from your Mac via tailscale.

## Adding sensors

Edit the relevant per-device YAML and uncomment the right block (examples are commented in `otlab-esp-common.yaml`). For example, to add a DHT22 temperature/humidity sensor on GPIO4:

```yaml
# in otlab-esp-teacher.yaml
substitutions:
  name: otlab-esp-teacher
  friendly_name: "OTLab ESP — Teacher"

packages:
  base: !include otlab-esp-common.yaml

sensor:
  - platform: dht
    model: DHT22
    pin: GPIO4
    temperature:
      name: "Office temperature"
    humidity:
      name: "Office humidity"
    update_interval: 10s
```

Save, click "INSTALL" in the dashboard, pick "Wirelessly", done. New endpoints appear automatically:

- `GET /sensor/office_temperature` → 22.4
- `GET /sensor/office_humidity` → 47.1

## Modbus TCP slave (Phase 2 — not yet shipped)

The OTLab pedagogy story works best if each ESP32 advertises as a Modbus TCP slave that students can poll from their virtual `modbus-master` containers. ESPHome's native `modbus_controller` is CLIENT-only (it polls other slaves), so we need either:

1. **External component** — there's a community `modbus_server` component for ESPHome that adds slave support. Add via `external_components:` in the YAML.
2. **Pi-side gateway** — the Pi runs a small Python service (`pymodbus`) that maps Modbus registers to REST calls against the ESP32. Pi exposes Modbus TCP on its own IP; reads/writes translate to ESPHome REST.

Tracked as a follow-up. For tonight, students can interact with the ESP32 over HTTP REST — the OT story comes online when phase 2 ships.

## Recovery

If WiFi credentials in `secrets.yaml` are wrong and the device can't join the lab network, ESPHome falls back to a hotspot mode. The device broadcasts `<name>-fallback` SSID with the `ap_password` from `secrets.yaml`. Connect a phone to it, browse `http://192.168.4.1` to fix the WiFi config (or just re-flash via USB).

If the device is completely bricked (rare), hold BOOT, press RESET, release BOOT to enter the ESP32 ROM bootloader, then re-flash via USB:

```bash
./scripts/flash-esp32.sh otadmin@<pi> <yaml-name>
```

## See also

- ESPHome docs: https://esphome.io/
- ESPHome component reference: https://esphome.io/components/
- Lonely Binary tutorial: https://lonelybinary.com/pages/esp32-s3
