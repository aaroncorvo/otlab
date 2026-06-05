#!/usr/bin/env python3
"""otlab-vio — virtual I/O backend (a drop-in for otlab-qwiic).

Lets the ladder PLC be fully usable on a Pi that has NO physical Qwiic
hardware yet. It serves the exact same REST contract as otlab-qwiic on the
same port (:8090), so the PLC engine (otlab-plc) and the Modbus bridge
(otlab-modbus-io) talk to it without any change — they don't know or care
that the hardware is simulated.

    GET  /api/state         {temp_c,temp_f,relay,motor_a,motor_b,motor_ready}
    POST /api/relay         {on}
    POST /api/motor         {channel,speed}
    POST /api/motor/stop
    GET  /                   spinning-turbine visualization (the "plant")

The temperature is a CLOSED-LOOP THERMAL SIMULATION that the student's own
ladder logic controls:

    heat creeps the temperature up  ──►  the student's rung fires  ──►
    the turbine motor (and/or relay "cooling pump") spins  ──►
    that removes heat  ──►  temperature falls  ──►  rung clears  ──►  repeat

So a student with no hardware writes "temp_f >= 82 -> motor 70%", hits Run,
and watches the temperature climb to 82, the turbine kick on, the temp fall,
and the turbine ease off — a real control loop they can tune. The SAME
program later drives the real Qwiic turbine once the kit arrives (just swap
otlab-vio back to otlab-qwiic on :8090).

Optionally pull a REAL temperature from an ESP32 running ESPHome's REST API
(OTLAB_VIO_ESP32_URL) instead of the sim; if it's unreachable we fall back
to the sim so the demo never goes dead.

Env:
    OTLAB_VIO_PORT      HTTP listen port            (default 8090)
    OTLAB_VIO_ESP32_URL ESPHome REST base for real temp, e.g.
                        http://10.20.30.202  (unset = pure simulation)
    OTLAB_VIO_ESP32_SENSOR  ESPHome sensor path    (default /sensor/mcu_temperature)
    OTLAB_VIO_START_F   sim starting temperature    (default 78)
    DASH_USER/DASH_PASS basic auth                  (default otlab / P@ssw0rd!)
"""
import json
import os
import threading
import time
import urllib.request

from flask import Flask, Response, jsonify, request

PORT        = int(os.environ.get("OTLAB_VIO_PORT", "8090"))
ESP32_URL   = os.environ.get("OTLAB_VIO_ESP32_URL", "").rstrip("/")
ESP32_SENS  = os.environ.get("OTLAB_VIO_ESP32_SENSOR", "/sensor/mcu_temperature")
START_F     = float(os.environ.get("OTLAB_VIO_START_F", "78"))
DASH_USER   = os.environ.get("DASH_USER", "otlab")
DASH_PASS   = os.environ.get("DASH_PASS", "P@ssw0rd!")

# ── thermal model constants (tuned for a satisfying, tunable demo) ─────
HEAT_RATE     = 0.30    # °F/s the process heats with no cooling
COOL_PER_PCT  = 0.025   # °F/s of cooling per % of motor speed (100% = 2.5)
RELAY_COOL    = 0.60    # °F/s extra cooling when the relay ("pump") is on
AMBIENT_MIN   = 70.0    # floor — can't cool below ambient
TEMP_MAX      = 115.0   # ceiling — uncontrolled process pegs here

_lock = threading.Lock()
_state = {"motor_a": 0, "motor_b": 0, "relay": False}
_sim = {"temp_f": START_F}
_temp_source = "sim"          # "sim" or "esp32"
_esp32_ok = False
_esp32_last = 0.0


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


# ── optional real ESP32 temperature (ESPHome REST) ────────────────────
def _read_esp32_c():
    """Return ESP32 temperature in °C, or None if unreachable/disabled."""
    if not ESP32_URL:
        return None
    try:
        req = urllib.request.Request(ESP32_URL + ESP32_SENS)
        with urllib.request.urlopen(req, timeout=1.5) as r:
            d = json.loads(r.read().decode())
        # ESPHome returns {"value": <float>, "state": "..."}
        v = d.get("value")
        return float(v) if v is not None else None
    except Exception:
        return None


# ── the simulation loop ───────────────────────────────────────────────
def sim_loop():
    global _temp_source, _esp32_ok, _esp32_last
    last = time.monotonic()
    esp_poll = 0.0
    while True:
        now = time.monotonic()
        dt = now - last
        last = now

        real_c = None
        if ESP32_URL and (now - esp_poll) >= 1.0:
            esp_poll = now
            real_c = _read_esp32_c()
            _esp32_ok = real_c is not None
            if real_c is not None:
                _esp32_last = real_c

        with _lock:
            if ESP32_URL and _esp32_ok:
                # Track the real sensor (smoothed a touch for nice motion).
                target_f = _esp32_last * 9.0 / 5.0 + 32.0
                _sim["temp_f"] += (target_f - _sim["temp_f"]) * min(1.0, dt * 2.0)
                _temp_source = "esp32"
            else:
                # Closed-loop thermal model driven by the live outputs.
                speed = max(abs(_state["motor_a"]), abs(_state["motor_b"]))
                cooling = speed * COOL_PER_PCT
                if _state["relay"]:
                    cooling += RELAY_COOL
                d = (HEAT_RATE - cooling) * dt
                t = _sim["temp_f"] + d
                _sim["temp_f"] = max(AMBIENT_MIN, min(TEMP_MAX, t))
                _temp_source = "sim"

        time.sleep(0.2)


# ── REST API (matches otlab-qwiic) ────────────────────────────────────
app = Flask(__name__)


@app.before_request
def _guard():
    a = request.authorization
    if not (a and a.username == DASH_USER and a.password == DASH_PASS):
        return Response("auth required", 401,
                        {"WWW-Authenticate": 'Basic realm="OTLab Virtual I/O"'})


@app.route("/api/state")
def api_state():
    with _lock:
        f = _sim["temp_f"]
        return jsonify({
            "temp_c":  round(f_to_c(f), 2),
            "temp_f":  round(f, 1),
            "relay":   _state["relay"],
            "motor_a": _state["motor_a"],
            "motor_b": _state["motor_b"],
            "motor_ready": True,
            "virtual": True,
            "temp_source": _temp_source,
        })


@app.route("/api/relay", methods=["POST"])
def api_relay():
    d = request.get_json(silent=True) or {}
    with _lock:
        _state["relay"] = bool(d.get("on"))
        return jsonify({"ok": True, "relay": _state["relay"]})


@app.route("/api/motor", methods=["POST"])
def api_motor():
    d = request.get_json(silent=True) or {}
    ch = str(d.get("channel", "A")).upper()
    speed = int(d.get("speed", 0))
    speed = max(-100, min(100, speed))
    with _lock:
        _state["motor_b" if ch == "B" else "motor_a"] = speed
    return jsonify({"ok": True, "channel": ch, "speed": speed})


@app.route("/api/motor/stop", methods=["POST"])
def api_motor_stop():
    with _lock:
        _state["motor_a"] = 0
        _state["motor_b"] = 0
    return jsonify({"ok": True})


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OTLab · Virtual Turbine</title><style>
  :root{--bg:#060200;--surface:#120602;--border:#5a2c10;--hi:#ff7020;
        --text:#ffe6c8;--accent:#ff5500;--up:#ffd060;--cool:#5fe08a;--hot:#ff5a3c}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
    font-family:ui-monospace,Menlo,monospace;padding:22px}
  h1{color:var(--accent);font-size:19px;letter-spacing:.1em;margin:0 0 2px}
  .sub{color:#e0b890;font-size:12px;margin-bottom:18px}
  .badge{display:inline-block;font-size:11px;border:1px solid var(--border);border-radius:10px;
    padding:2px 9px;color:#e0b890;margin-left:8px}
  .wrap{display:flex;gap:22px;flex-wrap:wrap;align-items:stretch}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px}
  .stage{flex:1;min-width:320px;display:flex;flex-direction:column;align-items:center;justify-content:center}
  .side{width:260px;display:flex;flex-direction:column;gap:14px}
  .gauge{font-size:54px;font-weight:700;line-height:1}
  .gauge .u{font-size:22px;color:#e0b890;margin-left:6px}
  .gc{font-size:13px;color:#e0b890;margin-top:4px}
  .meter{height:10px;border-radius:5px;background:#2a1206;overflow:hidden;margin-top:12px}
  .meter>span{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--cool),var(--up),var(--hot))}
  .row{display:flex;align-items:center;justify-content:space-between;font-size:13px}
  .row b{font-size:18px;color:var(--up)}
  .lamp{width:16px;height:16px;border-radius:50%;background:#3a1c0c;border:1px solid var(--border);
    box-shadow:none;transition:.15s}
  .lamp.on{background:var(--up);box-shadow:0 0 12px var(--up)}
  .pill{font-size:11px;padding:2px 8px;border-radius:9px;border:1px solid var(--border);color:#e0b890}
  .pill.cool{color:var(--cool);border-color:var(--cool)}
  .pill.hot{color:var(--hot);border-color:var(--hot)}
  svg{display:block}
  .hint{font-size:11px;color:#a98;margin-top:14px;max-width:520px;line-height:1.5}
</style></head><body>
<h1>◴ OTLab Virtual Turbine <span class="badge" id="srcbadge">simulation</span></h1>
<div class="sub">No Qwiic hardware attached — your ladder logic drives a simulated wind turbine and a closed-loop thermal process.</div>

<div class="wrap">
  <div class="card stage">
    <svg id="turb" width="300" height="320" viewBox="0 0 300 320">
      <!-- tower -->
      <polygon points="143,300 157,300 153,120 147,120" fill="#3a2412" stroke="#5a2c10"/>
      <rect x="138" y="298" width="24" height="10" rx="2" fill="#3a2412" stroke="#5a2c10"/>
      <!-- nacelle -->
      <rect x="138" y="108" width="30" height="16" rx="4" fill="#7a4a25" stroke="#5a2c10"/>
      <!-- rotor group (spun by JS) -->
      <g id="rotor" transform="translate(150,116)">
        <g id="blades">
          <path d="M0,0 C 6,-50 4,-92 0,-104 C -4,-92 -6,-50 0,0 Z" fill="#ffd060"/>
          <path d="M0,0 C 6,-50 4,-92 0,-104 C -4,-92 -6,-50 0,0 Z" fill="#ffb84a" transform="rotate(120)"/>
          <path d="M0,0 C 6,-50 4,-92 0,-104 C -4,-92 -6,-50 0,0 Z" fill="#ff9a3c" transform="rotate(240)"/>
        </g>
        <circle cx="0" cy="0" r="8" fill="#ff7020" stroke="#2a1206"/>
      </g>
    </svg>
    <div class="row" style="width:220px"><span>Turbine</span>
      <span class="pill" id="rpmpill">stopped</span></div>
  </div>

  <div class="side">
    <div class="card">
      <div style="font-size:12px;color:#e0b890;letter-spacing:.1em;margin-bottom:6px">PROCESS TEMPERATURE</div>
      <div class="gauge"><span id="tf">--</span><span class="u">°F</span></div>
      <div class="gc"><span id="tc">--</span> °C · <span class="pill" id="tstate">--</span></div>
      <div class="meter"><span id="tbar"></span></div>
    </div>
    <div class="card">
      <div class="row"><span>Motor A (turbine)</span><b id="ma">0%</b></div>
      <div class="row" style="margin-top:8px"><span>Motor B</span><b id="mb">0%</b></div>
      <div class="row" style="margin-top:12px"><span>Relay (cooling pump)</span><span class="lamp" id="relay"></span></div>
    </div>
    <div class="card">
      <div class="row"><span>Source</span><span class="pill" id="src">sim</span></div>
      <div class="hint">Program the logic in the <b>Ladder PLC</b> (:8091). Heat rises on its
        own; your rungs must spin the turbine to cool it. Tune your setpoints
        and watch the loop settle.</div>
    </div>
  </div>
</div>

<script>
let angle=0, speed=0, last=performance.now();
function frame(now){
  const dt=(now-last)/1000; last=now;
  angle += speed*3.0*dt;          // deg/s scales with motor %
  document.getElementById('rotor').setAttribute('transform',
    'translate(150,116) rotate('+angle.toFixed(1)+')');
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

async function refresh(){
  try{
    const r=await fetch('/api/state'); const s=await r.json();
    speed = s.motor_a||0;
    const tf=s.temp_f, tc=s.temp_c;
    document.getElementById('tf').textContent=(tf!=null?tf.toFixed(1):'--');
    document.getElementById('tc').textContent=(tc!=null?tc.toFixed(1):'--');
    document.getElementById('ma').textContent=(s.motor_a||0)+'%';
    document.getElementById('mb').textContent=(s.motor_b||0)+'%';
    document.getElementById('relay').className='lamp'+(s.relay?' on':'');
    // temp meter 70..115 -> 0..100%
    const pct=Math.max(0,Math.min(100,((tf-70)/(115-70))*100));
    const bar=document.getElementById('tbar'); bar.style.width=pct+'%';
    const ts=document.getElementById('tstate');
    if(tf>=95){ts.textContent='HOT';ts.className='pill hot'}
    else if(tf<=80){ts.textContent='cool';ts.className='pill cool'}
    else{ts.textContent='warm';ts.className='pill'}
    const rp=document.getElementById('rpmpill');
    if(!s.motor_a){rp.textContent='stopped';rp.className='pill'}
    else{rp.textContent=(s.motor_a>0?'▶ ':'◀ ')+Math.abs(s.motor_a)+'%';
         rp.className='pill '+(s.motor_a>0?'cool':'hot')}
    const src=(s.temp_source==='esp32')?'ESP32 (real)':'simulation';
    document.getElementById('src').textContent=src;
    document.getElementById('srcbadge').textContent=src;
  }catch(e){}
}
refresh(); setInterval(refresh, 500);
</script></body></html>"""


def main():
    print(f"otlab-vio: port={PORT} esp32={ESP32_URL or '(sim only)'} "
          f"start={START_F}F", flush=True)
    threading.Thread(target=sim_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
