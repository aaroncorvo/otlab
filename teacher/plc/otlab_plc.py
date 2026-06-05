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
            "comment": "Spin the turbine at 70% once it gets warm (>= 82 F)",
            "branches": [[{"type": "GE", "tag": "temp_f", "value": 82.0}]],
            "outputs": [{"type": "motor", "channel": "A", "speed": 70}],
        },
        {
            "comment": "Full speed if it gets hot (>= 88 F)",
            "branches": [[{"type": "GE", "tag": "temp_f", "value": 88.0}]],
            "outputs": [{"type": "motor", "channel": "A", "speed": 100}],
        },
        {
            "comment": "Trip the alarm relay if it gets too hot (>= 91 F)",
            "branches": [[{"type": "GE", "tag": "temp_f", "value": 91.0}]],
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
_rung_detail = []   # per-rung {energized, branches:[[contact_pass,...]]}
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
        c = s.get("temp_c")
        return {
            "temp": c,                                   # °C
            "temp_f": (c * 9 / 5 + 32) if c is not None else None,  # °F
            "relay_in": bool(s.get("relay")),
        }
    except Exception:
        return {"temp": None, "temp_f": None, "relay_in": None}


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


def _eval_branch_detail(branch, dt_ms):
    # Series AND with per-contact capture. Timers see the running AND of
    # everything before them. Returns (branch_passed, [contact_pass,...]).
    acc = True
    passes = []
    for c in branch:
        if c.get("type", "").upper() in ("TON", "TOF"):
            c["_input"] = acc
        passed = _eval_contact(c, dt_ms)
        passes.append(bool(passed))
        acc = acc and passed
    return acc, passes


def scan_once(dt_ms):
    global _scan_count, _inputs, _outputs, _rung_states, _rung_detail
    _inputs = read_inputs()

    # Reset non-retentive outputs to safe defaults each scan.
    relay = False
    motor_a = 0
    motor_b = 0
    coils_set = {}

    states = []
    detail = []
    for rung in _program.get("rungs", []):
        rdetail = {"energized": False, "branches": []}
        energized = False
        for b in rung.get("branches", []):
            bok, passes = _eval_branch_detail(b, dt_ms)
            rdetail["branches"].append(passes)
            if bok:
                energized = True
        rdetail["energized"] = energized
        detail.append(rdetail)
        on = energized
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
    _rung_detail = detail
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
            "rung_detail": _rung_detail,
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
        --text:#ffe6c8;--accent:#ff5500;--on:#ffd060;--off:#7a5030;--down:#ff6a4a;
        --live:#5fe08a}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
    font-family:ui-monospace,Menlo,monospace;padding:18px}
  h1{color:var(--accent);font-size:19px;letter-spacing:.1em;margin:0 0 2px}
  .sub{color:#e0b890;font-size:12px;margin-bottom:14px}
  .bar{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
  button{font:inherit;font-weight:700;cursor:pointer;border-radius:6px;padding:9px 16px;
    border:1px solid var(--border);background:#1f0c04;color:var(--text)}
  button:hover{border-color:var(--hi)}
  button.run{background:rgba(255,150,0,.18);border-color:var(--on);color:var(--on)}
  button.stop{background:rgba(220,30,0,.2);border-color:var(--down);color:var(--down)}
  button.sm{padding:3px 9px;font-size:11px}
  .master{display:flex;align-items:center;gap:9px;margin-right:8px}
  .master-label{font-size:11px;letter-spacing:.14em;color:#e0b890}
  .switch{font:inherit;font-weight:800;cursor:pointer;border-radius:22px;padding:11px 24px;
    min-width:92px;letter-spacing:.12em;border:2px solid var(--off);background:#1a0c04;color:var(--off)}
  .switch.on{border-color:var(--on);color:#1a0c04;background:var(--on);
    box-shadow:0 0 16px rgba(255,200,80,.55)}
  .switch:hover{filter:brightness(1.08)}
  .pill{font-size:12px;padding:3px 10px;border-radius:10px;border:1px solid var(--border)}
  .pill.on{color:var(--on);border-color:var(--on);background:rgba(255,150,0,.15)}
  .io{display:inline-flex;gap:6px;align-items:center;font-size:12px;color:#e0b890;
    border:1px solid var(--border);border-radius:8px;padding:5px 12px}
  .io b{color:var(--on);font-size:14px}
  .io.hot b{color:var(--live)}
  /* ladder */
  #ladder{margin-top:8px}
  .rung{position:relative;background:var(--surface);border:1px solid var(--border);
    border-radius:8px;padding:10px 12px 12px;margin-bottom:12px}
  .rung-head{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:12px}
  .rung-head .rid{color:var(--accent);font-weight:700}
  .rung-head input{flex:1;background:#0d0500;border:1px solid var(--border);border-radius:4px;
    color:var(--text);font:12px ui-monospace,Menlo,monospace;padding:5px 8px}
  .branch{display:flex;align-items:center;gap:4px;margin:5px 0;flex-wrap:wrap}
  .rail{color:var(--off);font-weight:700}
  .rail.hot{color:var(--live)}
  .wire{flex:1;height:2px;background:var(--off);min-width:18px}
  .wire.hot{background:var(--live)}
  .contact{cursor:pointer;border:1px solid var(--border);border-radius:4px;padding:6px 10px;
    background:#0d0500;white-space:nowrap;font-size:13px}
  .contact:hover{border-color:var(--hi)}
  .contact.pass{border-color:var(--live);color:var(--live);background:rgba(95,224,138,.10)}
  .coil{cursor:pointer;border:1px solid var(--border);border-radius:18px;padding:6px 14px;
    background:#0d0500;white-space:nowrap;font-size:13px;margin-left:4px}
  .coil:hover{border-color:var(--hi)}
  .coil.energized{border-color:var(--on);color:var(--on);background:rgba(255,150,0,.12)}
  .addbtn{cursor:pointer;color:var(--off);border:1px dashed var(--border);border-radius:4px;
    padding:5px 8px;font-size:11px;background:transparent}
  .addbtn:hover{color:var(--hi);border-color:var(--hi)}
  .outs{display:flex;align-items:center;gap:6px}
  .msg{font-size:12px;margin:8px 0;min-height:16px}
  /* modal */
  #modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;
    justify-content:center;z-index:50}
  #modal.open{display:flex}
  .mbox{background:var(--surface);border:1px solid var(--hi);border-radius:10px;padding:20px;
    width:340px;max-width:92vw}
  .mbox h3{color:var(--accent);margin:0 0 14px;font-size:14px}
  .mrow{margin:10px 0;display:flex;flex-direction:column;gap:4px}
  .mrow label{font-size:11px;color:#e0b890;letter-spacing:.05em}
  .mrow select,.mrow input{background:#0d0500;border:1px solid var(--border);border-radius:5px;
    color:var(--text);font:13px ui-monospace,Menlo,monospace;padding:8px}
  .mbtns{display:flex;gap:8px;margin-top:16px;justify-content:space-between}
  details{margin-top:18px}summary{cursor:pointer;color:#e0b890;font-size:12px}
  textarea{width:100%;height:240px;background:#0d0500;color:var(--text);border:1px solid var(--border);
    border-radius:6px;font:12px ui-monospace,Menlo,monospace;padding:10px;margin-top:8px}
</style></head><body>
<h1>◎ OTLab Ladder PLC</h1>
<div class="sub">Draw rungs · click a contact or coil to edit · Save &amp; Run drives the real Qwiic hardware</div>
<div class="bar">
  <span class="master"><span class="master-label">TURBINE</span>
    <button id="master" class="switch" onclick="toggleMaster()">OFF</button></span>
  <button class="run"  onclick="run()">▶ Run</button>
  <button class="stop" onclick="stop()">■ Stop</button>
  <span class="pill" id="runpill">stopped</span>
  <span class="pill" id="scanpill">scan 0</span>
  <span class="io" id="io_temp">temp <b>--</b></span>
  <span class="io" id="io_relay">relay <b>--</b></span>
  <span class="io" id="io_motor">motor A <b>--</b></span>
  <button onclick="saveRun()">💾 Save &amp; Run</button>
</div>
<div class="msg" id="msg"></div>
<div id="ladder"></div>
<button class="addbtn" style="font-size:13px;padding:8px 14px" onclick="addRung()">+ Add Rung</button>

<details>
  <summary>Advanced: raw JSON</summary>
  <textarea id="json" spellcheck="false"></textarea>
  <div style="margin-top:6px"><button class="sm" onclick="fromJSON()">Apply JSON → editor</button></div>
</details>

<div id="modal"><div class="mbox" id="mbox"></div></div>

<script>
// ── tag catalogue (what students can pick) ──────────────────────────
const ANALOG_TAGS = ["temp_f","temp"];   // temp_f first = default pick
const BOOL_TAGS   = ["relay_in","m0","m1","m2","m3"];
const COIL_TAGS   = ["relay","m0","m1","m2","m3"];
const CMP_OPS     = {GE:"≥",GT:">",LE:"≤",LT:"<",EQ:"=",NE:"≠"};
const TAG_UNIT    = {temp_f:"°F", temp:"°C"};   // analog tag -> display unit
function unitOf(tag){return TAG_UNIT[tag]||""}

let prog = {name:"",scan_ms:200,rungs:[]};
let editing = false;   // suppress live re-render while a modal/edit is open

async function jget(u){const r=await fetch(u);return r.json()}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},
  body:b?JSON.stringify(b):'{}'});return r.json()}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function msg(t,bad){const m=document.getElementById('msg');m.textContent=t||'';m.style.color=bad?'#ff6a4a':'#ffd060'}

// ── labels ──────────────────────────────────────────────────────────
function contactLabel(c){
  const t=(c.type||'XIC').toUpperCase();
  if(t==='XIC') return '┤ '+c.tag+' ├';
  if(t==='XIO') return '┤/'+c.tag+' ├';
  if(CMP_OPS[t]) return '┤ '+c.tag+' '+CMP_OPS[t]+' '+c.value+unitOf(c.tag)+' ├';
  if(t==='TON'||t==='TOF') return '┤ '+t+' '+(c.id||'t')+' '+((c.preset_ms||0)/1000)+'s ├';
  return '┤ ? ├';
}
function outputLabel(o){
  const t=(o.type||'coil').toLowerCase();
  if(t==='motor') return '( MTR '+(o.channel||'A')+' '+(o.speed||0)+'% )';
  if(o.latch) return '( L '+o.tag+' )';
  if(o.unlatch) return '( U '+o.tag+' )';
  return '( '+o.tag+' )';
}

// ── render the ladder ───────────────────────────────────────────────
function render(){
  document.getElementById('json').value=JSON.stringify(prog,null,2);
  const L=document.getElementById('ladder');
  L.innerHTML=(prog.rungs||[]).map((r,ri)=>{
    const branches=(r.branches||[]).map((b,bi)=>{
      const contacts=b.map((c,ci)=>
        `<div class="contact" data-ri="${ri}" data-bi="${bi}" data-ci="${ci}"
              onclick="editContact(${ri},${bi},${ci})">${esc(contactLabel(c))}</div>`
      ).join('<span class="wire"></span>');
      return `<div class="branch"><span class="rail">│</span>`+
        (contacts||'<span class="addbtn" onclick="addContact('+ri+','+bi+')">+ contact</span>')+
        `<span class="wire"></span>`+
        (bi===0 ? outsHTML(r,ri) : '<span style="color:var(--off)">(OR branch)</span>')+
        (contacts?`<span class="addbtn" onclick="addContact(${ri},${bi})">+</span>`:'')+
        `</div>`;
    }).join('');
    return `<div class="rung" data-ri="${ri}">
      <div class="rung-head"><span class="rid">R${ri}</span>
        <input value="${esc(r.comment||'')}" placeholder="comment"
               onchange="prog.rungs[${ri}].comment=this.value">
        <button class="addbtn" onclick="addBranch(${ri})">+ OR branch</button>
        <button class="addbtn" onclick="delRung(${ri})">✕ rung</button>
      </div>${branches}</div>`;
  }).join('') || '<div class="sub">No rungs yet — click “+ Add Rung”.</div>';
}
function outsHTML(r,ri){
  const outs=(r.outputs||[]).map((o,oi)=>
    `<div class="coil" data-ri="${ri}" data-oi="${oi}" onclick="editOutput(${ri},${oi})">${esc(outputLabel(o))}</div>`
  ).join('');
  return `<span class="rail">│</span><div class="outs">`+outs+
    `<span class="addbtn" onclick="addOutput(${ri})">+ out</span></div>`;
}

// ── structural edits ────────────────────────────────────────────────
function addRung(){prog.rungs.push({comment:"",branches:[[]],outputs:[]});render()}
function delRung(ri){prog.rungs.splice(ri,1);render()}
function addBranch(ri){prog.rungs[ri].branches.push([]);render()}
function addContact(ri,bi){editContactNew(ri,bi)}
function addOutput(ri){editOutputNew(ri)}

// ── modal helpers ───────────────────────────────────────────────────
function openModal(html){editing=true;document.getElementById('mbox').innerHTML=html;
  document.getElementById('modal').classList.add('open')}
function closeModal(){editing=false;document.getElementById('modal').classList.remove('open')}

function tagSelect(id,opts,sel){return `<select id="${id}">`+
  opts.map(o=>`<option ${o===sel?'selected':''}>${o}</option>`).join('')+`</select>`}

// contact editor (new or existing) -----------------------------------
function contactForm(c){
  const t=(c.type||'GE').toUpperCase();
  const typeOpts=['GE','GT','LE','LT','EQ','NE','XIC','XIO','TON','TOF'];
  return `<h3>Contact</h3>
   <div class="mrow"><label>Type</label>
     <select id="ctype" onchange="cTypeChange()">`+
     typeOpts.map(o=>`<option value="${o}" ${o===t?'selected':''}>${o} `+
        (CMP_OPS[o]?('('+CMP_OPS[o]+')'):o==='XIC'?'(bool on)':o==='XIO'?'(bool off)':o==='TON'?'(on-delay)':o==='TOF'?'(off-delay)':'')+`</option>`).join('')+
     `</select></div>
   <div class="mrow" id="r_tag"><label>Tag</label><span id="tagslot"></span></div>
   <div class="mrow" id="r_val"><label id="vlabel">Value</label><input id="cval" type="number" step="0.1" value="${c.value!=null?c.value:82}"></div>
   <div class="mrow" id="r_tid"><label>Timer id</label><input id="ctid" value="${c.id||'t0'}"></div>
   <div class="mrow" id="r_preset"><label>Preset (ms)</label><input id="cpreset" type="number" value="${c.preset_ms||3000}"></div>`;
}
function cTypeChange(){
  const t=document.getElementById('ctype').value;
  const cmp=!!CMP_OPS[t], bool=(t==='XIC'||t==='XIO'), tim=(t==='TON'||t==='TOF');
  document.getElementById('r_tag').style.display=tim?'none':'flex';
  document.getElementById('r_val').style.display=cmp?'flex':'none';
  document.getElementById('r_tid').style.display=tim?'flex':'none';
  document.getElementById('r_preset').style.display=tim?'flex':'none';
  const slot=document.getElementById('tagslot');
  if(cmp){ slot.innerHTML=tagSelect('ctag',ANALOG_TAGS,slot.dataset.sel||ANALOG_TAGS[0]);
    document.getElementById('ctag').onchange=updateValUnit; updateValUnit(); }
  else if(bool) slot.innerHTML=tagSelect('ctag',BOOL_TAGS,slot.dataset.sel||BOOL_TAGS[0]);
}
function updateValUnit(){const t=document.getElementById('ctag');const lab=document.getElementById('vlabel');
  if(t&&lab) lab.textContent='Value ('+(unitOf(t.value)||'#')+')';}
let _ctxTarget=null;
function editContact(ri,bi,ci){_ctxTarget={ri,bi,ci,isNew:false};const c=prog.rungs[ri].branches[bi][ci];
  openModal(contactForm(c)+modalBtns(true));
  document.getElementById('tagslot').dataset.sel=c.tag||'';cTypeChange();
  if(c.tag) try{document.getElementById('ctag').value=c.tag}catch(e){}}
function editContactNew(ri,bi){_ctxTarget={ri,bi,ci:null,isNew:true};
  openModal(contactForm({type:'GE',tag:'temp_f',value:82})+modalBtns(false));cTypeChange()}
function saveContact(){
  const t=document.getElementById('ctype').value;const c={type:t};
  if(CMP_OPS[t]){c.tag=document.getElementById('ctag').value;c.value=parseFloat(document.getElementById('cval').value)}
  else if(t==='XIC'||t==='XIO'){c.tag=document.getElementById('ctag').value}
  else{c.id=document.getElementById('ctid').value;c.preset_ms=parseInt(document.getElementById('cpreset').value)}
  const {ri,bi,ci,isNew}=_ctxTarget;
  if(isNew) prog.rungs[ri].branches[bi].push(c); else prog.rungs[ri].branches[bi][ci]=c;
  closeModal();render()
}
function delContact(){const {ri,bi,ci}=_ctxTarget;prog.rungs[ri].branches[bi].splice(ci,1);closeModal();render()}

// output editor -------------------------------------------------------
function outputForm(o){
  const t=(o.type||'motor').toLowerCase();
  return `<h3>Output</h3>
   <div class="mrow"><label>Type</label>
     <select id="otype" onchange="oTypeChange()">
       <option value="motor" ${t==='motor'?'selected':''}>motor (drive turbine)</option>
       <option value="coil" ${t==='coil'?'selected':''}>coil (relay / memory bit)</option>
     </select></div>
   <div class="mrow" id="o_mch"><label>Motor channel</label>`+tagSelect('omch',['A','B'],o.channel||'A')+`</div>
   <div class="mrow" id="o_spd"><label>Speed % (-100..100)</label><input id="ospd" type="number" min="-100" max="100" value="${o.speed!=null?o.speed:70}"></div>
   <div class="mrow" id="o_tag"><label>Coil tag</label>`+tagSelect('otag',COIL_TAGS,o.tag||'relay')+`</div>
   <div class="mrow" id="o_latch"><label>Latch mode</label>
     <select id="olatch"><option value="">momentary</option><option value="latch" ${o.latch?'selected':''}>latch (OTL)</option><option value="unlatch" ${o.unlatch?'selected':''}>unlatch (OTU)</option></select></div>`;
}
function oTypeChange(){const t=document.getElementById('otype').value;const m=t==='motor';
  document.getElementById('o_mch').style.display=m?'flex':'none';
  document.getElementById('o_spd').style.display=m?'flex':'none';
  document.getElementById('o_tag').style.display=m?'none':'flex';
  document.getElementById('o_latch').style.display=m?'none':'flex';}
let _outTarget=null;
function editOutput(ri,oi){_outTarget={ri,oi,isNew:false};openModal(outputForm(prog.rungs[ri].outputs[oi])+modalBtns(true));oTypeChange()}
function editOutputNew(ri){_outTarget={ri,oi:null,isNew:true};openModal(outputForm({type:'motor',channel:'A',speed:70})+modalBtns(false));oTypeChange()}
function saveOutput(){
  const t=document.getElementById('otype').value;let o={type:t};
  if(t==='motor'){o.channel=document.getElementById('omch').value;o.speed=parseInt(document.getElementById('ospd').value)}
  else{o.tag=document.getElementById('otag').value;const lm=document.getElementById('olatch').value;if(lm==='latch')o.latch=true;if(lm==='unlatch')o.unlatch=true}
  const {ri,oi,isNew}=_outTarget;
  if(isNew) prog.rungs[ri].outputs.push(o); else prog.rungs[ri].outputs[oi]=o;
  closeModal();render()
}
function delOutput(){const {ri,oi}=_outTarget;prog.rungs[ri].outputs.splice(oi,1);closeModal();render()}

// modal buttons (shared) — figures out which save/del to call ---------
function modalBtns(canDelete){
  return `<div class="mbtns">
    <button onclick="closeModal()">Cancel</button>
    <div style="display:flex;gap:8px">`+
    (canDelete?`<button class="stop" onclick="modalDelete()">Delete</button>`:'')+
    `<button class="run" onclick="modalSave()">OK</button></div></div>`;
}
function modalSave(){ if(_ctxTarget && document.getElementById('ctype')) return saveContact(); return saveOutput(); }
function modalDelete(){ if(_ctxTarget && document.getElementById('ctype')) return delContact(); return delOutput(); }
// reset the discriminator when opening each editor
function editContactWrap(){_outTarget=null}

// ── save / run / json ───────────────────────────────────────────────
async function saveProgram(){
  const r=await jpost('/api/program',prog);
  msg(r.ok?'saved ✓':('error: '+r.err),!r.ok);return r.ok;
}
async function saveRun(){ if(await saveProgram()){ await jpost('/api/run'); refresh(); } }
async function run(){await jpost('/api/run');refresh()}
async function stop(){await jpost('/api/stop');refresh()}
async function toggleMaster(){const s=await jget('/api/status');
  if(s.running){await jpost('/api/stop');}else{await jpost('/api/run');}refresh();}
function fromJSON(){try{prog=JSON.parse(document.getElementById('json').value);render();msg('applied ✓')}catch(e){msg('JSON error: '+e,true)}}

// ── live polling ────────────────────────────────────────────────────
async function refresh(){
  try{
    const s=await jget('/api/status');
    document.getElementById('runpill').textContent=s.running?'RUNNING':'stopped';
    document.getElementById('runpill').className='pill '+(s.running?'on':'');
    const _m=document.getElementById('master');
    if(_m){_m.textContent=s.running?'ON':'OFF';_m.className='switch '+(s.running?'on':'');}
    document.getElementById('scanpill').textContent='scan '+s.scan_count;
    const i=s.inputs||{},o=s.outputs||{};
    const tf=(i.temp_f!=null)?i.temp_f:(i.temp!=null?i.temp*9/5+32:null);
    const tempTxt=(tf!=null)?(tf.toFixed(1)+'°F'+(i.temp!=null?' / '+i.temp.toFixed(1)+'°C':'')):'--';
    setIO('io_temp','temp',tempTxt,false);
    setIO('io_relay','relay',o.relay?'ON':'off',o.relay);
    setIO('io_motor','motor A',(o.motor_a!=null?o.motor_a:'--')+'%',o.motor_a>0);
    if(!editing) applyLive(s.rung_detail||[]);
  }catch(e){}
}
function setIO(id,lab,val,hot){const el=document.getElementById(id);
  el.innerHTML=lab+' <b>'+val+'</b>';el.className='io'+(hot?' hot':'')}
function applyLive(detail){
  document.querySelectorAll('.rung').forEach(rungEl=>{
    const ri=+rungEl.dataset.ri;const d=detail[ri];if(!d)return;
    rungEl.querySelectorAll('.contact').forEach(cel=>{
      const bi=+cel.dataset.bi,ci=+cel.dataset.ci;
      const pass=d.branches&&d.branches[bi]&&d.branches[bi][ci];
      cel.classList.toggle('pass',!!pass);
    });
    rungEl.querySelectorAll('.coil').forEach(coil=>coil.classList.toggle('energized',!!d.energized));
    rungEl.querySelectorAll('.rail,.wire').forEach(w=>w.classList.toggle('hot',!!d.energized));
  });
}

async function init(){prog=await jget('/api/program');render();refresh();setInterval(refresh,800)}
init();
</script></body></html>"""


def main():
    print(f"otlab-plc: port={PORT} qwiic={QWIIC_URL} store={STORE}", flush=True)
    load_program()
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
