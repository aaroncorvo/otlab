# OTLab — Maple Ridge ICS Training Lab

Hands-on industrial control systems training lab for [ICS Village](https://icsvillage.com/) (DEF CON village). Built on Raspberry Pi, ESP32, and Arduino hardware, with a multi-vendor honeypot fabric that emulates a small municipal water treatment plant.

> **Status (2026-05-06):** Phase 0 (host provisioning), the honeypot fabric, and Phase 1 (Modbus loop between the two real PLCs) are all live. `softplc-1`'s OpenPLC polls `softplc-2`'s sensor-sim every 100 ms, mirrors values to local registers, and exposes them on its own Modbus TCP server with link-liveness telemetry. **ESP32 #1 (Phase 3 partial)** is at static `10.20.30.40` on the lab WiFi (`MFCTP`); confirmed the lab WiFi bridges to the wired `10.20.30.0/24` segment. ESP32 firmware platform pivoted from MicroPython to **Arduino IDE on a Windows laptop** ([`docs/arduino-setup.md`](docs/arduino-setup.md)) — `plc/esp32/iot-1/iot-1.ino` is written and ready to flash. **Phase 2** (physical I/O on the soft-PLCs — pushbutton + relay-driven AD16 indicators + LED strip) is blocked on the 24 V PSU arriving; software side can move ahead in parallel.

## What's here

```
.
├── docs/                  # Build documentation, phase write-ups
│   └── lab-architecture.md   ← start here
├── honeypot/              # Conpot deployment, ready to scp to a Pi 3 B+
│                            and `docker compose up -d`
├── plc/                   # OpenPLC programs, ladder/ST source (empty until Phase 1)
├── scripts/               # Validation, attack, and utility scripts
├── reference/             # Diagrams, address maps, BOMs, vendor OID list
└── requirements.txt       # Python deps for the lab venv on softplc-1/-2
```

## The lab in one paragraph

Three Raspberry Pi hosts on a dedicated lab segment (`10.20.30.0/24`):

| Host | Hardware | Role |
|---|---|---|
| `softplc-1` | Pi 5 + Freenove GPIO breakout | OpenPLC #1 |
| `softplc-2` | Pi 5 + NVMe + Waveshare 3-CH relay HAT | OpenPLC #2 + attacker workstation |
| `honeypot-host` | Pi 3 B+ | Conpot Docker host running 3 vendor personas |

The honeypot fabric presents the **Maple Ridge Treatment Plant** — a fictional municipal water utility with three subsystems on three different vendor controllers (Siemens S7-200 distribution pumps, Schneider M340 chemical-room HVAC, Allen-Bradley CompactLogix chlorination dosing). All three speak vendor-coherent protocols, return vendor-correct SNMP enterprise OIDs, and serve vendor-themed multi-page HTTP admin UIs with internally-consistent process data.

Hardware on hand for later phases includes a Velocio Ace 1600 PLC, two Arduino UNOs with relay shields, three Lonely Binary ESP32-S3 boards, MAX485 transceivers, a Waveshare RS485-to-Ethernet gateway, the ELEGOO 37-sensor kit, AD16 indicators, an LED strip, a Tecmojo 4U rack, and Mean Well DIN-rail power supplies.

## Quick links

- **[Lab architecture & honeypot build doc](docs/lab-architecture.md)** — comprehensive working draft covering hosts, network, honeypot personas, process data per persona, deployment, ops, validation tests, phase plan
- **Phase 1 (planned):** Modbus loop between the two real soft-PLCs

## Operating the honeypot fabric

To rebuild the deployment on a fresh Pi 3 B+ (or any arm64 / amd64 Docker host on the lab segment), see [`honeypot/README.md`](honeypot/README.md). Short version:

```bash
# on the target Pi
mkdir -p ~/conpot/compose
# copy honeypot/* from this repo into ~/conpot/compose/
mkdir -p ~/conpot/compose/logs/{siemens,schneider,allenbradley}
sudo chown -R 2000:2000 ~/conpot/compose/logs/
cd ~/conpot/compose && docker compose up -d
```

Validate cross-Pi from `softplc-2` — see [`docs/lab-architecture.md`](docs/lab-architecture.md#validation-tests-cross-pi).

## License

[MIT](LICENSE). Documentation and code free to fork, adapt, and use in your own training environments.

## Contributing

This is a personal/ICS Village lab build. If you've found something useful and want to suggest improvements or share what you've built on top, open an issue or PR.
