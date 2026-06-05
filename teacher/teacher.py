"""Classroom teacher dashboard.

Discovers Raspberry Pi student hosts by scanning a configurable IP range,
polls them via SSH for health metrics, and serves a drag-and-drop canvas
where the teacher arranges Pi cards to match the physical room layout.

Audience: classroom instructor running a multi-Pi cohort. NOT the
OTLab single-Pi operator (that's `dashboard/`). Different audience,
different deployment, different network. Runs as its own container.

Env vars (all have defaults, all overridable at runtime):
  SCAN_BASE        IP prefix for the classroom subnet  (default "192.168.1")
  SCAN_START       First host octet to scan            (default 100)
  SCAN_END         Last host octet to scan             (default 200)
  SSH_USER         SSH username shared by all Pis      (default "otadmin" — OTLab convention)
  SSH_PASS         SSH password — bootstrap only       (default "P@ssw0rd!" — OTLab convention)
  SSH_KEY_PATH     Teacher SSH private key path. If set, the teacher
                   panel uses key auth first and falls back to password.
                   Auto-generated as ed25519 on first start if missing.
                   Empty = password-only (default "/var/lib/teacher/keys/id_ed25519")
  DASH_USER        Teacher panel HTTP basic auth user  (default "otlab")
  DASH_PASS        Teacher panel HTTP basic auth pass  (default "P@ssw0rd!")
  FORTI_IP         FortiGate management IP — empty/blank hides FortiGate panel
                                                       (default "" — disabled)
  FORTI_TIMEOUT    FortiGate HTTPS timeout in seconds  (default 8)
  PROBE_INTERVAL   Seconds between discovery sweeps    (default 30)
  HEALTH_INTERVAL  Seconds between per-host polls      (default 15)
  LISTEN_PORT      HTTP listen port                    (default 8080)
  DATA_DIR         Persistent state directory          (default /var/lib/teacher)
  MAX_HOSTS        Auto-lock when N hosts found; 0=off (default 0)

Trust model (see teacher/README.md "Security posture"):
  - Teacher panel holds an ed25519 private key (auto-gen on first start)
  - bootstrap-students.sh pushes the matching pubkey to each student's
    ~otadmin/.ssh/authorized_keys + disables PasswordAuthentication in sshd
  - After bootstrap: only the teacher's key opens any student; students
    hold no SSH keys, no Tailscale auth, no outbound credentials at all
  - Password auth stays available DURING bootstrap as a one-time
    fallback so the initial push works
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
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCAN_BASE       = os.environ.get('SCAN_BASE',       '192.168.1')
SCAN_START      = int(os.environ.get('SCAN_START',  '100'))
SCAN_END        = int(os.environ.get('SCAN_END',    '200'))
# Default SSH creds match the OTLab convention (otadmin / P@ssw0rd!) so
# the teacher panel works against any Pi bootstrapped by our scripts.
SSH_USER        = os.environ.get('SSH_USER',        'otadmin')
SSH_PASS        = os.environ.get('SSH_PASS',        'P@ssw0rd!')
# Teacher SSH key — auto-generated on first start if path doesn't exist.
# When set, _connect() tries key auth first and falls back to password
# (the fallback path is only useful during initial student bootstrap;
# after bootstrap-students.sh runs, password auth is disabled on the
# students and key is the only way in).
SSH_KEY_PATH    = os.environ.get('SSH_KEY_PATH',
                                 '/var/lib/teacher/keys/id_ed25519').strip()
# HTTP basic auth on the teacher dashboard itself. Same convention as
# the OTLab operator dashboard. Rotate per event.
DASH_USER       = os.environ.get('DASH_USER',       'otlab')
DASH_PASS       = os.environ.get('DASH_PASS',       'P@ssw0rd!')
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

app  = Flask(__name__)
auth = HTTPBasicAuth()

# ---------------------------------------------------------------------------
# HTTP basic auth. Same convention as the OTLab operator dashboard —
# single shared lab user (default otlab / P@ssw0rd!, rotate per event).
# Applied to every route via @auth.login_required.
# ---------------------------------------------------------------------------
_USERS = {DASH_USER: generate_password_hash(DASH_PASS)}


@auth.verify_password
def _verify(username, password):
    """Standard flask-httpauth callback. Returns username on success, None on failure.
    Constant-time compare via werkzeug's check_password_hash."""
    if username in _USERS and check_password_hash(_USERS[username], password):
        return username
    return None


# ---------------------------------------------------------------------------
# Persistence — atomic write via temp file rename.
# ---------------------------------------------------------------------------
def _load_state():
    global _roster, _layout, _locked
    try:
        data = json.loads(STATE_FILE.read_text())
        with _lock:
            _roster = data.get('roster', {})
            _layout = data.get('layout', {})
            _locked = data.get('locked', False)
        print(f'[state] loaded {len(_roster)} hosts (locked={_locked})', flush=True)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f'[state] load error: {e}', flush=True)


def _save_state():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = {'roster': dict(_roster), 'layout': dict(_layout), 'locked': _locked}
    tmp = STATE_FILE.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(STATE_FILE)
    except Exception as e:
        print(f'[state] save error: {e}', flush=True)


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------
def _ensure_ssh_key():
    """Auto-generate an ed25519 keypair on first start if SSH_KEY_PATH
    is configured but the file doesn't exist yet. Uses ssh-keygen — the
    standard, well-understood path. Lives in the persistent volume so
    the key survives container restarts."""
    if not SSH_KEY_PATH:
        return
    key_path = Path(SSH_KEY_PATH)
    if key_path.exists():
        return
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', str(key_path),
             '-C', 'otlab-teacher@auto-generated'],
            check=True, capture_output=True, timeout=10,
        )
        os.chmod(key_path, 0o600)
        pub = Path(str(key_path) + '.pub')
        if pub.exists():
            os.chmod(pub, 0o644)
        print(f'[ssh-key] generated {key_path}', flush=True)
    except Exception as e:
        print(f'[ssh-key] gen failed: {type(e).__name__}: {e}', flush=True)


def _connect(ip):
    """Return a connected paramiko SSHClient or None on any failure.

    Two-step auth: try the teacher's key first (asymmetric trust model —
    once bootstrap-students.sh has run, this is the ONLY way in because
    students have PasswordAuthentication disabled). Fall back to password
    if key auth fails — that path only works during initial bootstrap
    before students are locked down."""
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # ── Try key auth first ──────────────────────────────────────────────
    if SSH_KEY_PATH and Path(SSH_KEY_PATH).exists():
        try:
            c.connect(
                ip,
                username=SSH_USER,
                key_filename=SSH_KEY_PATH,
                timeout=SSH_CONNECT_TIMEOUT,
                banner_timeout=SSH_CONNECT_TIMEOUT,
                auth_timeout=SSH_CONNECT_TIMEOUT,
                look_for_keys=False,
                allow_agent=False,
            )
            return c
        except paramiko.AuthenticationException:
            # Student doesn't have our pubkey yet — fall through to password.
            try: c.close()
            except Exception: pass
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        except Exception:
            # Network problem, host down, etc. Don't bother with password.
            return None

    # ── Password fallback (bootstrap path) ─────────────────────────────
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
                    f'{SCAN_BASE}.{i}'
                    for i in range(SCAN_START, SCAN_END + 1)
                    if f'{SCAN_BASE}.{i}' not in known
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
@auth.login_required
def index():
    return render_template('index.html')


@app.route('/api/status')
@auth.login_required
def api_status():
    with _lock:
        return jsonify({
            'roster':  {ip: dict(v) for ip, v in _roster.items()},
            'locked':  _locked,
            'layout':  dict(_layout),
            'updated': datetime.now().isoformat(timespec='seconds'),
            'config':  {
                'base':  SCAN_BASE,
                'start': SCAN_START,
                'end':   SCAN_END,
                # FortiGate config — the front-end uses these to decide
                # whether to render the FortiGate card + which IP to
                # display in the auth modal subtitle. enabled=false
                # means hide the whole panel.
                'fortigate': {
                    'enabled': bool(FORTI_IP),
                    'ip':      FORTI_IP,
                },
            },
        })


@app.route('/api/lock', methods=['POST'])
@auth.login_required
def api_lock():
    global _locked
    with _lock:
        _locked = True
    _save_state()
    print('[lock] roster locked by teacher', flush=True)
    return jsonify({'locked': True})


@app.route('/api/unlock', methods=['POST'])
@auth.login_required
def api_unlock():
    global _locked
    with _lock:
        _locked = False
    _save_state()
    print('[lock] roster unlocked — resuming discovery', flush=True)
    return jsonify({'locked': False})


@app.route('/api/demo', methods=['POST'])
@auth.login_required
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
@auth.login_required
def api_scan():
    """Trigger an immediate discovery sweep (ignores cooldown timer)."""
    _scan_now.set()
    return jsonify({'ok': True, 'msg': 'scan triggered'})


@app.route('/api/layout', methods=['POST'])
@auth.login_required
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
@auth.login_required
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
@auth.login_required
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
@auth.login_required
def api_remove(ip):
    """Remove a host from the roster and canvas."""
    with _lock:
        _roster.pop(ip, None)
        _layout.pop(ip, None)
    _save_state()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Links Hub — one place per-student + teacher landing page with live
# HTTP status indicators. URLs are built from a static catalogue + the
# per-Pi IPs the teacher panel already discovered. Status checks run
# server-side (the teacher Pi is the one with reachability to every
# student segment + the venue/Tailscale net).
# ---------------------------------------------------------------------------

# Teacher-side services — single source of truth for the static links
# at the top of /links. Each entry: (label, port_or_path, scheme,
# probe_path). Probe path defaults to "/" if omitted.
TEACHER_SERVICES = [
    # (key,            label,                     scheme,  port, probe_path,           note)
    ('teacher-panel',  'Teacher Admin Panel',     'http',  8080, '/api/status',        'this page'),
    ('grafana',        'Grafana',                 'http',  3000, '/api/health',        'admin / P@ssw0rd!'),
    ('loki',           'Loki (raw API)',          'http',  3100, '/ready',             'log ingest'),
    ('portainer',      'Portainer',               'https', 9443, '/',                  'container management'),
    ('esphome',        'ESPHome Dashboard',       'http',  6052, '/',                  'ESP32 reflash / config'),
    ('edgeshark',      'Edgeshark',               'http',  5001, '/',                  'per-container pcap'),
    ('cockpit',        'Cockpit',                 'https', 9090, '/',                  'Pi system view'),
    ('qwiic-io',       'Physical I/O (Qwiic)',    'http',  8090, '/api/state',         'temp + relay + wind-turbine motor'),
    ('ladder-plc',     'Ladder PLC',              'http',  8091, '/api/status',        'program logic: temp -> motor/relay'),
]

# Per-student services — one entry per port the student Pi exposes via
# the clab fabric. The IP is filled in per discovered Pi from the roster.
#
# Intentionally excludes:
#   - Local Loki (binds to 127.0.0.1 on each student by design — see
#     install-student-loki.sh; only reachable from the student itself)
#   - Cockpit (teacher-only by default; not in install-virtual-lab.sh)
# Both can still be browsed by SSH-tunneling to the student.
STUDENT_SERVICES = [
    # (key,             label,                    scheme,  port, probe_path,    note)
    # Dashboard serves HTTPS with a self-signed cert (generated at first
    # boot by install-dashboard.sh). _probe_url verifies disabled via
    # _forti_ctx so the probe accepts it; the browser will show a one-
    # time cert warning the operator clicks through.
    ('dashboard',       'OTLab Dashboard',        'https', 8000, '/api/status', 'otlab / P@ssw0rd!'),
    ('openplc-1',       'OpenPLC plc-1-virt',     'http',  8081, '/',           'openplc / P@ssw0rd!'),
    ('openplc-2',       'OpenPLC plc-2-virt',     'http',  8082, '/',           'openplc / P@ssw0rd!'),
    # Physical I/O stack on the student's Cruiser board (host services, not
    # clab). Probes hit auth-protected endpoints -> "auth" pill = alive.
    ('qwiic-io',        'Physical I/O (Qwiic)',   'http',  8090, '/api/state',  'temp + relay + wind-turbine motor'),
    ('ladder-plc',      'Ladder PLC',             'http',  8091, '/api/status', 'student programs: temp -> motor/relay'),
    # The :8090 page also IS the live turbine view when the Pi is in virtual
    # mode (otlab-vio) — surfaced as its own tile so the simulated turbine is
    # one click away while a student has no physical Qwiic kit yet.
    ('turbine',         'Virtual Turbine',        'http',  8090, '/api/state',  'live simulated turbine — driven by your ladder logic'),
]

# Standalone devices that aren't part of student/teacher Pis — ESP32s
# on the classroom WiFi, FortiGate, etc. Filled from env so each event
# can wire up its real fleet without code changes.
#
# ESP32 fleet convention: 10.20.30.201 = teacher, 10.20.30.202+ = students.
# Set ESP32_IPS as a comma-separated "label=ip" list to enable, e.g.
#   ESP32_IPS="teacher=10.20.30.201,student-1=10.20.30.202,student-2=10.20.30.203"
ESP32_IPS = os.environ.get('ESP32_IPS', 'teacher=10.20.30.201').strip()

# Teacher's own IPs — filtered out of the Links Hub student list so the
# teacher Pi doesn't appear as a "student" just because it's inside the
# scan range. Comma-separated. Set this when the panel runs on the same
# subnet that hosts the student Pis (the common case).
#   TEACHER_IPS="10.20.30.27,100.77.2.22"
TEACHER_IPS = {
    ip.strip()
    for ip in os.environ.get('TEACHER_IPS', '').split(',')
    if ip.strip()
}

# Honeypot Pi IPs — these run Conpot personas, not student services, so
# we render them in their own section instead of mixed into students.
# Comma-separated.
#   HONEYPOT_IPS="10.20.30.48"
HONEYPOT_IPS = {
    ip.strip()
    for ip in os.environ.get('HONEYPOT_IPS', '').split(',')
    if ip.strip()
}

# Per-honeypot services — these are the Conpot personas exposed on the
# honeypot Pi. Modbus is on tcp:502 — not browsable but worth listing
# so the operator sees the targets exist.
HONEYPOT_PERSONAS = [
    # (key,           label,                       port, note)
    ('siemens',       'Conpot · Siemens persona',  502,  '10.20.30.50:502 — Modbus'),
    ('schneider',     'Conpot · Schneider persona', 502, '10.20.30.51:502 — Modbus'),
    ('rockwell',      'Conpot · Rockwell persona',  502, '10.20.30.52:502 — Modbus'),
]


def _build_teacher_links(host_ip):
    """Build the teacher service URL list, anchored on `host_ip` (the IP
    the browser used to hit the teacher panel — so links work whether
    the operator is on Tailscale, classroom segment, or office LAN)."""
    out = []
    for key, label, scheme, port, probe, note in TEACHER_SERVICES:
        url = f'{scheme}://{host_ip}:{port}{probe}'
        click_url = f'{scheme}://{host_ip}:{port}/'
        out.append({
            'key':       f'teacher-{key}',
            'label':     label,
            'url':       click_url,
            'probe_url': url,
            'note':      note,
        })
    # Physical I/O as a Modbus TCP slave (otlab-modbus-io) — tcp:502, not
    # browsable, so list it as an info tile (no HTTP probe).
    out.append({
        'key':       'teacher-modbus-io',
        'label':     'Physical I/O (Modbus)',
        'url':       f'http://{host_ip}:8090/',
        'probe_url': '',     # 502 is not HTTP — don't probe
        'note':      f'{host_ip}:502 — Modbus TCP (OpenPLC/SCADA target)',
    })
    return out


def _build_student_links(student_ip):
    """Build the per-student URL list for a given Pi IP."""
    out = []
    for key, label, scheme, port, probe, note in STUDENT_SERVICES:
        click_url = f'{scheme}://{student_ip}:{port}/'
        probe_url = f'{scheme}://{student_ip}:{port}{probe}'
        out.append({
            'key':       f'student-{student_ip}-{key}',
            'label':     label,
            'url':       click_url,
            'probe_url': probe_url,
            'note':      note,
        })
    # Physical I/O as a Modbus TCP slave (otlab-modbus-io) — tcp:502, not
    # browsable, so list it as an info tile (no HTTP probe) like the
    # honeypot Modbus personas. This is what OpenPLC/SCADA points at.
    out.append({
        'key':       f'student-{student_ip}-modbus-io',
        'label':     'Physical I/O (Modbus)',
        'url':       f'http://{student_ip}:8090/',   # link to the I/O page
        'probe_url': '',     # 502 is not HTTP — don't probe
        'note':      f'{student_ip}:502 — Modbus TCP (OpenPLC/SCADA target)',
    })
    # Always offer SSH as a "link" (mailto-style) — not auto-probed.
    out.append({
        'key':       f'student-{student_ip}-ssh',
        'label':     'SSH',
        'url':       f'ssh://{SSH_USER}@{student_ip}',
        'probe_url': '',     # no probe for SSH
        'note':      f'{SSH_USER}@{student_ip} (teacher key only)',
    })
    return out


def _build_esp32_links():
    """Parse ESP32_IPS env and return a list of link dicts."""
    out = []
    if not ESP32_IPS:
        return out
    for entry in ESP32_IPS.split(','):
        entry = entry.strip()
        if '=' not in entry:
            continue
        label, ip = entry.split('=', 1)
        label = label.strip()
        ip = ip.strip()
        if not ip:
            continue
        out.append({
            'key':       f'esp32-{label}',
            'label':     f'ESP32 ({label})',
            'url':       f'http://{ip}/',
            'probe_url': f'http://{ip}/',
            'note':      f'ESPHome web UI · {ip}',
        })
    return out


def _build_honeypot_links(honeypot_ips):
    """Build honeypot persona "links" — these are Modbus TCP targets, not
    HTTP; rendered with their tcp address as the URL (browsers won't
    open them but it's the canonical attack target). No probe — we'd
    need a raw socket connect for that, out of scope for now."""
    out = []
    for ip in sorted(honeypot_ips):
        for key, label, port, note in HONEYPOT_PERSONAS:
            out.append({
                'key':       f'honeypot-{ip}-{key}',
                'label':     label,
                'url':       f'tcp://{ip}:{port}',
                'probe_url': '',     # no HTTP probe
                'note':      note,
            })
    return out


def _probe_url(url, timeout=2.0):
    """HEAD-then-GET probe. Returns an int HTTP status (0 on connection
    failure). 200/2xx/3xx = up, 401/403 = up-but-auth (still green),
    everything else = sick."""
    if not url:
        return 0
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == 'https':
            conn = http.client.HTTPSConnection(
                parsed.hostname, parsed.port or 443,
                context=_forti_ctx(),       # accept self-signed certs
                timeout=timeout,
            )
        else:
            conn = http.client.HTTPConnection(
                parsed.hostname, parsed.port or 80,
                timeout=timeout,
            )
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        conn.request('GET', path)
        resp = conn.getresponse()
        status = resp.status
        conn.close()
        return status
    except Exception:
        return 0


@app.route('/links')
@auth.login_required
def links_page():
    """Render the Links Hub landing page."""
    return render_template('links.html')


@app.route('/api/links')
@auth.login_required
def api_links():
    """Build the link catalogue from current roster + static configs.
    Does NOT probe; that's a separate endpoint so the page can render
    fast and the indicators populate progressively."""
    # Use the Host header so URLs work whether the operator browsed via
    # Tailscale, classroom segment, or office LAN. Strip any port — we
    # rebuild service-specific ports.
    host = request.host.split(':')[0]

    with _lock:
        # Students: anything in the roster that isn't the teacher or a
        # honeypot. Honeypots are real lab assets but they don't run
        # student services, so they get their own section below.
        students = [
            {
                'ip':       ip,
                'label':    v.get('label') or v.get('hostname') or ip,
                'hostname': v.get('hostname', ''),
                'status':   v.get('status', 'unknown'),
                'links':    _build_student_links(ip),
            }
            for ip, v in sorted(
                _roster.items(),
                key=lambda kv: tuple(int(o) for o in kv[0].split('.')),
            )
            if ip not in TEACHER_IPS and ip not in HONEYPOT_IPS
        ]

        # Honeypots: every roster entry whose IP is in HONEYPOT_IPS gets
        # rendered with the Conpot persona link list instead of student
        # services. If HONEYPOT_IPS is empty but a host with the label
        # "honeypot" was discovered, treat it as one (cheap-and-nasty
        # auto-detect — operator can override via the env list).
        #
        # Don't reuse `host` as a loop variable here — that's the request
        # host computed above (used to build teacher URLs further down).
        auto_hps = set()
        for ip, v in _roster.items():
            r_label = (v.get('label') or '').lower()
            r_host  = (v.get('hostname') or '').lower()
            if 'honeypot' in r_label or 'honeypot' in r_host:
                auto_hps.add(ip)
        effective_hp = HONEYPOT_IPS | auto_hps
        honeypots = [
            {
                'ip':       ip,
                'label':    v.get('label') or v.get('hostname') or ip,
                'hostname': v.get('hostname', ''),
                'status':   v.get('status', 'unknown'),
                'links':    _build_honeypot_links([ip]),
            }
            for ip, v in sorted(
                _roster.items(),
                key=lambda kv: tuple(int(o) for o in kv[0].split('.')),
            )
            if ip in effective_hp
        ]
        # Also drop auto-detected honeypots from the student list (the
        # `students` comprehension above only knew about HONEYPOT_IPS).
        students = [s for s in students if s['ip'] not in effective_hp]

    return jsonify({
        'teacher': {
            'host':  host,
            'links': _build_teacher_links(host),
        },
        'students':  students,
        'honeypots': honeypots,
        'esp32':     _build_esp32_links(),
        'updated':   datetime.now().isoformat(timespec='seconds'),
    })


@app.route('/api/links/status', methods=['POST'])
@auth.login_required
def api_links_status():
    """Probe a batch of URLs in parallel and return {key: status}.
    Body: {"urls": [{"key": "...", "probe_url": "http://..."}, ...]}
    Status: int HTTP code, or 0 on connect failure."""
    payload = request.get_json(silent=True) or {}
    items = payload.get('urls', [])
    if not items:
        return jsonify({'statuses': {}})

    results = {}
    # Bounded concurrency — students + teacher services together are
    # rarely more than ~50 URLs even with 20 students, but the timeouts
    # add up serially.
    with ThreadPoolExecutor(max_workers=16) as pool:
        future_to_key = {
            pool.submit(_probe_url, it.get('probe_url', ''), 1.5): it.get('key')
            for it in items if it.get('key')
        }
        for fut in as_completed(future_to_key):
            key = future_to_key[fut]
            try:
                results[key] = fut.result()
            except Exception:
                results[key] = 0
    return jsonify({'statuses': results})


@app.route('/api/teacher/pubkey')
@auth.login_required
def api_pubkey():
    """Return the teacher panel's SSH public key.

    Used by `teacher/bootstrap-students.sh` to push the pubkey into each
    student's authorized_keys before disabling password auth. After
    bootstrap, this is the ONLY credential that opens any student."""
    if not SSH_KEY_PATH:
        return jsonify({'ok': False, 'err': 'SSH_KEY_PATH not configured'}), 400
    pub = Path(SSH_KEY_PATH + '.pub')
    if not pub.exists():
        return jsonify({'ok': False, 'err': f'Public key not found at {pub}'}), 404
    return jsonify({
        'ok':     True,
        'pubkey': pub.read_text().strip(),
        'path':   str(pub),
    })


@app.route('/api/clear_offline', methods=['POST'])
@auth.login_required
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
# FortiGate IP — empty / unset means the panel is disabled and won't
# render in the UI. Single-Pi standalone users don't have a Fortinet,
# so off-by-default is the right behavior. Set to the FortiGate's
# management IP to enable.
FORTI_IP      = os.environ.get('FORTI_IP',   '').strip()
FORTI_TIMEOUT = int(os.environ.get('FORTI_TIMEOUT', '8'))

_forti_lock   = threading.Lock()
_forti_cookie = ''    # "APSCOOKIE_...=...; ccsrftoken=..." — session auth
_forti_token  = ''    # Bearer token — token auth
_forti_authed = False


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


def _forti_disabled_response():
    """Standard 503 returned by every FortiGate endpoint when FORTI_IP
    is unset. Lets the front-end show a clear error if it somehow gets
    called despite the UI hiding the panel."""
    return jsonify({
        'ok': False,
        'err': 'FortiGate panel disabled — set FORTI_IP env to enable',
        'disabled': True,
    }), 503


@app.route('/api/fortigate/connect', methods=['POST'])
@auth.login_required
def api_forti_connect():
    """Authenticate to the FortiGate.
    Body: {user, pass}  — OR —  {token}"""
    if not FORTI_IP:
        return _forti_disabled_response()

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
            with _forti_lock:
                _forti_authed = True
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
        return jsonify({'ok': True, 'method': 'session'})

    except Exception as e:
        return jsonify({'ok': False, 'err': f'{type(e).__name__}: {e}'}), 500


@app.route('/api/fortigate/interfaces')
@auth.login_required
def api_forti_interfaces():
    """Fetch interface statistics from the FortiGate."""
    if not FORTI_IP:
        return _forti_disabled_response()

    global _forti_authed
    with _forti_lock:
        authed = _forti_authed
    if not authed:
        return jsonify({'ok': False, 'err': 'Not authenticated',
                        'auth_required': True}), 401

    status, data = _forti_get('/api/v2/monitor/system/interface?vdom=root')

    if status == 401 or status == 403:
        with _forti_lock:
            _forti_authed = False
        return jsonify({'ok': False, 'err': 'Session expired',
                        'auth_required': True}), 401

    if status != 200 or data is None:
        return jsonify({'ok': False,
                        'err': f'FortiGate returned HTTP {status}',
                        'detail': (data or {}).get('_raw', '')}), 502

    interfaces = data.get('results', [])
    return jsonify({'ok': True, 'interfaces': interfaces})


@app.route('/api/fortigate/disconnect', methods=['POST'])
@auth.login_required
def api_forti_disconnect():
    """Log out of the FortiGate and clear stored credentials."""
    if not FORTI_IP:
        return _forti_disabled_response()
    global _forti_cookie, _forti_token, _forti_authed
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
        _forti_cookie = ''
        _forti_token  = ''
        _forti_authed = False
    print('[forti] disconnected', flush=True)
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    _ensure_ssh_key()
    _load_state()
    threading.Thread(target=_background, daemon=True, name='bg').start()
    print(
        f'[teacher] http://0.0.0.0:{LISTEN_PORT}/  '
        f'scan={SCAN_BASE}.{SCAN_START}-{SCAN_END}  '
        f'user={SSH_USER}  '
        f'probe={PROBE_INTERVAL}s  health={HEALTH_INTERVAL}s',
        flush=True,
    )
    app.run(
        host='0.0.0.0', port=LISTEN_PORT,
        threaded=True, debug=False, use_reloader=False,
    )
