#!/usr/bin/env python3
"""otlab-qwiic — physical I/O control surface for the teacher Pi.

Drives the SparkFun Qwiic devices plugged into the Cruiser carrier's
Qwiic port that lands on I2C bus 1:

    0x18  Qwiic Single Relay        (output — on/off)
    0x48  TMP117 high-precision temp (input  — °C)
    0x5d  Qwiic Motor Driver (SCMD)  (output — 2x motor, drives the
                                      wind-turbine demo)

Serves a small control page + REST API on :8090. The LCD is on the OTHER
Qwiic port (bus 2) and is driven separately by otlab-lcd.service.

Device protocols (from SparkFun libraries):
  Relay 0x18 : write byte 0x01 (on) / 0x00 (off); read reg 0x05 for state
  TMP117 0x48: temp register 0x00, signed 16-bit, 1 LSB = 0.0078125 °C
  SCMD 0x5d  : ID reg 0x01 == 0xA9; enable reg 0x70 = 0x01;
               Motor A drive reg 0x20, Motor B reg 0x21;
               drive value 128 = stop, 255 = full fwd, 0 = full reverse

Env:
  OTLAB_QWIIC_BUS    I2C bus number       (default 1)
  OTLAB_QWIIC_PORT   HTTP listen port     (default 8090)
  QWIIC_RELAY_ADDR   relay address        (default 0x18)
  QWIIC_TMP117_ADDR  TMP117 address       (default 0x48)
  QWIIC_MOTOR_ADDR   motor driver address (default 0x5d)
  DASH_USER/DASH_PASS  HTTP basic auth    (default otlab / P@ssw0rd!)
"""
import os
import threading

from flask import Flask, jsonify, request, Response
from smbus2 import SMBus, i2c_msg

BUS_NUM     = int(os.environ.get("OTLAB_QWIIC_BUS", "1"))
PORT        = int(os.environ.get("OTLAB_QWIIC_PORT", "8090"))
RELAY_ADDR  = int(os.environ.get("QWIIC_RELAY_ADDR", "0x18"), 0)
TMP117_ADDR = int(os.environ.get("QWIIC_TMP117_ADDR", "0x48"), 0)
MOTOR_ADDR  = int(os.environ.get("QWIIC_MOTOR_ADDR", "0x5d"), 0)
DASH_USER   = os.environ.get("DASH_USER", "otlab")
DASH_PASS   = os.environ.get("DASH_PASS", "P@ssw0rd!")

# SCMD registers
SCMD_ID            = 0x01
SCMD_ID_WORD       = 0xA9
SCMD_MA_DRIVE      = 0x20
SCMD_MB_DRIVE      = 0x21
SCMD_DRIVER_ENABLE = 0x70
SCMD_STATUS_1      = 0x77
SCMD_ENUM_BIT      = 0x01

_lock = threading.Lock()
_bus = None
_motor_enabled = False
# Last-commanded values (the SCMD drive regs aren't reliably readable
# back, so we track what we set).
_state = {"motor_a": 0, "motor_b": 0}


def _open():
    global _bus
    if _bus is None:
        _bus = SMBus(BUS_NUM)
    return _bus


def _reset_bus():
    global _bus, _motor_enabled
    try:
        if _bus is not None:
            _bus.close()
    except Exception:
        pass
    _bus = None
    _motor_enabled = False


# ── TMP117 ────────────────────────────────────────────────────────────
def read_temp():
    with _lock:
        try:
            b = _open()
            d = b.read_i2c_block_data(TMP117_ADDR, 0x00, 2)
            raw = (d[0] << 8) | d[1]
            if raw >= 32768:
                raw -= 65536
            return raw * 0.0078125
        except Exception:
            _reset_bus()
            return None


# ── Relay ─────────────────────────────────────────────────────────────
def relay_set(on):
    with _lock:
        try:
            b = _open()
            b.write_byte(RELAY_ADDR, 0x01 if on else 0x00)
            return True
        except Exception:
            _reset_bus()
            return False


def relay_get():
    with _lock:
        try:
            b = _open()
            v = b.read_byte_data(RELAY_ADDR, 0x05)
            return bool(v)
        except Exception:
            _reset_bus()
            return None


# ── Motor (SCMD) ──────────────────────────────────────────────────────
def _motor_enable(b):
    global _motor_enabled
    if not _motor_enabled:
        b.write_byte_data(MOTOR_ADDR, SCMD_DRIVER_ENABLE, 0x01)
        _motor_enabled = True


def speed_to_drive(speed):
    """speed -100..100 (%)  ->  SCMD drive byte 0..255 (128 = stop)."""
    speed = max(-100, min(100, int(speed)))
    return max(0, min(255, 128 + round(speed * 127 / 100)))


def motor_set(channel, speed):
    reg = SCMD_MA_DRIVE if channel.upper() == "A" else SCMD_MB_DRIVE
    drive = speed_to_drive(speed)
    with _lock:
        try:
            b = _open()
            _motor_enable(b)
            b.write_byte_data(MOTOR_ADDR, reg, drive)
            _state["motor_a" if channel.upper() == "A" else "motor_b"] = int(speed)
            return True
        except Exception:
            _reset_bus()
            return False


def motor_stop_all():
    ok_a = motor_set("A", 0)
    ok_b = motor_set("B", 0)
    return ok_a and ok_b


def motor_ready():
    with _lock:
        try:
            b = _open()
            sid = b.read_byte_data(MOTOR_ADDR, SCMD_ID)
            st = b.read_byte_data(MOTOR_ADDR, SCMD_STATUS_1)
            return (sid == SCMD_ID_WORD) and bool(st & SCMD_ENUM_BIT) and st != 0xFF
        except Exception:
            _reset_bus()
            return None


# ── Flask app ─────────────────────────────────────────────────────────
app = Flask(__name__)


def _auth_ok(req):
    a = req.authorization
    return a and a.username == DASH_USER and a.password == DASH_PASS


def _need_auth():
    return Response(
        "auth required", 401,
        {"WWW-Authenticate": 'Basic realm="OTLab Qwiic I/O"'},
    )


@app.before_request
def _guard():
    if not _auth_ok(request):
        return _need_auth()


@app.route("/api/state")
def api_state():
    c = read_temp()
    return jsonify({
        "temp_c":  round(c, 2) if c is not None else None,
        "temp_f":  round(c * 9 / 5 + 32, 1) if c is not None else None,
        "relay":   relay_get(),
        "motor_a": _state["motor_a"],
        "motor_b": _state["motor_b"],
        "motor_ready": motor_ready(),
    })


@app.route("/api/relay", methods=["POST"])
def api_relay():
    d = request.get_json(silent=True) or {}
    ok = relay_set(bool(d.get("on")))
    return jsonify({"ok": ok, "relay": relay_get()})


@app.route("/api/motor", methods=["POST"])
def api_motor():
    d = request.get_json(silent=True) or {}
    ch = str(d.get("channel", "A"))
    speed = d.get("speed", 0)
    ok = motor_set(ch, speed)
    return jsonify({"ok": ok, "channel": ch.upper(), "speed": int(speed)})


@app.route("/api/motor/stop", methods=["POST"])
def api_motor_stop():
    return jsonify({"ok": motor_stop_all()})


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


# Control page — self-contained, ember theme.
PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OTLab · Physical I/O</title>
<style>
  :root{--bg:#060200;--surface:#120602;--border:#5a2c10;--border-hi:#ff7020;
        --text:#ffe6c8;--accent:#ff5500;--up:#ffd060;--down:#ff6a4a;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:ui-monospace,"SF Mono",Menlo,monospace;padding:24px;}
  h1{color:var(--accent);font-size:20px;letter-spacing:.1em;margin:0 0 4px}
  .sub{color:#e0b890;font-size:12px;margin-bottom:24px}
  .panel{background:var(--surface);border:1px solid var(--border);
         border-radius:8px;padding:20px;margin-bottom:18px;max-width:560px}
  .panel h2{font-size:13px;letter-spacing:.15em;text-transform:uppercase;
            color:var(--accent);margin:0 0 14px;border-bottom:1px solid var(--border);
            padding-bottom:8px}
  .temp{font-size:44px;font-weight:700;color:var(--up)}
  .temp .f{font-size:20px;color:#e0b890;margin-left:10px}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:10px 0}
  button{font-family:inherit;font-size:14px;font-weight:700;cursor:pointer;
         border-radius:6px;padding:12px 20px;border:1px solid var(--border);
         background:#1f0c04;color:var(--text);transition:.12s}
  button:hover{border-color:var(--border-hi);transform:translateY(-1px)}
  button.on{background:rgba(255,150,0,.18);border-color:var(--up);color:var(--up)}
  button.off{background:rgba(220,30,0,.18);border-color:var(--down);color:var(--down)}
  button.stop{background:rgba(220,30,0,.25);border-color:var(--down);color:var(--down)}
  .pill{font-size:12px;padding:3px 10px;border-radius:10px;border:1px solid var(--border)}
  .pill.on{color:var(--up);border-color:var(--up);background:rgba(255,150,0,.15)}
  .pill.off{color:#e0b890}
  input[type=range]{flex:1;min-width:200px;accent-color:var(--accent)}
  .spd{font-size:24px;font-weight:700;color:var(--up);min-width:70px;text-align:right}
  label{font-size:12px;color:#e0b890;letter-spacing:.05em}
</style></head><body>
<h1>◉ OTLab Physical I/O</h1>
<div class="sub">Live Qwiic hardware on the teacher Pi · I2C bus 1</div>

<div class="panel">
  <h2>TMP117 Temperature</h2>
  <div class="temp"><span id="tc">--</span>°C<span class="f" id="tf">--°F</span></div>
</div>

<div class="panel">
  <h2>Qwiic Relay <span class="pill off" id="relayPill">unknown</span></h2>
  <div class="row">
    <button class="on"  onclick="relay(true)">Relay ON</button>
    <button class="off" onclick="relay(false)">Relay OFF</button>
  </div>
</div>

<div class="panel">
  <h2>Wind Turbine Motor <span class="pill off" id="motorPill">--</span></h2>
  <div class="row">
    <label>Speed</label>
    <input type="range" id="spd" min="-100" max="100" value="0" step="5"
           oninput="document.getElementById('spdv').textContent=this.value+'%'">
    <span class="spd" id="spdv">0%</span>
  </div>
  <div class="row">
    <button onclick="setMotor(60)">Spin ▶</button>
    <button onclick="setMotor(100)">Full ⏩</button>
    <button onclick="setMotor(-60)">Reverse ◀</button>
    <button class="stop" onclick="stopMotor()">STOP ■</button>
    <button onclick="applySlider()">Apply Slider</button>
  </div>
</div>

<script>
async function jget(u){const r=await fetch(u);return r.json()}
async function jpost(u,b){const r=await fetch(u,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json()}
async function relay(on){await jpost('/api/relay',{on});refresh()}
async function setMotor(s){document.getElementById('spd').value=s;
  document.getElementById('spdv').textContent=s+'%';
  await jpost('/api/motor',{channel:'A',speed:s});refresh()}
async function applySlider(){await setMotor(parseInt(document.getElementById('spd').value))}
async function stopMotor(){await jpost('/api/motor/stop',{});
  document.getElementById('spd').value=0;document.getElementById('spdv').textContent='0%';refresh()}
async function refresh(){
  try{
    const s=await jget('/api/state');
    document.getElementById('tc').textContent=s.temp_c!=null?s.temp_c.toFixed(1):'--';
    document.getElementById('tf').textContent=s.temp_f!=null?s.temp_f.toFixed(1)+'°F':'--';
    const rp=document.getElementById('relayPill');
    if(s.relay===true){rp.textContent='ON';rp.className='pill on'}
    else if(s.relay===false){rp.textContent='OFF';rp.className='pill off'}
    else{rp.textContent='n/a';rp.className='pill off'}
    const mp=document.getElementById('motorPill');
    mp.textContent=(s.motor_ready?'ready':'no driver')+' · A='+s.motor_a+'%';
    mp.className='pill '+(s.motor_ready?'on':'off');
  }catch(e){}
}
refresh();setInterval(refresh,2000);
</script>
</body></html>"""


def main():
    print(f"otlab-qwiic: bus={BUS_NUM} port={PORT} "
          f"relay=0x{RELAY_ADDR:02x} tmp117=0x{TMP117_ADDR:02x} "
          f"motor=0x{MOTOR_ADDR:02x}", flush=True)
    # Safe defaults: motor stopped, relay left as-is.
    motor_stop_all()
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
