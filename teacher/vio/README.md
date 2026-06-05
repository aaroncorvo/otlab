# OTLab Virtual I/O (simulated turbine)

Makes the ladder PLC **fully usable on a Pi with no physical Qwiic hardware
yet**. `otlab-vio` is a drop-in for `otlab-qwiic`: it serves the exact same
REST contract on the same port (`:8090`), so the PLC engine
(`otlab-plc`) and the Modbus bridge (`otlab-modbus-io`) talk to it **without
any change** — they never know the hardware is simulated.

```
Ladder PLC (:8091)  ──REST :8090──►  otlab-vio  ──►  closed-loop thermal sim
   student's logic                                   + spinning-turbine page
```

The temperature is a **closed-loop thermal simulation the student's own
logic controls**:

```
heat creeps the temperature up  ─►  the student's rung fires  ─►
the turbine motor (and relay "cooling pump") spin  ─►  heat is removed  ─►
temperature falls  ─►  the rung clears  ─►  repeat
```

So a student writes `temp_f >= 82 -> motor 70%`, hits **Run**, and watches
the temperature climb to 82 °F, the turbine kick on, the temp fall, and the
turbine ease off — a real, tunable control loop with no hardware. The
**same program** later drives the real Qwiic turbine once the kit arrives.

> Verified on student-01: with the default demo running, the sim held the
> process at ~82 °F, the turbine cycling 0/70% to keep it there. The
> bang-bang chatter at the setpoint is real and is a built-in teachable
> moment — it's the reason you'd add a hysteresis timer.

## The turbine page

`http://<pi>:8090/` (otlab / P@ssw0rd!) renders a spinning wind turbine
whose speed tracks the commanded motor %, a process-temperature gauge, and a
relay ("cooling pump") lamp — all driven live by the sim. This is the
"plant" the student is controlling.

## Install + switch

```bash
# stage the service (does not disturb a running otlab-qwiic)
./teacher/vio/install-vio.sh otadmin@10.20.30.49

# run the simulated turbine instead of physical hardware
./teacher/vio/switch-io.sh   otadmin@10.20.30.49 virtual

# switch back when the Qwiic kit arrives — same PLC program, real turbine
./teacher/vio/switch-io.sh   otadmin@10.20.30.49 physical
```

Only one service owns `:8090` at a time (`otlab-vio` `Conflicts=` with
`otlab-qwiic`). The PLC and Modbus bridge keep pointing at `:8090`, so the
swap is transparent to them — that's why their units were relaxed from a
hard `Requires=otlab-qwiic` to ordering-only `After=` (a hard requirement
would resurrect the physical service when switching to virtual).

## Real ESP32 temperature (optional)

By default the input is pure simulation. To drive it from a **real** ESP32
instead, point it at the board's ESPHome REST API:

```ini
# in otlab-vio.service
Environment=OTLAB_VIO_ESP32_URL=http://10.20.30.202
```

It polls `\/sensor/mcu_temperature` once a second and uses that as the
process temperature; if the board is unreachable it falls back to the sim,
so the demo never goes dead. (The in-fabric `modbus-gateway` also carries
the ESP32 temperature on Modbus, but the ESP32's own REST API is directly
reachable from the Pi host, so vio uses that.)

## Tuning the thermal model

Env / constants in `otlab_vio.py`:

| knob | default | effect |
|------|---------|--------|
| `OTLAB_VIO_START_F` | 78 | starting temperature |
| `HEAT_RATE` | 0.30 °F/s | how fast it heats with no cooling |
| `COOL_PER_PCT` | 0.025 °F/s/% | turbine cooling per % motor speed |
| `RELAY_COOL` | 0.60 °F/s | extra cooling when the relay is on |
| `AMBIENT_MIN` / `TEMP_MAX` | 70 / 115 °F | clamp range |

An uncontrolled process pegs at `TEMP_MAX` — which is the point: it gives the
student a reason to write control logic.

## Where the turbine "dashboard" lives

The turbine visualization is the vio `:8090` page itself, because vio runs on
the Pi host right next to the PLC. The OTLab Dashboard (`:8000`) is a
ContainerLab container in a separate network namespace, so embedding the
turbine there would mean a per-student image rebuild plus cross-namespace
plumbing to reach the host services — deliberately avoided. Link students to
`:8090` for the live turbine.
