# reference/

Diagrams, address maps, BOMs, vendor protocol references, packet captures — anything that doesn't change much but is useful to have in one place.

## Current contents

```
reference/
└── captures/
    └── phase1-sensor-sim-cross-pi.pcap   1.7 KB, 19 packets
```

### Packet captures

`phase1-sensor-sim-cross-pi.pcap` — captured 2026-05-06 with `tcpdump -i eth0 -w` on `softplc-2`. Shows 4 Modbus TCP request/response pairs from `softplc-1` (10.20.30.111) reading the sensor-sim slave on `softplc-2:5020`. Includes the TCP three-way handshake and a clean disconnect.

Open in Wireshark. The dissector recognizes Modbus on any port via right-click → Decode As → Modbus/TCP. Useful for showing students:

- The MBAP header layout (transaction ID / protocol ID / length / unit ID)
- FC 3 request structure: function code + start address + register count
- FC 3 response structure: function code + byte count + register values (big-endian 16-bit)
- How Modbus rides on a single persistent TCP connection rather than reconnecting per request

## Suggestions for what to add as the lab grows

- Network topology diagram (mermaid or drawn)
- Rack layout drawing once Phase 4 wires it
- Bill of materials with prices and links
- Vendor OID lookup table (Siemens / Schneider / Rockwell / et al)
- Modbus function code quick reference
- DEF CON village handout PDFs once produced
- More pcaps as we add protocols (S7Comm against the honeypot, EtherNet/IP against the AB persona, etc.)
