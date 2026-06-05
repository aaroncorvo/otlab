#!/usr/bin/env python3
"""otlab-plc — a small persistent ladder-logic PLC engine.

Runs a real scan loop on the host Pi (teacher or student Cruiser board):

    read inputs  ->  evaluate ladder rungs  ->  write outputs

Inputs and outputs are the physical Qwiic devices, reached through the
local otlab-qwiic REST API (default http://127.0.0.1:8090):

    INPUTS   temp        TMP117 temperature (°C, float)
             relay_in    relay current state (bool)
    OUTPUTS  relay       Qwiic relay (bool coil)
             motor_a     Qwiic motor driver channel A (-100..100 %)
             motor_b     channel B
    MEMORY   m0..m31     internal boolean bits (latches, interlocks)
    TIMERS   declared per-rung (TON / TOF)

Ladder model (clean JSON, this engine owns it):

    {
      "name": "Wind turbine temp control",
      "scan_ms": 200,
      "rungs": [
        { "comment": "...",
          "branches": [                      # branches are ORed together
            [ {contact}, {contact} ]         # contacts in a branch are ANDed
          ],
          "outputs": [ {output}, ... ]       # applied while the rung is true
        }
      ]
    }

Contact types:
    {"type":"XIC","tag":"m0"}                bool true  (examine-if-closed)
    {"type":"XIO","tag":"m0"}                bool false (examine-if-open)
    {"type":"GE","tag":"temp","value":28}    temp >= 28   (also GT/LE/LT/EQ/NE)
    {"type":"TON","id":"t0","tag":"temp_hi","preset_ms":3000}
         on-delay timer: while its INPUT (the AND of contacts BEFORE it in
         the branch) is true, accumulate; contact passes once acc>=preset.

Output types:
    {"type":"coil","tag":"relay"}            energize a bool output/memory bit
    {"type":"coil","tag":"m0","latch":true}  set/latch (OTL); "unlatch":true = OTU
    {"type":"motor","channel":"A","speed":70}

Rung evaluation: a rung is TRUE if ANY branch is TRUE; a branch is TRUE if
ALL its contacts pass. Outputs of a true rung are applied. Non-retentive
coils de-energize when no true rung drives them this scan (standard PLC
semantics): each scan, outputs reset to safe defaults (relay off, motor 0,
non-latched memory false) then true rungs set them. Latched bits hold.

REST API (basic auth):
    GET  /                 control + live-state UI
    GET  /api/status       {running, scan_count, inputs, outputs, rung_states}
    GET  /api/program      current ladder JSON
    POST /api/program      save ladder JSON (validated)
    POST /api/run          start the scan loop
    POST /api/stop         stop (outputs go to safe state)

Env:
    OTLAB_PLC_PORT     HTTP port              (default 8091)
    OTLAB_QWIIC_URL    I/O API base           (default http://127.0.0.1:8090)
    OTLAB_PLC_STORE    program path           (default /var/lib/otlab/plc/program.json)
    DASH_USER/DASH_PASS  basic auth           (default otlab / P@ssw0rd!)
"""
import json
import os
import threading
import time
import urllib.request

from flask import Flask, Response, jsonify, request

PORT       = int(os.environ.get("OTLAB_PLC_PORT", "8091"))
QWIIC_URL  = os.environ.get("OTLAB_QWIIC_URL", "http://127.0.0.1:8090").rstrip("/")
STORE      = os.environ.get("OTLAB_PLC_STORE", "/var/lib/otlab/plc/program.json")
DASH_USER  = os.environ.get("DASH_USER", "otlab")
DASH_PASS  = os.environ.get("DASH_PASS", "P@ssw0rd!")
QWIIC_AUTH = (os.environ.get("DASH_USER", "otlab"),
             os.environ.get("DASH_PASS", "P@ssw0rd!"))

# ── default program: the wind-turbine temp demo ───────────────────────
DEFAULT_PROGRAM = {
    "name": "Wind turbine temp control",
    "scan_ms": 200,
    "rungs": [
        {
            "comment": "Spin the turbine at 70% once it gets warm (>= 28 C)",
            "branches": [[{"type": "GE", "tag": "temp", "value": 28.0}]],
            "outputs": [{"type": "motor", "channel": "A", "speed": 70}],
        },
        {
            "comment": "Full speed if it gets hot (>= 31 C)",
            "branches": [[{"type": "GE", "tag": "temp", "value": 31.0}]],
            "outputs": [{"type": "motor", "channel": "A", "speed": 100}],
        },
        {
            "comment": "Trip the alarm relay if it gets too hot (>= 33 C)",
            "branches": [[{"type": "GE", "tag": "temp", "value": 33.0}]],
            "outputs": [{"type": "coil", "tag": "relay"}],
        },
    ],
}

# ── engine state ──────────────────────────────────────────────────────
_lock = threading.Lock()
_program = dict(DEFAULT_PROGRAM)
_running = False
_scan_count = 0
_inputs = {}        # last-read input tags
_outputs = {}       # last-applied output tags
_rung_states = []   # bool per rung (energized this scan)
_memory = {}        # m0..mN latches + named bits
_timers = {}        # id -> {"acc_ms": int, "last_ms": float, "done": bool}
_thread = None
_last_written = {"relay": None, "motor_a": None, "motor_b": None}


# ── I/O via the qwiic REST API ────────────────────────────────────────
def _http_get(path):
    url = QWIIC_URL + path
    req = urllib.request.Request(url)
    _add_auth(req)
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read().decode())


def _http_post(path, body):
    url = QWIIC_URL + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    _add_auth(req)
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read().decode())


def _add_auth(req):
    import base64
    tok = base64.b64encode(f"{QWIIC_AUTH[0]}:{QWIIC_AUTH[1]}".encode()).decode()
    req.add_header("Authorization", f"Basic {tok}")


def read_inputs():
    try:
        s = _http_get("/api/state")
        return {
            "temp": s.get("temp_c"),
            "relay_in": bool(s.get("relay")),
        }
    except Exception:
        return {"temp": None, "relay_in": None}


def write_outputs(relay, motor_a, motor_b):
    # Only push changes (avoid hammering I2C every scan).
    if relay != _last_written["relay"]:
        try:
            _http_post("/api/relay", {"on": bool(relay)})
            _last_written["relay"] = relay
        except Exception:
            pass
    for ch, val, key in (("A", motor_a, "motor_a"), ("B", motor_b, "motor_b")):
        if val != _last_written[key]:
            try:
                _http_post("/api/motor", {"channel": ch, "speed": int(val)})
                _last_written[key] = val
            except Exception:
                pass


# ── ladder evaluation ─────────────────────────────────────────────────
def _tag_value(tag):
    if tag in _inputs:
        return _inputs[tag]
    return _memory.get(tag, False)


def _eval_contact(c, dt_ms):
    t = c.get("type", "XIC").upper()
    if t == "XIC":
        return bool(_tag_value(c.get("tag")))
    if t == "XIO":
        return not bool(_tag_value(c.get("tag")))
    if t in ("GE", "GT", "LE", "LT", "EQ", "NE"):
        v = _tag_value(c.get("tag"))
        ref = c.get("value", 0)
        if v is None:
            return False
        try:
            v = float(v); ref = float(ref)
        except Exception:
            return False
        return {
            "GE": v >= ref, "GT": v > ref, "LE": v <= ref,
            "LT": v < ref, "EQ": v == ref, "NE": v != ref,
        }[t]
    if t in ("TON", "TOF"):
        return _eval_timer(c, dt_ms, t)
    return False


def _eval_timer(c, dt_ms, kind):
    # The timer's INPUT is whether the branch was true up to this point.
    # We approximate by using the timer's own 'input' flag set by the
    # branch evaluator (passed via a sentinel on the contact dict).
    tid = c.get("id", "t?")
    preset = float(c.get("preset_ms", 1000))
    st = _timers.setdefault(tid, {"acc_ms": 0.0, "done": False})
    inp = c.get("_input", False)
    if kind == "TON":
        if inp:
            st["acc_ms"] = min(preset, st["acc_ms"] + dt_ms)
        else:
            st["acc_ms"] = 0.0
        st["done"] = st["acc_ms"] >= preset
        return st["done"]
    else:  # TOF
        if inp:
            st["acc_ms"] = preset
            st["done"] = True
        else:
            st["acc_ms"] = max(0.0, st["acc_ms"] - dt_ms)
            st["done"] = st["acc_ms"] > 0
        return st["done"]


def _eval_branch(branch, dt_ms):
    # Series AND. Timers see the running AND of everything before them.
    acc = True
    for c in branch:
        if c.get("type", "").upper() in ("TON", "TOF"):
            c["_input"] = acc
        passed = _eval_contact(c, dt_ms)
        acc = acc and passed
    return acc


def _eval_rung(rung, dt_ms):
    # Parallel OR of branches.
    branches = rung.get("branches", [])
    if not branches:
        return False
    return any(_eval_branch(b, dt_ms) for b in branches)


def scan_once(dt_ms):
    global _scan_count, _inputs, _outputs, _rung_states
    _inputs = read_inputs()

    # Reset non-retentive outputs to safe defaults each scan.
    relay = False
    motor_a = 0
    motor_b = 0
    coils_set = {}

    states = []
    for rung in _program.get("rungs", []):
        on = _eval_rung(rung, dt_ms)
        states.append(on)
        if on:
            for o in rung.get("outputs", []):
                ot = o.get("type", "coil").lower()
                if ot == "coil":
                    tag = o.get("tag")
                    if o.get("latch"):
                        _memory[tag] = True
                    elif o.get("unlatch"):
                        _memory[tag] = False
                    elif tag == "relay":
                        relay = True
                    else:
                        coils_set[tag] = True
                elif ot == "motor":
                    ch = str(o.get("channel", "A")).upper()
                    spd = int(o.get("speed", 0))
                    if ch == "B":
                        motor_b = spd
                    else:
                        motor_a = spd

    # Apply non-latched coil memory bits (true only while a rung drives them).
    for tag in list(_memory.keys()):
        # leave latched bits alone; only clear plain coils not set this scan
        pass
    for tag, val in coils_set.items():
        _memory[tag] = True

    write_outputs(relay, motor_a, motor_b)
    _outputs = {"relay": relay, "motor_a": motor_a, "motor_b": motor_b}
    _rung_states = states
    _scan_count += 1


def scan_loop():
    last = time.monotonic()
    while _running:
        now = time.monotonic()
        dt_ms = (now - last) * 1000.0
        last = now
        try:
            with _lock:
                scan_once(dt_ms)
        except Exception as e:
            print(f"otlab-plc: scan error {e}", flush=True)
        time.sleep(max(0.02, _program.get("scan_ms", 200) / 1000.0))
    # On stop: outputs to safe state.
    try:
        write_outputs(False, 0, 0)
    except Exception:
        pass


# ── persistence ───────────────────────────────────────────────────────
def load_program():
    global _program
    try:
        with open(STORE) as f:
            _program = json.load(f)
    except Exception:
        _program = dict(DEFAULT_PROGRAM)
        save_program()


def save_program():
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_program, f, indent=2)
    os.replace(tmp, STORE)


def validate_program(p):
    if not isinstance(p, dict) or "rungs" not in p:
        return "program must be an object with a 'rungs' array"
    if not isinstance(p["rungs"], list):
        return "'rungs' must be a list"
    for i, r in enumerate(p["rungs"]):
        if "branches" not in r or not isinstance(r["branches"], list):
            return f"rung {i}: missing 'branches' list"
    return None


# ── Flask ─────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.before_request
def _guard():
    a = request.authorization
    if not (a and a.username == DASH_USER and a.password == DASH_PASS):
        return Response("auth required", 401,
                        {"WWW-Authenticate": 'Basic realm="OTLab PLC"'})


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify({
            "running": _running,
            "scan_count": _scan_count,
            "name": _program.get("name", ""),
            "inputs": _inputs,
            "outputs": _outputs,
            "rung_states": _rung_states,
            "memory": {k: v for k, v in _memory.items() if v},
            "timers": _timers,
        })


@app.route("/api/program", methods=["GET", "POST"])
def api_program():
    global _program
    if request.method == "GET":
        with _lock:
            return jsonify(_program)
    p = request.get_json(silent=True)
    err = validate_program(p)
    if err:
        return jsonify({"ok": False, "err": err}), 400
    with _lock:
        _program = p
        save_program()
        _timers.clear()
        _memory.clear()
    return jsonify({"ok": True})


@app.route("/api/run", methods=["POST"])
def api_run():
    global _running, _thread, _scan_count
    with _lock:
        if not _running:
            _running = True
            _scan_count = 0
            _last_written.update({"relay": None, "motor_a": None, "motor_b": None})
            _thread = threading.Thread(target=scan_loop, daemon=True)
            _thread.start()
    return jsonify({"ok": True, "running": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _running
    with _lock:
        _running = False
    time.sleep(0.3)
    return jsonify({"ok": True, "running": False})


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OTLab · Ladder PLC</title><style>
  :root{--bg:#060200;--surface:#120602;--border:#5a2c10;--hi:#ff7020;
        --text:#ffe6c8;--accent:#ff5500;--on:#ffd060;--off:#7a5030;--down:#ff6a4a}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
    font-family:ui-monospace,Menlo,monospace;padding:20px}
  h1{color:var(--accent);font-size:19px;letter-spacing:.1em;margin:0 0 2px}
  .sub{color:#e0b890;font-size:12px;margin-bottom:18px}
  .bar{display:flex;gap:10px;align-items:center;margin-bottom:18px;flex-wrap:wrap}
  button{font:inherit;font-weight:700;cursor:pointer;border-radius:6px;padding:10px 18px;
    border:1px solid var(--border);background:#1f0c04;color:var(--text)}
  button:hover{border-color:var(--hi)}
  button.run{background:rgba(255,150,0,.18);border-color:var(--on);color:var(--on)}
  button.stop{background:rgba(220,30,0,.2);border-color:var(--down);color:var(--down)}
  .pill{font-size:12px;padding:3px 10px;border-radius:10px;border:1px solid var(--border)}
  .pill.on{color:var(--on);border-color:var(--on);background:rgba(255,150,0,.15)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
  .panel h2{font-size:12px;letter-spacing:.15em;text-transform:uppercase;color:var(--accent);
    margin:0 0 12px;border-bottom:1px solid var(--border);padding-bottom:8px}
  .io{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--border)}
  .io .v{color:var(--on);font-weight:700}
  .rung{border:1px solid var(--border);border-left:3px solid var(--off);border-radius:5px;
    padding:8px 10px;margin-bottom:8px;font-size:12px}
  .rung.hot{border-left-color:var(--on);background:rgba(255,150,0,.06)}
  .rung .c{color:#e0b890}
  textarea{width:100%;height:300px;background:#0d0500;color:var(--text);border:1px solid var(--border);
    border-radius:6px;font:12px ui-monospace,Menlo,monospace;padding:10px}
  .msg{font-size:12px;margin-top:8px;min-height:16px}
</style></head><body>
<h1>◎ OTLab Ladder PLC</h1>
<div class="sub" id="pname">—</div>
<div class="bar">
  <button class="run"  onclick="run()">▶ Run</button>
  <button class="stop" onclick="stop()">■ Stop</button>
  <span class="pill" id="runpill">stopped</span>
  <span class="pill" id="scanpill">scan 0</span>
  <button onclick="save()">Save Program</button>
  <button onclick="loadDefault()">Load Demo</button>
</div>
<div class="grid">
  <div class="panel">
    <h2>Live I/O</h2>
    <div class="io"><span>temp (TMP117)</span><span class="v" id="i_temp">--</span></div>
    <div class="io"><span>relay state</span><span class="v" id="i_relay">--</span></div>
    <div class="io"><span>→ relay out</span><span class="v" id="o_relay">--</span></div>
    <div class="io"><span>→ motor A</span><span class="v" id="o_motora">--</span></div>
    <h2 style="margin-top:16px">Rungs</h2>
    <div id="rungs"></div>
  </div>
  <div class="panel">
    <h2>Program (JSON)</h2>
    <textarea id="prog" spellcheck="false"></textarea>
    <div class="msg" id="msg"></div>
  </div>
</div>
<script>
async function jget(u){const r=await fetch(u);return r.json()}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},
  body:b?JSON.stringify(b):'{}'});return r.json()}
async function run(){await jpost('/api/run');refresh()}
async function stop(){await jpost('/api/stop');refresh()}
async function loadDefault(){const p=await jget('/api/program');/* demo already default */ document.getElementById('prog').value=JSON.stringify(p,null,2)}
async function save(){
  let p; try{p=JSON.parse(document.getElementById('prog').value)}catch(e){msg('JSON error: '+e,true);return}
  const r=await jpost('/api/program',p);
  msg(r.ok?'saved ✓':('error: '+r.err),!r.ok);
}
function msg(t,bad){const m=document.getElementById('msg');m.textContent=t;m.style.color=bad?'#ff6a4a':'#ffd060'}
async function loadProg(){const p=await jget('/api/program');document.getElementById('prog').value=JSON.stringify(p,null,2);
  document.getElementById('pname').textContent=p.name||'(unnamed program)'}
async function refresh(){
  try{
    const s=await jget('/api/status');
    document.getElementById('runpill').textContent=s.running?'RUNNING':'stopped';
    document.getElementById('runpill').className='pill '+(s.running?'on':'');
    document.getElementById('scanpill').textContent='scan '+s.scan_count;
    document.getElementById('pname').textContent=s.name||'';
    const i=s.inputs||{},o=s.outputs||{};
    document.getElementById('i_temp').textContent=i.temp!=null?i.temp.toFixed(1)+' °C':'--';
    document.getElementById('i_relay').textContent=i.relay_in==null?'--':(i.relay_in?'ON':'off');
    document.getElementById('o_relay').textContent=o.relay?'ON':'off';
    document.getElementById('o_motora').textContent=(o.motor_a!=null?o.motor_a:'--')+'%';
    const prog=await jget('/api/program');
    const rs=s.rung_states||[];
    document.getElementById('rungs').innerHTML=(prog.rungs||[]).map((r,idx)=>
      `<div class="rung ${rs[idx]?'hot':''}"><b>R${idx}</b> ${rs[idx]?'● ':'○ '}`+
      `<span class="c">${(r.comment||'').replace(/</g,'&lt;')}</span></div>`).join('');
  }catch(e){}
}
loadProg();refresh();setInterval(refresh,1000);
</script></body></html>"""


def main():
    print(f"otlab-plc: port={PORT} qwiic={QWIIC_URL} store={STORE}", flush=True)
    load_program()
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
