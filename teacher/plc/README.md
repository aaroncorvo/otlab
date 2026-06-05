# OTLab Ladder PLC

A small **persistent ladder-logic PLC engine** that runs a real scan loop
on a Pi and drives the physical Qwiic hardware:

```
read inputs (TMP117 temp, relay)  ->  evaluate ladder rungs  ->  write outputs (relay, motor)
```

Control UI + REST API on `:8091`. Built to run on the teacher Pi **and**
each student Cruiser board (each one programs its own logic against its
own Qwiic I/O).

> Verified live: with a `temp >= 25 -> motor 40%` rung, the engine read
> the real TMP117 (26.8 °C), energized the rung, and spun the real motor
> at 40% — autonomously, no human in the loop. That's a working PLC.

## Install

```bash
./teacher/plc/install-plc.sh otadmin@10.20.30.27
```

Requires the `otlab-qwiic` I/O service (see `teacher/qwiic/`) — the PLC
reaches the hardware through its REST API, so the two are decoupled.

Open `http://<pi>:8091/` (otlab / P@ssw0rd!), edit the program, **Run**.

## The default demo (wind turbine)

```
R0  temp >= 28 °C  ->  motor A 70%      "spin when warm"
R1  temp >= 31 °C  ->  motor A 100%     "full when hot"
R2  temp >= 33 °C  ->  relay ON         "alarm when too hot"
```

Warm the TMP117 with your hand and watch the turbine spin up, then the
relay trip.

## Ladder model

Clean JSON this engine owns (not tied to any external editor format).
A rung is TRUE if **any branch** is true; a branch is true if **all its
contacts** pass (parallel = OR, series = AND).

```json
{
  "name": "...",
  "scan_ms": 200,
  "rungs": [
    {
      "comment": "...",
      "branches": [                      // ORed
        [ {"type":"GE","tag":"temp","value":28} ]   // ANDed contacts
      ],
      "outputs": [ {"type":"motor","channel":"A","speed":70} ]
    }
  ]
}
```

**Contacts**

| type | meaning |
|------|---------|
| `XIC` `{tag}` | bool true (examine-if-closed) |
| `XIO` `{tag}` | bool false (examine-if-open) |
| `GE/GT/LE/LT/EQ/NE` `{tag,value}` | analog compare (e.g. temp ≥ 28) |
| `TON` `{id,preset_ms}` | on-delay timer: passes after the branch has been true `preset_ms` |
| `TOF` `{id,preset_ms}` | off-delay timer |

**Outputs**

| type | meaning |
|------|---------|
| `coil` `{tag}` | energize a bool output (`relay`) or memory bit while the rung is true |
| `coil` `{tag,latch:true}` / `{tag,unlatch:true}` | set/reset latch (OTL/OTU) |
| `motor` `{channel,speed}` | drive motor A/B at -100..100 % |

**Tags**

| tag | dir | source |
|-----|-----|--------|
| `temp` | in | TMP117 °C |
| `relay_in` | in | relay current state |
| `relay` | out | Qwiic relay |
| `motor_a` / `motor_b` | out | motor driver |
| `m0..mN` | mem | internal bits / latches |

## REST API

| Method | Path | Effect |
|--------|------|--------|
| GET | `/api/status` | running, scan_count, inputs, outputs, rung_states, timers |
| GET/POST | `/api/program` | read / save ladder JSON (validated) |
| POST | `/api/run` | start scan loop |
| POST | `/api/stop` | stop (outputs to safe state: relay off, motors 0) |

## Safety

Outputs are **non-retentive**: each scan they reset to safe defaults
(relay off, motor 0) and only true rungs energize them. On Stop, all
outputs go to the safe state. Latched bits (`OTL`) hold until unlatched.

## Next

The student-facing graphical ladder editor is the follow-up (task #55) —
either the embedded PiLab editor mapped to this JSON, or a focused OTLab
ladder canvas. The engine + JSON contract above is the stable target
either way.
