# Test Library

Small purpose-built scripts that exercise the lab. Each is single-purpose, well-commented, and has expected behavior documented in its header.

## Catalog

| Script | Purpose | Runtime | Cleanup |
|---|---|---|---|
| `test-modbus-read-sweep.py`  | Read all 8 registers + 8 coils on both Modbus endpoints. Reachability sanity + wire baseline. | ~2 s | none |
| `test-modbus-write.py`        | Single FC5/FC6 write to a chosen target. CLI wrapper for the dashboard's Write Playground. | ~1 s | leaves override (use `--target` carefully) |
| `test-modbus-write-storm.py` | Bombard sensor-sim with FC6 writes (default 200 in 10 s). Auto-clears overrides at end. | ~10 s | self-cleans |
| `test-modbus-replay.py`       | Extract FC5/6 frames from a captured pcap and replay them. Demonstrates absence of replay protection. | varies | leaves overrides |
| `test-dnp3-scan.py`           | Probe the DNP3 outstation on l1-plc-01:20000. Link-Layer + Read Class 0. | ~3 s | none |
| `test-snmp-fingerprint.sh`    | snmpwalk vendor enterprise OIDs against each Conpot persona. ICS reconnaissance pattern. | ~5 s | none |
| `test-arp-discovery.sh`       | Passive + active discovery of every device on `10.20.30.0/24`. Compares to expected inventory. | ~5 s | none |
| `test-tls-cipher-scan.sh`     | Enumerate TLS ciphers + cert exposed by the dashboard. Detects self-signed cert. | ~10 s | none |
| `test-ssh-bruteforce.sh`      | Fire N failed-auth attempts at a target Pi. Populates `failed_ssh_1h` counter. | ~5-30 s | may need `fail2ban-client unban --all` if fail2ban is deployed |

## How to run

These scripts ship with `install-sensor-sim.sh` to `/home/otuser/lab/tests/` on l1-plc-01 (where sensor-sim runs). Run them directly:

```bash
ssh otadmin@l1-plc-01.local 'sudo -u otuser /home/otuser/lab/.venv-modern/bin/python3 /home/otuser/lab/tests/test-modbus-read-sweep.py'
```

Or from the dashboard's **Teaching tab** → **Test Library** panel — picks the test, runs it via SSH-as-otadmin (the dashboard lives on l3-mon-01 and shells out to whichever Pi hosts the test scripts), displays output with diff against expected (when reference output is present in `expected/`).

## Detection signatures

For each test script, defenders should be able to detect the activity. If your IDS/SOC can't see these, your detection is incomplete:

| Test | Detection signature |
|---|---|
| read-sweep | FC3 reads to addresses outside the master's known poll range, or read-counts > master's typical |
| write-storm | Burst of FC6 from any IP > 5 writes/sec |
| write-single | Any FC5/6 from a non-master IP |
| replay | Two identical FC6 PDUs with widely-separated timestamps from the same src IP |
| dnp3-scan | TCP connect to :20000 from any non-master IP, or any DNP3 link-layer frame at all |
| snmp-fingerprint | SNMP queries to Conpot persona IPs (any external IP hitting these = malicious-by-design) |
| arp-discovery | Burst of ICMP echo to 10.20.30.{1..254} |
| tls-cipher-scan | Multiple TLS handshakes to :8000 with varied cipher offers |
| ssh-bruteforce | ≥5 sshd "Invalid user" or "Failed password" entries in 60 s |

## Adding a new test

1. New file at `plc/tests/test-<name>.{py,sh}`. Python = pure-stdlib + pymodbus where needed; bash = standard utilities only.
2. Header docstring covers: what it does, real-world parallel, expected behavior, cleanup notes.
3. Add an `expected/test-<name>.txt` reference output if the dashboard's grading mode should diff against it.
4. Add a row to the catalog table above.
5. Re-run `install-sensor-sim.sh` to push to the Pi.

The dashboard's Test Library panel auto-discovers everything in `/home/otuser/lab/tests/` matching `test-*.{py,sh}` — no dashboard code change needed when adding a test.
