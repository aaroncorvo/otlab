"""OTLab status dashboard — Flask app.

Runs on softplc-2 as the `otuser` system user. Probes every device on the
lab in a background thread and exposes:

  GET  /                       — single-page HTML dashboard
  GET  /api/status             — JSON snapshot of latest probes (auth)
  POST /api/reboot/<host>      — issue `sudo systemctl reboot` on a Pi
  POST /api/restart/<svc>      — restart a service on softplc-2 self
  POST /api/capture/<host>     — kick off a 60s tcpdump capture
  GET  /api/captures           — list recently captured pcaps
  GET  /api/capture-download/<id>  — download a captured pcap

Defaults: HTTPS on port 8000 with a self-signed cert (10 yr SAN-rich).
HTTP basic auth user `otlab` / pass `P@ssw0rd!` (override via DASH_USER /
DASH_PASS env vars in dashboard.env).

Lab is intentionally a teaching environment — auth keeps booth visitors
from accidentally mashing buttons, not determined attackers out.
"""

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, send_file, abort
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

from pymodbus.client import ModbusTcpClient


# ---------------------------------------------------------------------------
# Config — env-overridable, defaults baked in.
# ---------------------------------------------------------------------------
DASH_USER       = os.environ.get('DASH_USER', 'otlab')
DASH_PASS       = os.environ.get('DASH_PASS', 'P@ssw0rd!')
PROBE_INTERVAL  = float(os.environ.get('PROBE_INTERVAL', '2.5'))
PROBE_TIMEOUT   = float(os.environ.get('PROBE_TIMEOUT',  '1.5'))
HEALTH_INTERVAL = float(os.environ.get('HEALTH_INTERVAL', '5.0'))
HEALTH_TIMEOUT  = float(os.environ.get('HEALTH_TIMEOUT',  '4.0'))
LISTEN_PORT     = int(os.environ.get('LISTEN_PORT',     '8000'))
SSH_USER        = os.environ.get('SSH_USER',            'otadmin')
DASH_CERT       = os.environ.get('DASH_CERT', '/home/otuser/lab/dashboard/cert.pem')
DASH_KEY        = os.environ.get('DASH_KEY',  '/home/otuser/lab/dashboard/key.pem')

# Sparkline ring-buffer length. 5 min @ 2.5 s/probe = 120 samples.
HISTORY_LEN     = int(os.environ.get('HISTORY_LEN', '120'))

# Pcap captures land here. Directory is created at startup.
CAPTURES_DIR    = Path(os.environ.get('CAPTURES_DIR', '/home/otuser/lab/dashboard/captures'))
CAPTURE_SECS    = int(os.environ.get('CAPTURE_SECS', '60'))


# ---------------------------------------------------------------------------
# Topology — single source of truth for what we probe.
# ---------------------------------------------------------------------------
HOSTS = {
    'softplc-1':     {'lab': '10.20.30.47',  'mgmt': '192.168.120.216', 'reboot': True, 'self': False},
    'softplc-2':     {'lab': '10.20.30.49',  'mgmt': '192.168.120.19',  'reboot': True, 'self': True},
    'honeypot-host': {'lab': '10.20.30.48',  'mgmt': '192.168.120.48',  'reboot': True, 'self': False},
}
CONPOTS = {
    'siemens-PS4':       {'ip': '10.20.30.50', 'tcp_ports': [80, 102],
                          'log': '/home/acrow/conpot/compose/logs/siemens/conpot.log',
                          'connect_re': r'New S7 connection from'},
    'schneider-M340':    {'ip': '10.20.30.51', 'tcp_ports': [80, 502],
                          'log': '/home/acrow/conpot/compose/logs/schneider/conpot.log',
                          'connect_re': r'New Modbus connection from'},
    'rockwell-CHEM':     {'ip': '10.20.30.52', 'tcp_ports': [80, 44818],
                          'log': '/home/acrow/conpot/compose/logs/allenbradley/conpot.log',
                          'connect_re': r"EtherNet/IP CIP Request"},
}

# IPs we consider "our own" (dashboard / lab infrastructure) so we can
# filter out our own probe traffic when reporting honeypot hits.
INTERNAL_IPS = {'10.20.30.47', '10.20.30.48', '10.20.30.49',
                '192.168.120.19', '192.168.120.216', '192.168.120.48'}

# Reusable SSH base command. ControlMaster keeps a persistent control
# socket per remote so subsequent probes are sub-100 ms instead of a
# full TCP+TLS handshake every poll.
SSH_CTRL = '/home/otuser/.ssh/cm-%h-%p-%r'
SSH_BASE = [
    'ssh',
    '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', 'ConnectTimeout=4',
    '-o', 'ControlMaster=auto',
    '-o', f'ControlPath={SSH_CTRL}',
    '-o', 'ControlPersist=120',
]


# ---------------------------------------------------------------------------
# Flask + auth setup.
# ---------------------------------------------------------------------------
app = Flask(__name__)
auth = HTTPBasicAuth()
USERS = {DASH_USER: generate_password_hash(DASH_PASS)}


@auth.verify_password
def verify(username, password):
    if username in USERS and check_password_hash(USERS[username], password):
        return username
    return None


# ---------------------------------------------------------------------------
# Probe primitives.
# ---------------------------------------------------------------------------
def ping(host, timeout=PROBE_TIMEOUT):
    try:
        r = subprocess.run(
            ['ping', '-c', '1', '-W', str(max(1, int(timeout))), host],
            capture_output=True, timeout=timeout + 0.5,
        )
        if r.returncode == 0:
            for line in r.stdout.decode(errors='ignore').splitlines():
                if 'time=' in line:
                    try:
                        ms = float(line.split('time=')[1].split()[0].rstrip('ms'))
                        return {'up': True, 'ms': round(ms, 1)}
                    except Exception:
                        pass
            return {'up': True, 'ms': None}
        return {'up': False}
    except Exception:
        return {'up': False}


def tcp_probe(host, port, timeout=PROBE_TIMEOUT):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def http_probe(url, timeout=PROBE_TIMEOUT):
    try:
        urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # any HTTP status = server alive
    except Exception:
        return False


def modbus_probe(host, port, hr_count=4, coil_count=2, timeout=PROBE_TIMEOUT):
    try:
        c = ModbusTcpClient(host, port=port, timeout=timeout)
        if not c.connect():
            return None
        hr = c.read_holding_registers(address=0, count=hr_count, device_id=0)
        co = c.read_coils(address=0, count=coil_count, device_id=0)
        c.close()
        if hr.isError() or co.isError():
            return None
        return {'hr': list(hr.registers), 'co': list(co.bits[:coil_count])}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# System health.
# ---------------------------------------------------------------------------
# Inline shell that emits a single JSON line. CPU is sampled across two
# /proc/stat reads 200 ms apart for an accurate instantaneous %.
HEALTH_SCRIPT = r'''
read_cpu() { awk '/^cpu / {idle=$5; total=0; for(i=2;i<=NF;i++) total+=$i; print idle, total}' /proc/stat; }
read s1_idle s1_total < <(read_cpu)
sleep 0.2
read s2_idle s2_total < <(read_cpu)
cpu=$(awk -v si="$s1_idle" -v st="$s1_total" -v ei="$s2_idle" -v et="$s2_total" \
       'BEGIN { d=et-st; if (d>0) printf "%.1f", 100*(1-(ei-si)/d); else print "0.0" }')
mem=$(awk '/^MemTotal:/{t=$2}/^MemAvailable:/{a=$2}END{printf "%.1f", 100*(t-a)/t}' /proc/meminfo)
disk_root=$(df -P / | awk 'NR==2 { gsub("%","",$5); print $5 }')
disk_used_gb=$(df -BG -P / | awk 'NR==2 { gsub("G","",$3); print $3 }')
disk_size_gb=$(df -BG -P / | awk 'NR==2 { gsub("G","",$2); print $2 }')
# Note: vcgencmd writes its "Can't open /dev/vcio" error to STDOUT, not
# stderr, so 2>/dev/null doesn't help. Use grep to extract only a real
# decimal number; absent that, return empty.
temp=$(vcgencmd measure_temp 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
uptime=$(awk '{print int($1)}' /proc/uptime)
load1=$(awk '{print $1}' /proc/loadavg)
load5=$(awk '{print $2}' /proc/loadavg)
failed=$(systemctl --failed --plain --no-legend 2>/dev/null | wc -l || echo 0)
boot_dev=$(findmnt -no SOURCE / | sed 's|/dev/||')
printf '{"cpu":%s,"mem":%s,"disk_pct":%s,"disk_used":%s,"disk_size":%s,"temp":"%s","uptime":%s,"load1":%s,"load5":%s,"failed":%s,"boot_dev":"%s"}\n' \
       "$cpu" "$mem" "$disk_root" "$disk_used_gb" "$disk_size_gb" "$temp" "$uptime" "$load1" "$load5" "$failed" "$boot_dev"
'''


def _run_remote(target_user_at_ip, script, timeout=HEALTH_TIMEOUT):
    """Run a shell script on a remote Pi via SSH ControlMaster. Returns
    (stdout_str, ok_bool)."""
    cmd = SSH_BASE + [target_user_at_ip, 'bash -s']
    try:
        r = subprocess.run(cmd, input=script.encode(),
                           capture_output=True, timeout=timeout)
        return r.stdout.decode(errors='ignore'), r.returncode == 0
    except Exception:
        return '', False


def health_local():
    """System-health for softplc-2 itself — no SSH overhead."""
    try:
        r = subprocess.run(['bash', '-c', HEALTH_SCRIPT],
                           capture_output=True, timeout=HEALTH_TIMEOUT)
        if r.returncode == 0:
            return json.loads(r.stdout.decode())
    except Exception as e:
        print(f"[health-local] {e}", flush=True)
    return None


def health_remote(target_ip):
    out, ok = _run_remote(f'{SSH_USER}@{target_ip}', HEALTH_SCRIPT)
    if not ok:
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Honeypot intelligence — parse Conpot logs.
# ---------------------------------------------------------------------------
# Pull the last 1000 lines of each persona's log (cheap), let the dashboard
# do the time-windowing + IP extraction. Logs have stable timestamp prefix
# `YYYY-MM-DD HH:MM:SS,mmm` followed by free-form text.
HONEYPOT_LOG_TAIL = 1000

LOG_TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
IP_RE     = re.compile(r"(?:from |Client \(')(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")


def fetch_conpot_log_tail(log_path):
    """sudo-tail the conpot log on honeypot-host. Returns list of lines."""
    script = f'sudo tail -n {HONEYPOT_LOG_TAIL} "{log_path}" 2>/dev/null || true'
    out, ok = _run_remote(f'{SSH_USER}@{HOSTS["honeypot-host"]["lab"]}', script,
                          timeout=HEALTH_TIMEOUT)
    return out.splitlines() if ok else []


def analyze_conpot_log(lines, persona_cfg, now_ts):
    """Extract connection counts (1m/5m/1h windows), source IPs, recent
    events. Filters out our own internal-IP probe noise."""
    connect_re = re.compile(persona_cfg['connect_re'])
    windows = {'1m': 60, '5m': 300, '1h': 3600}
    cnt = {k: {'all': 0, 'ext': 0} for k in windows}
    src_ips_all  = {}  # ip -> count (all-time-in-tail)
    src_ips_ext  = {}  # ip -> count (external only)
    recent_evts  = []  # last N matching connect events for the timeline

    for line in lines:
        m_ts = LOG_TS_RE.match(line)
        if not m_ts:
            continue
        try:
            t = datetime.strptime(m_ts.group(1), '%Y-%m-%d %H:%M:%S').timestamp()
        except Exception:
            continue
        age = now_ts - t

        if connect_re.search(line):
            ip_m = IP_RE.search(line)
            ip = ip_m.group(1) if ip_m else None
            is_ext = ip and ip not in INTERNAL_IPS
            for k, secs in windows.items():
                if age <= secs:
                    cnt[k]['all'] += 1
                    if is_ext:
                        cnt[k]['ext'] += 1
            if ip:
                src_ips_all[ip] = src_ips_all.get(ip, 0) + 1
                if is_ext:
                    src_ips_ext[ip] = src_ips_ext.get(ip, 0) + 1
            if len(recent_evts) < 5 and is_ext:
                recent_evts.append({'t': m_ts.group(1), 'ip': ip})

    # Top external IPs by hit count (top 5)
    top_ext = sorted(src_ips_ext.items(), key=lambda kv: -kv[1])[:5]
    return {
        'conn_1m':  cnt['1m'],
        'conn_5m':  cnt['5m'],
        'conn_1h':  cnt['1h'],
        'top_ips':  [{'ip': ip, 'hits': c} for ip, c in top_ext],
        'recent':   recent_evts,
        'lines_seen': len(lines),
    }


# ---------------------------------------------------------------------------
# Sparkline history — one ring buffer per (card, metric) pair.
# ---------------------------------------------------------------------------
HISTORY = {
    'softplc-1': {'tank': deque(maxlen=HISTORY_LEN), 'temp': deque(maxlen=HISTORY_LEN), 'press': deque(maxlen=HISTORY_LEN)},
    'softplc-2': {'tank': deque(maxlen=HISTORY_LEN), 'temp': deque(maxlen=HISTORY_LEN), 'press': deque(maxlen=HISTORY_LEN)},
}


def _push_history(card, hr):
    if card not in HISTORY or not hr or len(hr) < 3:
        return
    HISTORY[card]['tank'].append(round(hr[0] / 10.0, 1))
    HISTORY[card]['temp'].append(round(hr[1] / 10.0, 1))
    HISTORY[card]['press'].append(round(hr[2] / 10.0, 1))


def _history_dict(card):
    return {k: list(v) for k, v in HISTORY[card].items()}


# ---------------------------------------------------------------------------
# Probe orchestration — background thread updates STATE every PROBE_INTERVAL.
# Health probes have their own (slower) cadence to avoid SSH thrash.
# ---------------------------------------------------------------------------
STATE = {'updated': None, 'cards': {}, 'health': {}, 'honeypot': {}}
STATE_LOCK = threading.Lock()
LAST_HEALTH = 0.0
LAST_HONEYPOT = 0.0


def probe_fast():
    """Network + Modbus + HTTP — runs every PROBE_INTERVAL."""
    cards = {}

    # --- Network sanity row ---
    cards['wan']     = {**ping('1.1.1.1'),       'label': 'WAN (1.1.1.1)',      'group': 'net'}
    cards['mgmt_gw'] = {**ping('192.168.120.1'), 'label': 'Mgmt Gateway',       'group': 'net'}
    cards['fw']      = {**ping('10.20.30.1'),    'label': 'Firewall (TP-Link)', 'group': 'net'}

    # --- softplc-1 ---
    s1 = HOSTS['softplc-1']
    s1c = ping(s1['lab'])
    s1c.update({
        'label':  'softplc-1 — RASPLC01 (PLC master)',
        'group':  'plc',
        'plc_ui': http_probe(f"http://{s1['lab']}:8080/login"),
        'modbus': modbus_probe(s1['lab'], 502, hr_count=6, coil_count=2),
        'reboot': True,
    })
    if s1c['modbus']:
        _push_history('softplc-1', s1c['modbus']['hr'])
    s1c['history'] = _history_dict('softplc-1')
    cards['softplc-1'] = s1c

    # --- softplc-2 ---
    s2 = HOSTS['softplc-2']
    s2c = ping(s2['lab'])
    s2c.update({
        'label':  'softplc-2 — RASPLC02 (sensor-sim)',
        'group':  'plc',
        'plc_ui': http_probe(f"http://{s2['lab']}:8080/login"),
        'modbus': modbus_probe(s2['lab'], 5020, hr_count=4, coil_count=2),
        'reboot': True,
    })
    if s2c['modbus']:
        _push_history('softplc-2', s2c['modbus']['hr'])
    s2c['history'] = _history_dict('softplc-2')
    cards['softplc-2'] = s2c

    # --- honeypot-host ---
    hh = HOSTS['honeypot-host']
    hhc = ping(hh['lab'])
    hhc.update({
        'label':  'honeypot-host — Conpot fabric',
        'group':  'plc',
        'reboot': True,
    })
    cards['honeypot-host'] = hhc

    # --- Conpot personas (TCP probes only here; intel runs slower) ---
    for name, c in CONPOTS.items():
        cc = ping(c['ip'])
        cc.update({
            'label': name,
            'group': 'honeypot',
            'svcs':  {p: tcp_probe(c['ip'], p) for p in c['tcp_ports']},
        })
        cards[name] = cc

    return cards


def probe_health():
    """System-health for all 3 Pis. Slower cadence — runs every HEALTH_INTERVAL."""
    h = {}
    h['softplc-2']     = health_local()
    h['softplc-1']     = health_remote(HOSTS['softplc-1']['lab'])
    h['honeypot-host'] = health_remote(HOSTS['honeypot-host']['lab'])
    return h


def probe_honeypot():
    """Conpot log analysis. Slower cadence — runs every HEALTH_INTERVAL."""
    now = datetime.now().timestamp()
    out = {}
    for name, cfg in CONPOTS.items():
        lines = fetch_conpot_log_tail(cfg['log'])
        out[name] = analyze_conpot_log(lines, cfg, now)
    return out


def probe_loop():
    global LAST_HEALTH, LAST_HONEYPOT
    while True:
        try:
            cards = probe_fast()

            now = time.time()
            with STATE_LOCK:
                health   = STATE.get('health', {})
                honeypot = STATE.get('honeypot', {})

            if now - LAST_HEALTH >= HEALTH_INTERVAL:
                health = probe_health()
                LAST_HEALTH = now
            if now - LAST_HONEYPOT >= HEALTH_INTERVAL:
                honeypot = probe_honeypot()
                LAST_HONEYPOT = now

            with STATE_LOCK:
                STATE['updated']  = datetime.now().isoformat(timespec='seconds')
                STATE['cards']    = cards
                STATE['health']   = health
                STATE['honeypot'] = honeypot
        except Exception as e:
            print(f"[probe-loop] {type(e).__name__}: {e}", flush=True)
        time.sleep(PROBE_INTERVAL)


# ---------------------------------------------------------------------------
# Pcap capture — async via background thread. Captures land in
# CAPTURES_DIR; metadata kept in-memory.
# ---------------------------------------------------------------------------
CAPTURES = {}   # capture_id -> {host, status, file, started, finished, size}
CAPTURES_LOCK = threading.Lock()


def _do_capture(cap_id, host):
    target = HOSTS[host]
    is_self = target.get('self', False)
    remote_path = f'/tmp/dash-cap-{cap_id}.pcap'
    local_path  = CAPTURES_DIR / f'{cap_id}.pcap'

    def _set(**kw):
        with CAPTURES_LOCK:
            CAPTURES[cap_id].update(kw)

    try:
        if is_self:
            # Local capture — narrow sudoers rule allows this.
            cmd = ['sudo', '-n', '/usr/bin/timeout', str(CAPTURE_SECS),
                   '/usr/bin/tcpdump', '-i', 'eth0',
                   '-w', remote_path, '-U', '-q']
            subprocess.run(cmd, capture_output=True, timeout=CAPTURE_SECS + 10)
            os.makedirs(CAPTURES_DIR, exist_ok=True)
            subprocess.run(['cp', remote_path, str(local_path)], check=True)
            subprocess.run(['rm', '-f', remote_path])
        else:
            # Remote: run tcpdump via SSH-as-otadmin, then scp the file back.
            ip = target['lab']
            tcpdump_cmd = (f'sudo timeout {CAPTURE_SECS} '
                           f'tcpdump -i eth0 -w {remote_path} -U -q')
            r = subprocess.run(SSH_BASE + [f'{SSH_USER}@{ip}', tcpdump_cmd],
                               capture_output=True, timeout=CAPTURE_SECS + 15)
            if r.returncode not in (0, 124):  # 124 = timeout's exit when interval elapses
                _set(status='failed',
                     err=f'tcpdump rc={r.returncode}: {r.stderr.decode(errors="ignore")[:200]}')
                return
            os.makedirs(CAPTURES_DIR, exist_ok=True)
            scp_cmd = ['scp', '-o', 'BatchMode=yes',
                       '-o', f'ControlPath={SSH_CTRL}',
                       f'{SSH_USER}@{ip}:{remote_path}', str(local_path)]
            subprocess.run(scp_cmd, capture_output=True, timeout=20, check=True)
            subprocess.run(SSH_BASE + [f'{SSH_USER}@{ip}', f'rm -f {remote_path}'],
                           capture_output=True, timeout=10)

        size = local_path.stat().st_size if local_path.exists() else 0
        _set(status='complete',
             finished=datetime.now().isoformat(timespec='seconds'),
             file=local_path.name,
             size=size)
    except Exception as e:
        _set(status='failed', err=f'{type(e).__name__}: {e}')


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------
@app.route('/')
@auth.login_required
def index():
    return render_template('index.html', user=auth.current_user())


@app.route('/api/status')
@auth.login_required
def api_status():
    with STATE_LOCK:
        return jsonify(STATE)


@app.route('/api/reboot/<host>', methods=['POST'])
@auth.login_required
def api_reboot(host):
    if host not in HOSTS or not HOSTS[host].get('reboot'):
        return jsonify({'ok': False, 'err': f'unknown host: {host}'}), 404
    print(f"[reboot] host={host} user={auth.current_user()}", flush=True)
    if HOSTS[host].get('self'):
        cmd = ['sudo', '-n', '/bin/systemctl', 'reboot']
    else:
        ip = HOSTS[host]['lab']
        cmd = SSH_BASE + [f'{SSH_USER}@{ip}', 'sudo systemctl reboot']
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({'ok': True, 'msg': f'reboot fired for {host}'})


# Per-host allowlist of services that the dashboard is allowed to bounce.
# Lets us avoid full Pi reboots when only a single service needs a kick.
RESTARTABLE_SVCS = {
    'softplc-1':     {'openplc'},
    'softplc-2':     {'sensor-sim', 'openplc', 'otlab-dashboard'},
    'honeypot-host': set(),  # docker compose handles its own restarts
}


@app.route('/api/restart/<host>/<svc>', methods=['POST'])
@auth.login_required
def api_restart_service(host, svc):
    if host not in HOSTS:
        return jsonify({'ok': False, 'err': f'unknown host: {host}'}), 404
    if svc not in RESTARTABLE_SVCS.get(host, set()):
        return jsonify({'ok': False,
                        'err': f'{svc} not restartable on {host}'}), 400

    user = auth.current_user()
    print(f"[restart] host={host} svc={svc} user={user}", flush=True)

    if HOSTS[host].get('self'):
        # Local restart via narrow sudoers rule (set up by install-dashboard.sh).
        cmd = ['sudo', '-n', '/bin/systemctl', 'restart', svc]
    else:
        ip = HOSTS[host]['lab']
        cmd = SSH_BASE + [f'{SSH_USER}@{ip}', f'sudo systemctl restart {svc}']

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        if r.returncode == 0:
            return jsonify({'ok': True, 'msg': f'{svc} restarted on {host}'})
        return jsonify({'ok': False,
                        'err': f'rc={r.returncode}: '
                               f'{r.stderr.decode(errors="ignore")[:200]}'}), 500
    except subprocess.TimeoutExpired:
        return jsonify({'ok': False, 'err': 'restart timed out'}), 504
    except Exception as e:
        return jsonify({'ok': False, 'err': f'{type(e).__name__}: {e}'}), 500


@app.route('/api/capture/<host>', methods=['POST'])
@auth.login_required
def api_capture(host):
    if host not in HOSTS:
        return jsonify({'ok': False, 'err': f'unknown host: {host}'}), 404
    cap_id = uuid.uuid4().hex[:12]
    with CAPTURES_LOCK:
        CAPTURES[cap_id] = {
            'id':       cap_id,
            'host':     host,
            'status':   'running',
            'started':  datetime.now().isoformat(timespec='seconds'),
            'duration': CAPTURE_SECS,
            'user':     auth.current_user(),
        }
    print(f"[capture] id={cap_id} host={host} user={auth.current_user()}", flush=True)
    threading.Thread(target=_do_capture, args=(cap_id, host), daemon=True).start()
    return jsonify({'ok': True, 'id': cap_id, 'duration': CAPTURE_SECS})


@app.route('/api/captures')
@auth.login_required
def api_captures():
    with CAPTURES_LOCK:
        # Most recent first
        ordered = sorted(CAPTURES.values(),
                         key=lambda c: c.get('started', ''), reverse=True)
    return jsonify({'captures': ordered[:20]})


@app.route('/api/capture-download/<cap_id>')
@auth.login_required
def api_capture_download(cap_id):
    with CAPTURES_LOCK:
        meta = CAPTURES.get(cap_id)
    if not meta or meta.get('status') != 'complete':
        abort(404)
    path = CAPTURES_DIR / meta['file']
    if not path.exists():
        abort(404)
    return send_file(str(path), as_attachment=True,
                     download_name=f'otlab-{meta["host"]}-{cap_id}.pcap',
                     mimetype='application/vnd.tcpdump.pcap')


# ---------------------------------------------------------------------------
# Entry.
# ---------------------------------------------------------------------------
def main():
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    threading.Thread(target=probe_loop, daemon=True).start()
    print(f"[otlab-dashboard] listening on https://0.0.0.0:{LISTEN_PORT}/ "
          f"(user={DASH_USER}, probe={PROBE_INTERVAL}s, health={HEALTH_INTERVAL}s)",
          flush=True)
    app.run(host='0.0.0.0', port=LISTEN_PORT,
            ssl_context=(DASH_CERT, DASH_KEY),
            threaded=True, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
