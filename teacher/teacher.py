"""Classroom teacher dashboard.

Discovers Raspberry Pi student hosts by scanning a configurable IP range,
polls them via SSH for health metrics, and serves a drag-and-drop canvas
where the teacher arranges Pi cards to match the physical room layout.

Env vars (all have defaults, all overridable at runtime):
  SCAN_BASE        IP prefix for the classroom subnet  (default "192.168.10")
  SCAN_START       First host octet to scan            (default 100)
  SCAN_END         Last host octet to scan             (default 150)
  SSH_USER         SSH username shared by all Pis      (default "pi")
  SSH_PASS         SSH password shared by all Pis      (default "raspberry")
  PROBE_INTERVAL   Seconds between discovery sweeps    (default 30)
  HEALTH_INTERVAL  Seconds between per-host polls      (default 15)
  LISTEN_PORT      HTTP listen port                    (default 8080)
  DATA_DIR         Persistent state directory          (default /var/lib/teacher)
  MAX_HOSTS        Auto-lock when N hosts found; 0=off (default 0)
"""

import http.client
import json
import os
import ssl
import subprocess
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import paramiko
from flask import Flask, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Scan range — mutable so the teacher can update them at runtime via the UI.
# Env vars are the initial defaults; state.json overrides them on load.
_scan_base  = os.environ.get('SCAN_BASE',  '192.168.10')
_scan_start = int(os.environ.get('SCAN_START', '100'))
_scan_end   = int(os.environ.get('SCAN_END',   '150'))
SSH_USER        = os.environ.get('SSH_USER',        'pi')
SSH_PASS        = os.environ.get('SSH_PASS',        'raspberry')
PROBE_INTERVAL  = float(os.environ.get('PROBE_INTERVAL',  '30'))
HEALTH_INTERVAL = float(os.environ.get('HEALTH_INTERVAL', '15'))
LISTEN_PORT     = int(os.environ.get('LISTEN_PORT', '8080'))
DATA_DIR        = Path(os.environ.get('DATA_DIR',
                       str(Path.home() / '.local' / 'share' / 'teacher')))
MAX_HOSTS       = int(os.environ.get('MAX_HOSTS',   '0'))

STATE_FILE          = DATA_DIR / 'state.json'
SSH_CONNECT_TIMEOUT = 5
SSH_CMD_TIMEOUT     = 15

# ---------------------------------------------------------------------------
# Health script — runs on each Pi via SSH. Two /proc/stat samples give
# an accurate instantaneous CPU %. Hostname, mem, temp, disk, uptime, load
# are all single-pass reads from /proc and sysfs.
# ---------------------------------------------------------------------------
HEALTH_SCRIPT = r'''
read_cpu() { awk '/^cpu / {idle=$5; total=0; for(i=2;i<=NF;i++) total+=$i; print idle, total}' /proc/stat; }
read a b < <(read_cpu); sleep 0.2; read c d < <(read_cpu)
cpu=$(awk -v a=$a -v b=$b -v c=$c -v d=$d 'BEGIN{x=d-b; if(x>0) printf "%.1f",100*(1-(c-a)/x); else print "0.0"}')
mem=$(awk '/^MemTotal:/{t=$2}/^MemAvailable:/{a=$2}END{printf "%.1f",100*(t-a)/t}' /proc/meminfo)
temp=$(vcgencmd measure_temp 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
[ -z "$temp" ] && temp=0
disk=$(df -P / | awk 'NR==2{gsub("%","",$5);print $5}')
up=$(awk '{print int($1)}' /proc/uptime)
load=$(awk '{print $1}' /proc/loadavg)
hname=$(hostname)
python3 -c "
import json
print(json.dumps({
  'cpu':      float('$cpu'   or 0),
  'mem':      float('$mem'   or 0),
  'temp':     float('$temp'  or 0),
  'disk':     int(  '$disk'  or 0),
  'uptime':   int(  '$up'    or 0),
  'load1':    float('$load'  or 0),
  'hostname': '$hname',
}))
"
'''

# ---------------------------------------------------------------------------
# In-process state
# ---------------------------------------------------------------------------
_lock     = threading.Lock()
_roster   = {}   # ip -> {hostname, label, status, health, last_seen, added}
_layout   = {}   # ip -> {x, y}
_locked   = False
_scan_now = threading.Event()

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Persistence — atomic write via temp file rename.
# ---------------------------------------------------------------------------
def _load_state():
    global _roster, _layout, _locked, _scan_base, _scan_start, _scan_end
    try:
        data = json.loads(STATE_FILE.read_text())
        with _lock:
            _roster = data.get('roster', {})
            _layout = data.get('layout', {})
            _locked = data.get('locked', False)
        scan = data.get('scan', {})
        if scan.get('base'):  _scan_base  = scan['base']
        if scan.get('start'): _scan_start = int(scan['start'])
        if scan.get('end'):   _scan_end   = int(scan['end'])
        print(f'[state] loaded {len(_roster)} hosts (locked={_locked})', flush=True)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f'[state] load error: {e}', flush=True)


def _save_state():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = {
            'roster': dict(_roster),
            'layout': dict(_layout),
            'locked': _locked,
            'scan':   {'base': _scan_base, 'start': _scan_start, 'end': _scan_end},
        }
    tmp = STATE_FILE.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(STATE_FILE)
    except Exception as e:
        print(f'[state] save error: {e}', flush=True)


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------
def _connect(ip):
    """Return a connected paramiko SSHClient or None on any failure."""
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(
            ip,
            username=SSH_USER,
            password=SSH_PASS,
            timeout=SSH_CONNECT_TIMEOUT,
            banner_timeout=SSH_CONNECT_TIMEOUT,
            auth_timeout=SSH_CONNECT_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
        )
        return c
    except Exception:
        return None


def _ssh_health(ip):
    """SSH into ip, run HEALTH_SCRIPT, return parsed dict or None."""
    client = _connect(ip)
    if not client:
        return None
    try:
        stdin, stdout, _ = client.exec_command('bash -s', timeout=SSH_CMD_TIMEOUT)
        stdin.write(HEALTH_SCRIPT)
        stdin.channel.shutdown_write()
        raw = stdout.read().decode(errors='ignore').strip()
        # Script may print warnings before the JSON line; take the last parseable line.
        for line in reversed(raw.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None
    except Exception:
        return None
    finally:
        client.close()


def _ping(ip):
    try:
        r = subprocess.run(
            ['ping', '-c', '1', '-W', '1', ip],
            capture_output=True, timeout=2,
        )
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Default canvas position for newly discovered hosts — fills left-to-right,
# top-to-bottom in a loose grid.
# ---------------------------------------------------------------------------
def _default_pos(idx):
    cols = 4
    return {
        'x': 20 + (idx % cols) * 210,
        'y': 20 + (idx // cols) * 255,
    }


# ---------------------------------------------------------------------------
# Background thread — discovery + health polling.
# ---------------------------------------------------------------------------
def _background():
    global _locked
    last_scan   = 0.0
    last_health = 0.0

    while True:
        now = time.time()

        # --- Discovery: ping sweep then SSH to responders -----------------
        do_scan = _scan_now.is_set() or (not _locked and (now - last_scan) >= PROBE_INTERVAL)
        if do_scan:
            _scan_now.clear()
            last_scan = now
            try:
                with _lock:
                    known = set(_roster)

                candidates = [
                    f'{_scan_base}.{i}'
                    for i in range(_scan_start, _scan_end + 1)
                    if f'{_scan_base}.{i}' not in known
                ]

                # Parallel ping — fast, ~1 s for a /24 slice
                responding = []
                with ThreadPoolExecutor(max_workers=64) as tp:
                    futures = {tp.submit(_ping, ip): ip for ip in candidates}
                    for f in as_completed(futures):
                        if f.result():
                            responding.append(futures[f])

                if responding:
                    print(f'[scan] {len(responding)} new IPs responded, SSHing…', flush=True)

                # SSH to responders to confirm they're Pis and grab first health snapshot
                with ThreadPoolExecutor(max_workers=10) as tp:
                    futures = {tp.submit(_ssh_health, ip): ip for ip in responding}
                    for f in as_completed(futures):
                        ip = futures[f]
                        h  = f.result()
                        if h:
                            with _lock:
                                if ip not in _roster:
                                    idx = len(_layout)
                                    _roster[ip] = {
                                        'hostname': h.get('hostname', ip),
                                        'label':    '',
                                        'status':   'online',
                                        'health':   h,
                                        'last_seen': datetime.now().isoformat(timespec='seconds'),
                                        'added':    datetime.now().isoformat(timespec='seconds'),
                                    }
                                    if ip not in _layout:
                                        _layout[ip] = _default_pos(idx)
                                    print(f'[+] {ip}  hostname={h.get("hostname", ip)}', flush=True)
                _save_state()

                # Auto-lock once MAX_HOSTS threshold is reached
                if MAX_HOSTS > 0:
                    with _lock:
                        count = len(_roster)
                        if count >= MAX_HOSTS and not _locked:
                            _locked = True
                            print(f'[lock] auto-locked at {count} hosts', flush=True)
                    _save_state()

            except Exception as e:
                print(f'[scan] error: {type(e).__name__}: {e}', flush=True)

        # --- Health poll: re-check every known host -----------------------
        if (now - last_health) >= HEALTH_INTERVAL:
            last_health = now
            try:
                with _lock:
                    ips = list(_roster)
                if not ips:
                    time.sleep(2)
                    continue
                with ThreadPoolExecutor(max_workers=min(len(ips), 20)) as tp:
                    futures = {tp.submit(_ssh_health, ip): ip for ip in ips}
                    for f in as_completed(futures):
                        ip = futures[f]
                        h  = f.result()
                        with _lock:
                            if ip not in _roster:
                                continue
                            if h:
                                _roster[ip]['status']    = 'online'
                                _roster[ip]['health']    = h
                                _roster[ip]['last_seen'] = datetime.now().isoformat(timespec='seconds')
                                # Update hostname if it was still defaulting to the IP
                                if _roster[ip].get('hostname') in ('', ip):
                                    _roster[ip]['hostname'] = h.get('hostname', ip)
                            else:
                                _roster[ip]['status'] = 'offline'
            except Exception as e:
                print(f'[health] error: {type(e).__name__}: {e}', flush=True)

        time.sleep(2)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def api_status():
    with _lock:
        return jsonify({
            'roster':  {ip: dict(v) for ip, v in _roster.items()},
            'locked':  _locked,
            'layout':  dict(_layout),
            'updated': datetime.now().isoformat(timespec='seconds'),
            'config':  {
                'base':  _scan_base,
                'start': _scan_start,
                'end':   _scan_end,
            },
        })


@app.route('/api/scan-range', methods=['POST'])
def api_scan_range():
    """Update the IP scan range at runtime."""
    global _scan_base, _scan_start, _scan_end
    d     = request.get_json(silent=True) or {}
    base  = d.get('base', '').strip()
    start = d.get('start')
    end   = d.get('end')
    if not base or start is None or end is None:
        return jsonify({'ok': False, 'err': 'Provide base, start, and end'}), 400
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'err': 'start and end must be integers'}), 400
    if not (0 <= start <= 254 and 0 <= end <= 254 and start <= end):
        return jsonify({'ok': False, 'err': 'start/end must be 0–254 and start ≤ end'}), 400
    parts = base.split('.')
    if len(parts) != 3 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return jsonify({'ok': False, 'err': 'base must be x.x.x (e.g. 192.168.10)'}), 400
    _scan_base, _scan_start, _scan_end = base, start, end
    _save_state()
    _scan_now.set()   # trigger an immediate scan with the new range
    print(f'[scan] range updated → {base}.{start}–{base}.{end}', flush=True)
    return jsonify({'ok': True, 'base': base, 'start': start, 'end': end})


@app.route('/api/lock', methods=['POST'])
def api_lock():
    global _locked
    with _lock:
        _locked = True
    _save_state()
    print('[lock] roster locked by teacher', flush=True)
    return jsonify({'locked': True})


@app.route('/api/unlock', methods=['POST'])
def api_unlock():
    global _locked
    with _lock:
        _locked = False
    _save_state()
    print('[lock] roster unlocked — resuming discovery', flush=True)
    return jsonify({'locked': False})


@app.route('/api/demo', methods=['POST'])
def api_demo():
    """Seed the roster with fake classroom data and lock it (demo / presenter mode)."""
    global _locked
    DEMO = [
        ('192.168.1.101', 'Alice',  'alice-pi',  23, 45, 52.0, 31,  7340, 0.42),
        ('192.168.1.102', 'Bob',    'bob-pi',    71, 62, 74.0, 58,  5120, 1.80),
        ('192.168.1.103', 'Carol',  'carol-pi',  11, 38, 49.0, 22, 12600, 0.18),
        ('192.168.1.104', 'David',  'david-pi',  88, 79, 83.0, 74,  3200, 2.41),
        ('192.168.1.105', 'Emma',   'emma-pi',   34, 51, 58.0, 40,  9800, 0.66),
        ('192.168.1.106', 'Frank',  'frank-pi',  55, 67, 68.0, 55,  6600, 1.22),
        ('192.168.1.107', 'Grace',  'grace-pi',   8, 29, 46.0, 18, 21600, 0.09),
        ('192.168.1.108', 'Henry',  'henry-pi',  47, 55, 63.0, 47,  4400, 0.95),
        ('192.168.1.109', 'Iris',   'iris-pi',   62, 71, 77.0, 61,  2900, 1.54),
        ('192.168.1.110', 'Jake',   'jake-pi',   19, 42, 53.0, 29, 14400, 0.31),
        ('192.168.1.111', 'Karen',  'karen-pi',  76, 83, 79.0, 68,  1800, 2.10),
        ('192.168.1.112', 'Leo',    'leo-pi',    42, 58, 61.0, 44,  8100, 0.87),
    ]
    COLS, COL_W, ROW_H, START_X, START_Y = 4, 220, 260, 40, 80
    now = datetime.now().isoformat(timespec='seconds')
    with _lock:
        for i, (ip, label, hostname, cpu, mem, temp, disk, uptime, load1) in enumerate(DEMO):
            _roster[ip] = {
                'hostname':  hostname,
                'label':     label,
                'status':    'online',
                'health':    {'cpu': cpu, 'mem': mem, 'temp': temp,
                              'disk': disk, 'uptime': uptime, 'load1': load1},
                'last_seen': now,
                'added':     now,
            }
            col, row = i % COLS, i // COLS
            _layout[ip] = {'x': START_X + col * COL_W, 'y': START_Y + row * ROW_H}
        _locked = True
    _save_state()
    print(f'[demo] loaded {len(DEMO)} demo hosts, roster locked', flush=True)
    return jsonify({'ok': True, 'count': len(DEMO)})


@app.route('/api/scan', methods=['POST'])
def api_scan():
    """Trigger an immediate discovery sweep (ignores cooldown timer)."""
    _scan_now.set()
    return jsonify({'ok': True, 'msg': 'scan triggered'})


@app.route('/api/layout', methods=['POST'])
def api_layout():
    """Save a single card's canvas position."""
    d  = request.get_json(silent=True) or {}
    ip = d.get('ip')
    x  = d.get('x')
    y  = d.get('y')
    if not ip or x is None or y is None:
        return jsonify({'ok': False, 'err': 'ip, x, y required'}), 400
    with _lock:
        _layout[ip] = {'x': max(0, int(x)), 'y': max(0, int(y))}
    _save_state()
    return jsonify({'ok': True})


@app.route('/api/arrange', methods=['POST'])
def api_arrange():
    """Reset all cards to a clean left-to-right grid layout."""
    with _lock:
        ips = sorted(
            _roster.keys(),
            key=lambda ip: tuple(int(o) for o in ip.split('.')),
        )
        for idx, ip in enumerate(ips):
            _layout[ip] = _default_pos(idx)
    _save_state()
    return jsonify({'ok': True})


@app.route('/api/label/<path:ip>', methods=['POST'])
def api_label(ip):
    """Assign a student name to a Pi card."""
    d     = request.get_json(silent=True) or {}
    label = str(d.get('label', ''))[:80]
    with _lock:
        if ip not in _roster:
            return jsonify({'ok': False, 'err': 'unknown host'}), 404
        _roster[ip]['label'] = label
    _save_state()
    return jsonify({'ok': True, 'label': label})


@app.route('/api/remove/<path:ip>', methods=['POST'])
def api_remove(ip):
    """Remove a host from the roster and canvas."""
    with _lock:
        _roster.pop(ip, None)
        _layout.pop(ip, None)
    _save_state()
    return jsonify({'ok': True})


@app.route('/api/clear_offline', methods=['POST'])
def api_clear_offline():
    """Remove all hosts that are currently offline."""
    with _lock:
        offline = [ip for ip, v in _roster.items() if v.get('status') == 'offline']
        for ip in offline:
            _roster.pop(ip, None)
            _layout.pop(ip, None)
    _save_state()
    return jsonify({'ok': True, 'removed': len(offline)})


# ---------------------------------------------------------------------------
# FortiGate — in-memory session state (cleared on restart).
#
# Supports two auth modes:
#   • Session  — POST /logincheck with admin username + password.
#                FortiOS sets APSCOOKIE + ccsrftoken cookies.
#   • API token — pass Bearer token directly (requires a REST API admin on
#                 the FortiGate: System → Administrators → Create API User).
#
# Only GET /api/v2/monitor/system/interface is needed so CSRF tokens are
# not required (CSRF applies to mutating methods only).
# ---------------------------------------------------------------------------
FORTI_IP            = os.environ.get('FORTI_IP',   '192.168.0.10')
FORTI_TIMEOUT       = int(os.environ.get('FORTI_TIMEOUT', '8'))
FORTI_POLL_INTERVAL = int(os.environ.get('FORTI_POLL_INTERVAL', '10'))

_forti_lock      = threading.Lock()
_forti_cookie    = ''    # "APSCOOKIE_...=...; ccsrftoken=..." — session auth
_forti_token     = ''    # Bearer token — token auth
_forti_authed    = False
_forti_bg_cache  = None  # last successful interface list (server-side)
_forti_bg_time   = 0.0   # epoch seconds of last successful fetch
_forti_bg_stop   = threading.Event()
_forti_bg_thread = None  # background poller thread


def _forti_bg_poller():
    """Background thread: polls FortiGate every FORTI_POLL_INTERVAL seconds.

    Uses a persistent HTTPS keep-alive connection so each poll avoids the
    TCP + TLS handshake overhead.  Polls immediately on first iteration
    (no leading sleep) so the cache is populated right after connect.
    """
    global _forti_bg_cache, _forti_bg_time, _forti_authed
    print(f'[forti] background poller started (interval={FORTI_POLL_INTERVAL}s)', flush=True)
    conn = None
    PATH = '/api/v2/monitor/system/interface?vdom=root'

    while True:
        # ── fetch now ────────────────────────────────────────────────────
        with _forti_lock:
            authed = _forti_authed
            cookie = _forti_cookie
            token  = _forti_token
        if not authed:
            break

        headers = {}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        elif cookie:
            headers['Cookie'] = cookie
        else:
            break

        try:
            if conn is None:
                conn = http.client.HTTPSConnection(
                    FORTI_IP, context=_forti_ctx(), timeout=FORTI_TIMEOUT)
            conn.request('GET', PATH, headers=headers)
            resp = conn.getresponse()
            raw  = resp.read().decode(errors='ignore')
            status = resp.status
            try:
                data = json.loads(raw)
            except Exception:
                data = {'_raw': raw[:500]}
        except Exception as e:
            print(f'[forti] poll error — {type(e).__name__}: {e}', flush=True)
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            # don't exit — reconnect on next cycle
            if _forti_bg_stop.wait(FORTI_POLL_INTERVAL):
                break
            continue

        if status == 200 and data:
            results = data.get('results', [])
            # FortiOS returns results as a dict keyed by interface name on
            # some firmware versions — normalise to a flat list either way.
            if isinstance(results, dict):
                results = list(results.values())
            with _forti_lock:
                _forti_bg_cache = results
                _forti_bg_time  = time.time()
            print(f'[forti] cache refreshed ({len(_forti_bg_cache)} interfaces)', flush=True)
        elif status in (401, 403):
            with _forti_lock:
                _forti_authed = False
            print('[forti] session expired — poller exiting', flush=True)
            break
        else:
            print(f'[forti] poll failed HTTP {status}', flush=True)

        # ── sleep, then loop back to poll ─────────────────────────────────
        if _forti_bg_stop.wait(FORTI_POLL_INTERVAL):
            break

    if conn:
        try:
            conn.close()
        except Exception:
            pass
    print('[forti] background poller stopped', flush=True)


def _start_forti_poller():
    """Start the background poller thread if not already running."""
    global _forti_bg_thread, _forti_bg_stop
    if _forti_bg_thread and _forti_bg_thread.is_alive():
        return
    _forti_bg_stop.clear()
    _forti_bg_thread = threading.Thread(target=_forti_bg_poller,
                                        daemon=True, name='forti-bg')
    _forti_bg_thread.start()


def _forti_ctx():
    """SSL context that skips FortiGate's self-signed cert verification."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def _forti_get(path):
    """Authenticated GET to the FortiGate REST API.
    Returns (http_status: int, body: dict | None)."""
    with _forti_lock:
        cookie = _forti_cookie
        token  = _forti_token

    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    elif cookie:
        headers['Cookie'] = cookie
    else:
        return 401, None

    try:
        conn = http.client.HTTPSConnection(FORTI_IP, context=_forti_ctx(),
                                           timeout=FORTI_TIMEOUT)
        conn.request('GET', path, headers=headers)
        resp = conn.getresponse()
        raw  = resp.read().decode(errors='ignore')
        conn.close()
        try:
            return resp.status, json.loads(raw)
        except Exception:
            return resp.status, {'_raw': raw[:500]}
    except Exception as e:
        return 0, {'_exc': f'{type(e).__name__}: {e}'}


@app.route('/api/fortigate/connect', methods=['POST'])
def api_forti_connect():
    """Authenticate to the FortiGate.
    Body: {user, pass}  — OR —  {token}"""
    global _forti_cookie, _forti_token, _forti_authed

    d     = request.get_json(silent=True) or {}
    user  = d.get('user',  '').strip()
    pw    = d.get('pass',  '').strip()
    token = d.get('token', '').strip()

    # ── Token auth ──────────────────────────────────────────────────────
    if token:
        with _forti_lock:
            _forti_token  = token
            _forti_cookie = ''
        status, data = _forti_get('/api/v2/monitor/system/interface?vdom=root')
        if status == 200:
            seed = (data or {}).get('results', [])
            if isinstance(seed, dict):
                seed = list(seed.values())
            with _forti_lock:
                _forti_authed   = True
                _forti_bg_cache = seed
                _forti_bg_time  = time.time()
            _start_forti_poller()
            return jsonify({'ok': True, 'method': 'token'})
        with _forti_lock:
            _forti_token  = ''
            _forti_authed = False
        return jsonify({'ok': False, 'err': f'Token rejected (HTTP {status})'}), 401

    # ── Session auth ─────────────────────────────────────────────────────
    if not user or not pw:
        return jsonify({'ok': False, 'err': 'Provide username + password, or an API token'}), 400

    try:
        ctx  = _forti_ctx()
        conn = http.client.HTTPSConnection(FORTI_IP, context=ctx,
                                           timeout=FORTI_TIMEOUT)
        body = urllib.parse.urlencode({'username': user, 'secretkey': pw})
        conn.request('POST', '/logincheck', body=body,
                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
        resp = conn.getresponse()
        raw  = resp.read().decode(errors='ignore')

        # Collect all Set-Cookie headers into one string
        cookies = {}
        for hdr, val in resp.getheaders():
            if hdr.lower() == 'set-cookie':
                part = val.split(';')[0].strip()
                if '=' in part:
                    k, v = part.split('=', 1)
                    cookies[k.strip()] = v.strip()
        conn.close()

        # FortiOS always returns 200; check loginstatus in body
        try:
            body_j = json.loads(raw)
            if body_j.get('loginstatus') != 1:
                return jsonify({'ok': False, 'err': 'Invalid credentials'}), 401
        except Exception:
            # Some FortiOS versions redirect on success instead of JSON
            if resp.status not in (200, 302):
                return jsonify({'ok': False,
                                'err': f'Unexpected HTTP {resp.status}'}), 401

        cookie_str = '; '.join(f'{k}={v}' for k, v in cookies.items())
        with _forti_lock:
            _forti_cookie = cookie_str
            _forti_token  = ''
            _forti_authed = True

        print(f'[forti] session auth ok for user={user}', flush=True)
        # Poller's first iteration runs immediately and seeds the cache —
        # no second blocking round-trip needed here.
        _start_forti_poller()
        return jsonify({'ok': True, 'method': 'session'})

    except Exception as e:
        return jsonify({'ok': False, 'err': f'{type(e).__name__}: {e}'}), 500


@app.route('/api/fortigate/interfaces')
def api_forti_interfaces():
    """Return the server-side cached interface list immediately.
    The background poller keeps this fresh every FORTI_POLL_INTERVAL seconds."""
    with _forti_lock:
        authed = _forti_authed
        cache  = _forti_bg_cache
        t      = _forti_bg_time
    if not authed:
        return jsonify({'ok': False, 'err': 'Not authenticated',
                        'auth_required': True}), 401
    if cache is None:
        # Still waiting for the first background poll — tell the browser
        return jsonify({'ok': True, 'interfaces': [], 'loading': True, 'age': None})
    age = round(time.time() - t, 1)
    return jsonify({'ok': True, 'interfaces': cache, 'loading': False, 'age': age})


@app.route('/api/fortigate/disconnect', methods=['POST'])
def api_forti_disconnect():
    """Log out of the FortiGate, stop the background poller, and clear credentials."""
    global _forti_cookie, _forti_token, _forti_authed, _forti_bg_cache, _forti_bg_time
    _forti_bg_stop.set()   # signal background poller to exit (closes its conn)
    with _forti_lock:
        cookie = _forti_cookie
    if cookie:
        try:
            conn = http.client.HTTPSConnection(FORTI_IP, context=_forti_ctx(),
                                               timeout=4)
            conn.request('GET', '/logout', headers={'Cookie': cookie})
            conn.getresponse()
            conn.close()
        except Exception:
            pass
    with _forti_lock:
        _forti_cookie   = ''
        _forti_token    = ''
        _forti_authed   = False
        _forti_bg_cache = None
        _forti_bg_time  = 0.0
    print('[forti] disconnected', flush=True)
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Deploy / SSH exec
# ---------------------------------------------------------------------------
_deploy_jobs  = {}   # job_id -> {running, lines, exit}
_deploy_jlock = threading.Lock()


@app.route('/api/deploy/check', methods=['POST'])
def api_deploy_check():
    """Check which optional services are installed on a Pi."""
    ip = (request.get_json(silent=True) or {}).get('ip', '')
    with _lock:
        known = ip in _roster
    if not known:
        return jsonify({'ok': False, 'err': 'Unknown host'}), 404
    probe = (
        'command -v cockpit-bridge >/dev/null 2>&1 && echo "cockpit:1" || echo "cockpit:0"; '
        'systemctl is-active cockpit.socket >/dev/null 2>&1 '
        '  && echo "cockpit_active:1" || echo "cockpit_active:0"; '
        'command -v docker >/dev/null 2>&1 && echo "docker:1" || echo "docker:0"'
    )
    try:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(ip, username=SSH_USER, password=SSH_PASS, timeout=8)
        _, stdout, _ = cli.exec_command(probe)
        out = stdout.read().decode(errors='ignore')
        cli.close()
        svc = {}
        for line in out.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                svc[k.strip()] = (v.strip() == '1')
        return jsonify({'ok': True, 'services': svc})
    except Exception as e:
        return jsonify({'ok': False, 'err': f'{type(e).__name__}: {e}'}), 500


@app.route('/api/deploy/exec', methods=['POST'])
def api_deploy_exec():
    """Start an SSH command on a Pi; returns a job_id for polling."""
    d   = request.get_json(silent=True) or {}
    ip  = d.get('ip',  '').strip()
    cmd = d.get('cmd', '').strip()
    if not ip or not cmd:
        return jsonify({'ok': False, 'err': 'Missing ip or cmd'}), 400

    job_id = str(time.time_ns())
    with _deploy_jlock:
        _deploy_jobs[job_id] = {'running': True, 'lines': [], 'exit': None}

    def _run():
        try:
            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            cli.connect(ip, username=SSH_USER, password=SSH_PASS, timeout=10)
            _, stdout, _ = cli.exec_command(cmd, get_pty=True)
            for line in iter(stdout.readline, ''):
                with _deploy_jlock:
                    _deploy_jobs[job_id]['lines'].append(line.rstrip('\r\n'))
            ec = stdout.channel.recv_exit_status()
            cli.close()
            with _deploy_jlock:
                _deploy_jobs[job_id].update({'running': False, 'exit': ec})
            print(f'[deploy] {ip} exit={ec} cmd={cmd[:60]}', flush=True)
        except Exception as e:
            with _deploy_jlock:
                _deploy_jobs[job_id]['lines'].append(f'[error] {type(e).__name__}: {e}')
                _deploy_jobs[job_id].update({'running': False, 'exit': -1})

    threading.Thread(target=_run, daemon=True, name=f'deploy-{job_id}').start()
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/deploy/status/<job_id>')
def api_deploy_status(job_id):
    """Return current output lines and status for a deploy job."""
    with _deploy_jlock:
        job = _deploy_jobs.get(job_id)
    if job is None:
        return jsonify({'ok': False, 'err': 'Unknown job'}), 404
    return jsonify({'ok': True, **job})


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    _load_state()
    threading.Thread(target=_background, daemon=True, name='bg').start()
    print(
        f'[teacher] http://0.0.0.0:{LISTEN_PORT}/  '
        f'scan={_scan_base}.{_scan_start}-{_scan_end}  '
        f'user={SSH_USER}  '
        f'probe={PROBE_INTERVAL}s  health={HEALTH_INTERVAL}s',
        flush=True,
    )
    app.run(
        host='0.0.0.0', port=LISTEN_PORT,
        threaded=True, debug=False, use_reloader=False,
    )
