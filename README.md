# OTLab — Maple Ridge ICS Training Lab

Hands-on industrial control systems training lab for [ICS Village](https://icsvillage.com/) (DEF CON village). Built on Raspberry Pi, ESP32, and Arduino hardware, with a multi-vendor honeypot fabric that emulates a small municipal water treatment plant.

> **Status (2026-05-06):** Phase 0 (host provisioning) and the honeypot fabric are live. Phase 1 (Modbus loop between the two real soft-PLCs) is up next.

## What's here

```
.
├── docs/                  # Build documentation, phase write-ups
│   └── lab-architecture.md   ← start here
├── honeypot/              # Conpot deployment (mirror of ~/conpot/compose/ on honeypot-host)
├── plc/                   # OpenPLC programs, ladder/ST source
├── scripts/               # Validation, attack, and utility scripts
└── reference/             # Diagrams, address maps, BOMs, vendor OID list
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

Lives on `honeypot-host:~/conpot/compose/` (not yet mirrored into this repo). From there:

```bash
docker compose up -d         # bring all three personas online
docker compose ps            # check status
docker compose logs -f honeypot-siemens
docker compose down          # stop
```

Then validate cross-Pi from `softplc-2` — see [docs/lab-architecture.md](docs/lab-architecture.md#validation-tests-cross-pi).

## License

[MIT](LICENSE). Documentation and code free to fork, adapt, and use in your own training environments.

## Contributing

This is a personal/ICS Village lab build. If you've found something useful and want to suggest improvements or share what you've built on top, open an issue or PR.
