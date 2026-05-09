"""OTLab status dashboard — Flask app.

Runs on l3-mon-01 (the L3 monitoring host) as the `otuser` system user.
Probes every device on the lab in a background thread and exposes:

  GET  /                       — single-page HTML dashboard
  GET  /api/status             — JSON snapshot of latest probes (auth)
  POST /api/reboot/<host>      — issue `sudo systemctl reboot` on a Pi
  POST /api/restart/<svc>      — restart a service on l3-mon-01 self
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
import sqlite3
import struct
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
HEALTH_INTERVAL = float(os.environ.get('HEALTH_INTERVAL', '8.0'))
HEALTH_TIMEOUT  = float(os.environ.get('HEALTH_TIMEOUT',  '12.0'))
LISTEN_PORT     = int(os.environ.get('LISTEN_PORT',     '8000'))
SSH_USER        = os.environ.get('SSH_USER',            'otadmin')
DASH_CERT       = os.environ.get('DASH_CERT', '/home/otuser/lab/dashboard/cert.pem')
DASH_KEY        = os.environ.get('DASH_KEY',  '/home/otuser/lab/dashboard/key.pem')

# Sparkline ring-buffer length. 5 min @ 2.5 s/probe = 120 samples.
HISTORY_LEN     = int(os.environ.get('HISTORY_LEN', '120'))

# Pcap captures land here. Directory is created at startup.
CAPTURES_DIR    = Path(os.environ.get('CAPTURES_DIR', '/home/otuser/lab/dashboard/captures'))
CAPTURE_SECS    = int(os.environ.get('CAPTURE_SECS', '60'))

# Audit log — SQLite-backed, every mutation endpoint records here.
AUDIT_DB        = os.environ.get('AUDIT_DB', '/home/otuser/lab/dashboard/audit.db')

# sensor-sim fault-injection control endpoint. Dashboard lives on l3-mon-01
# (.49); sensor-sim moved to l1-plc-01 (.47) when softplc-2 was repurposed.
# So the control endpoint is on l1-plc-01's lab IP.
SENSOR_SIM_CTRL = os.environ.get('SENSOR_SIM_CTRL', 'http://10.20.30.47:5021/control')

# Lab credentials surfaced to the at-a-glance creds panel. All
# intentionally-public per project convention; rotate per DEF CON event.
WIFI_SSID         = os.environ.get('WIFI_SSID',         'MFCTP')
WIFI_PASS         = os.environ.get('WIFI_PASS',         'P@ssw0rd!')
OPENPLC_USER_NAME = os.environ.get('OPENPLC_USER_NAME', 'openplc')
OPENPLC_USER_PASS = os.environ.get('OPENPLC_USER_PASS', 'P@ssw0rd!')


# ---------------------------------------------------------------------------
# Topology — single source of truth for what we probe.
# ---------------------------------------------------------------------------
HOSTS = {
    'l1-plc-01':     {'lab': '10.20.30.47',  'mgmt': '192.168.120.216', 'reboot': True, 'self': False},
    'l3-mon-01':     {'lab': '10.20.30.49',  'mgmt': '192.168.120.19',  'reboot': True, 'self': True},
    'l1-hp-01': {'lab': '10.20.30.48',  'mgmt': '192.168.120.48',  'reboot': True, 'self': False},
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
#
# ControlPath lives under the dashboard's writable directory rather than
# in ~/.ssh/ because the systemd unit sets ProtectHome=read-only — SSH
# silently exits non-zero when it can't write its control socket. The
# directory is created by install-dashboard.sh.
SSH_CTRL = '/home/otuser/lab/dashboard/.ssh-cm/cm-%h-%p-%r'
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
# Audit log — append-only SQLite, every mutating dashboard action lands here.
# Browseable via /api/audit. Powers the "Audit Log" panel in the Live Data tab.
# ---------------------------------------------------------------------------
AUDIT_LOCK = threading.Lock()


def audit_init():
    Path(AUDIT_DB).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(AUDIT_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
              id        INTEGER PRIMARY KEY AUTOINCREMENT,
              ts        TEXT    NOT NULL,
              user      TEXT    NOT NULL,
              action    TEXT    NOT NULL,
              target    TEXT,
              params    TEXT,
              outcome   TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC)")


def audit(action, target=None, params=None, outcome='ok'):
    """Append one audit row. Best-effort — never raise into the request path."""
    try:
        user = auth.current_user() or 'anonymous'
        ts   = datetime.now().isoformat(timespec='seconds')
        p    = json.dumps(params)[:1000] if params else None
        with AUDIT_LOCK, sqlite3.connect(AUDIT_DB, timeout=2) as conn:
            conn.execute("INSERT INTO events (ts, user, action, target, params, outcome) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (ts, user, action, target, p, outcome))
    except Exception as e:
        print(f"[audit] {type(e).__name__}: {e}", flush=True)


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

# attack telemetry — failed SSH attempts in the last hour
failed_ssh=$(journalctl -u ssh -u sshd --since "1 hour ago" --no-pager 2>/dev/null \
              | grep -cE "Failed password|Invalid user|authentication failure")
[ -z "$failed_ssh" ] && failed_ssh=0

# tailscale identity / routing
ts_ip=$(tailscale ip -4 2>/dev/null | head -1 || echo "")
ts_online=$(systemctl is-active tailscaled 2>/dev/null || echo unknown)
ts_routes=$(tailscale debug prefs 2>/dev/null \
              | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); r=d.get('AdvertiseRoutes') or []
    print(','.join(r))
except: print('')" 2>/dev/null)
ts_hostname=$(tailscale status --self --json 2>/dev/null \
                | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('Self',{}).get('HostName',''))
except: print('')" 2>/dev/null)

# pending apt updates (just count of upgradable lines)
apt_pending=$(apt list --upgradable 2>/dev/null | tail -n +2 | grep -c .)
[ -z "$apt_pending" ] && apt_pending=0

# last-bootstrap metadata (written by bootstrap-* / install-* scripts)
bootstrap_ts=$(grep -E "^ts="     /etc/otlab-bootstrap-info 2>/dev/null | cut -d= -f2- || echo "")
bootstrap_commit=$(grep -E "^commit=" /etc/otlab-bootstrap-info 2>/dev/null | cut -d= -f2- || echo "")
bootstrap_script=$(grep -E "^script=" /etc/otlab-bootstrap-info 2>/dev/null | cut -d= -f2- || echo "")

# Single JSON line — built via python3 to avoid printf-quoting hell now
# that we have many string fields with arbitrary content.
python3 -c "
import json
print(json.dumps({
  'cpu': float('$cpu' or 0),
  'mem': float('$mem' or 0),
  'disk_pct': int('$disk_root' or 0),
  'disk_used': int('$disk_used_gb' or 0),
  'disk_size': int('$disk_size_gb' or 0),
  'temp': '$temp',
  'uptime': int('$uptime' or 0),
  'load1': float('$load1' or 0),
  'load5': float('$load5' or 0),
  'failed': int('$failed' or 0),
  'boot_dev': '$boot_dev',
  'failed_ssh_1h': int('$failed_ssh' or 0),
  'ts_ip': '$ts_ip',
  'ts_online': '$ts_online',
  'ts_routes': '$ts_routes',
  'ts_hostname': '$ts_hostname',
  'apt_pending': int('$apt_pending' or 0),
  'bootstrap_ts': '$bootstrap_ts',
  'bootstrap_commit': '$bootstrap_commit',
  'bootstrap_script': '$bootstrap_script',
}))
"
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
    """System-health for l3-mon-01 itself — no SSH overhead."""
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


def probe_modbus_rate():
    """Sniff l3-mon-01's lab interface for 1.5 s, count Modbus polls
    on the wire to/from l1-plc-01:5020.

    NOTE: during the l1-plc-02 backfill gap, sensor-sim runs on l1-plc-01
    itself and the master polls 127.0.0.1:5020 — that's loopback,
    invisible to Suricata or our wire sniff. Expect 0 pps. After
    l1-plc-02 backfills, polls return to the wire and pps climbs to
    ~20 (OpenPLC's 100 ms pause + alternating FC2/FC3).

    Uses sudo+tcpdump via the narrow sudoers rule install-dashboard.sh
    lays down. Falls back to None if anything goes wrong (e.g. interface
    name differs).
    """
    cmd = ['sudo', '-n', '/usr/bin/timeout', '1.5',
           '/usr/bin/tcpdump', '-i', 'eth0', '-nn', '-q',
           'tcp port 5020 and host 10.20.30.47']
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=3)
        # tcpdump puts the "listening on..." preamble on stderr and one
        # line per packet on stdout; count lines containing " > " which
        # is the IP-pair separator that's only on packet lines.
        lines = r.stdout.decode(errors='ignore').splitlines()
        pkts = sum(1 for L in lines if ' > ' in L)
        return round(pkts / 1.5, 1)
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
    """sudo-tail the conpot log on l1-hp-01. Returns list of lines."""
    script = f'sudo tail -n {HONEYPOT_LOG_TAIL} "{log_path}" 2>/dev/null || true'
    out, ok = _run_remote(f'{SSH_USER}@{HOSTS["l1-hp-01"]["lab"]}', script,
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
    'l1-plc-01': {'tank': deque(maxlen=HISTORY_LEN), 'temp': deque(maxlen=HISTORY_LEN), 'press': deque(maxlen=HISTORY_LEN)},
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
STATE = {'updated': None, 'cards': {}, 'health': {}, 'honeypot': {},
         'faults': {}, 'writes': {}, 'neighbors': [], 'scenario': None}
STATE_LOCK = threading.Lock()
LAST_HEALTH    = 0.0
LAST_HONEYPOT  = 0.0
LAST_NEIGHBORS = 0.0
LAST_SCENARIO  = 0.0
NEIGHBORS_INTERVAL = float(os.environ.get('NEIGHBORS_INTERVAL', '30'))
SCENARIO_INTERVAL  = float(os.environ.get('SCENARIO_INTERVAL', '20'))


# Loose OUI prefixes — enough to label common device types in the topology.
OUI_HINTS = {
    'b8:27:eb': 'Raspberry Pi (Foundation)',
    'dc:a6:32': 'Raspberry Pi 4',
    '2c:cf:67': 'Raspberry Pi 5',
    'd8:3a:dd': 'Raspberry Pi 4/CM4',
    '88:a2:9e': 'Raspberry Pi 5',
    'b4:fb:e4': 'TP-Link',
    'ac:8b:a9': 'TP-Link',
    'e0:d3:62': 'TP-Link / Mercury',
    '02:42:0a': 'Docker macvlan',
    '02:42':    'Docker container',
    '00:1c:42': 'Parallels VM',
    '08:00:27': 'VirtualBox VM',
    '00:50:56': 'VMware VM',
    '70:f8:ae': 'HP / Apple',
    '58:e6:c5': 'TP-Link / Lenovo',
    '20:4e:7f': 'Apple',
    'b0:e4:5c': 'Apple',
    '88:a4:c2': 'Intel',
}


def oui_vendor(mac: str) -> str:
    if not mac or len(mac) < 8:
        return ''
    mac = mac.lower()
    # Try the full 6-hex-digit prefix first, then the looser 4-hex-digit
    # prefix (catches Docker's 02:42:* range and similar).
    return (OUI_HINTS.get(mac[:8])
            or OUI_HINTS.get(mac[:5])
            or '')


def probe_neighbors():
    """Discover devices on the lab segment. Pings .1-.254 in parallel
    (cheap — ~1 s total since each is backgrounded) to force ARP
    resolution, then reads /proc/net/arp via `ip neigh`."""
    try:
        subprocess.run(
            ['bash', '-c',
             'for i in $(seq 1 254); do (ping -c1 -W1 10.20.30.$i >/dev/null 2>&1 &); done; wait'],
            timeout=6, capture_output=True,
        )
    except Exception:
        pass

    try:
        r = subprocess.run(['ip', 'neigh', 'show', 'dev', 'eth0'],
                           capture_output=True, text=True, timeout=3)
    except Exception:
        return []

    neighbors = []
    for line in r.stdout.splitlines():
        # "10.20.30.47 lladdr 2c:cf:67:4f:d3:09 REACHABLE"  (often with
        # trailing whitespace, hence rstrip rather than relying on $).
        m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+lladdr\s+([0-9a-f:]+)\s+(\S+)', line.rstrip())
        if not m:
            continue
        ip, mac, state = m.groups()
        if state == 'FAILED':
            continue
        if not ip.startswith('10.20.30.'):
            continue
        neighbors.append({
            'ip':     ip,
            'mac':    mac,
            'state':  state,
            'vendor': oui_vendor(mac),
        })
    neighbors.sort(key=lambda n: tuple(int(x) for x in n['ip'].split('.')))
    return neighbors


# ---------------------------------------------------------------------------
# Sensor-sim fault control — proxies to sensor-sim's HTTP control endpoint.
# ---------------------------------------------------------------------------
def _sensor_sim_get(url=None):
    try:
        with urllib.request.urlopen(url or SENSOR_SIM_CTRL, timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# sensor-sim sibling endpoints (alongside /control)
SENSOR_SIM_WRITES   = SENSOR_SIM_CTRL.rsplit('/', 1)[0] + '/writes'
SENSOR_SIM_SCENARIO = SENSOR_SIM_CTRL.rsplit('/', 1)[0] + '/scenario'


def _sensor_sim_post(url, payload):
    try:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, method='POST',
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[sensor-sim-ctrl] {type(e).__name__}: {e}", flush=True)
        return None


def probe_fast():
    """Network + Modbus + HTTP — runs every PROBE_INTERVAL."""
    cards = {}

    # --- Network sanity row ---
    cards['wan']     = {**ping('1.1.1.1'),       'label': 'WAN (1.1.1.1)',      'group': 'net'}
    cards['mgmt_gw'] = {**ping('192.168.120.1'), 'label': 'Mgmt Gateway',       'group': 'net'}
    cards['fw']      = {**ping('10.20.30.1'),    'label': 'Firewall (TP-Link)', 'group': 'net'}

    # --- l1-plc-01 (master + sensor-sim outstation + DNP3 outstation) ---
    s1 = HOSTS['l1-plc-01']
    s1c = ping(s1['lab'])
    s1c.update({
        'label':  'l1-plc-01 — master + sensor-sim + DNP3',
        'group':  'plc',
        'plc_ui': http_probe(f"http://{s1['lab']}:8080/login"),
        'modbus': modbus_probe(s1['lab'], 5020, hr_count=4, coil_count=2),  # sensor-sim
        'modbus_master': modbus_probe(s1['lab'], 502, hr_count=6, coil_count=2),  # OpenPLC mirror
        'dnp3':   tcp_probe(s1['lab'], 20000),
        'reboot': True,
    })
    # History tracks sensor-sim's holding registers (the process-data sparkline).
    if s1c['modbus']:
        _push_history('l1-plc-01', s1c['modbus']['hr'])
    s1c['history'] = _history_dict('l1-plc-01')
    cards['l1-plc-01'] = s1c

    # --- l3-mon-01 (this host — monitoring; no PLC services) ---
    s2 = HOSTS['l3-mon-01']
    s2c = ping(s2['lab'])
    s2c.update({
        'label':  'l3-mon-01 — dashboard + Suricata + Guacamole',
        'group':  'mon',
        'reboot': True,
    })
    cards['l3-mon-01'] = s2c

    # --- l1-hp-01 ---
    hh = HOSTS['l1-hp-01']
    hhc = ping(hh['lab'])
    hhc.update({
        'label':  'l1-hp-01 — Conpot fabric',
        'group':  'plc',
        'reboot': True,
    })
    cards['l1-hp-01'] = hhc

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
    h['l3-mon-01']     = health_local()
    h['l1-plc-01']     = health_remote(HOSTS['l1-plc-01']['lab'])
    h['l1-hp-01']      = health_remote(HOSTS['l1-hp-01']['lab'])

    # Attach the lab-segment Modbus poll rate to l1-plc-01's health card
    # (that's where sensor-sim lives; until l1-plc-02 backfills, polls are
    # loopback and the rate will read 0). After backfill the rate jumps
    # to the master's actual cadence, ~20 pps.
    rate = probe_modbus_rate()
    if h.get('l1-plc-01') is not None and rate is not None:
        h['l1-plc-01']['modbus_pps_in'] = rate
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
    global LAST_HEALTH, LAST_HONEYPOT, LAST_NEIGHBORS, LAST_SCENARIO
    while True:
        try:
            cards = probe_fast()
            faults = _sensor_sim_get() or {}
            writes = _sensor_sim_get(SENSOR_SIM_WRITES) or {}

            now = time.time()
            with STATE_LOCK:
                health    = STATE.get('health', {})
                honeypot  = STATE.get('honeypot', {})
                neighbors = STATE.get('neighbors', [])
                scenario  = STATE.get('scenario')

            if now - LAST_HEALTH >= HEALTH_INTERVAL:
                health = probe_health()
                LAST_HEALTH = now
            if now - LAST_HONEYPOT >= HEALTH_INTERVAL:
                honeypot = probe_honeypot()
                LAST_HONEYPOT = now
            if now - LAST_NEIGHBORS >= NEIGHBORS_INTERVAL:
                neighbors = probe_neighbors()
                LAST_NEIGHBORS = now
            if now - LAST_SCENARIO >= SCENARIO_INTERVAL:
                s = _sensor_sim_get(SENSOR_SIM_SCENARIO)
                if s is not None:
                    scenario = s
                LAST_SCENARIO = now

            with STATE_LOCK:
                STATE['updated']   = datetime.now().isoformat(timespec='seconds')
                STATE['cards']     = cards
                STATE['health']    = health
                STATE['honeypot']  = honeypot
                STATE['faults']    = faults
                STATE['writes']    = writes
                STATE['neighbors'] = neighbors
                STATE['scenario']  = scenario
        except Exception as e:
            print(f"[probe-loop] {type(e).__name__}: {e}", flush=True)
        time.sleep(PROBE_INTERVAL)


# ---------------------------------------------------------------------------
# Real-time Modbus wire view — long-running tcpdump emits each packet's
# raw hex; a parser thread decodes Modbus frames and pushes them into a
# bounded deque. SSE endpoint streams new frames to the browser as they
# arrive. Decoding is pure stdlib (struct on raw bytes).
# ---------------------------------------------------------------------------
WIRE_FEED       = deque(maxlen=200)
WIRE_FEED_LOCK  = threading.Lock()
WIRE_NEW_EVENT  = threading.Event()  # poked when a new frame is appended

FC_NAMES = {1: 'FC1 read coils', 2: 'FC2 read disc inp',
            3: 'FC3 read HR',    4: 'FC4 read input',
            5: 'FC5 write coil', 6: 'FC6 write reg',
           15: 'FC15 write coils', 16: 'FC16 write regs'}


def _decode_modbus(payload: bytes):
    """Try to extract MBAP+PDU from a raw IPv4+TCP+payload chunk. Returns
    a dict with src/dst/fc/addr/data or None if it doesn't look Modbus.

    Min payload: IP header 20 + TCP header 20 + MBAP 7 = 47 bytes.
    tcpdump's -x output (without -e) starts at the IP header — no eth."""
    if len(payload) < 47:
        return None
    try:
        ip_hl = (payload[0] & 0x0F) * 4
        if payload[9] != 6:
            return None
        src = '.'.join(str(b) for b in payload[12:16])
        dst = '.'.join(str(b) for b in payload[16:20])
        tcp = payload[ip_hl:]
        if len(tcp) < 20:
            return None
        sport, dport = struct.unpack('>HH', tcp[0:4])
        tcp_hl = (tcp[12] >> 4) * 4
        mbap = tcp[tcp_hl:]
        if len(mbap) < 8:
            return None
        tx_id, proto_id, length, unit_id = struct.unpack('>HHHB', mbap[:7])
        if proto_id != 0 or length < 2 or length > 260:
            return None
        pdu = mbap[7:7 + length - 1]
        if not pdu:
            return None
        fc = pdu[0]
        if fc not in FC_NAMES and fc not in (0x83, 0x86, 0x90):
            return None
        out = {
            't':    datetime.now().strftime('%H:%M:%S.%f')[:-3],
            'src':  f'{src}:{sport}',
            'dst':  f'{dst}:{dport}',
            'fc':   fc,
            'name': FC_NAMES.get(fc & 0x7F, f'FC{fc}'),
        }
        # Decode common request/response shapes
        if fc in (1, 2, 3, 4) and len(pdu) >= 5:
            address, count = struct.unpack('>HH', pdu[1:5])
            out['addr'], out['count'] = address, count
        elif fc in (5, 6) and len(pdu) >= 5:
            address, value = struct.unpack('>HH', pdu[1:5])
            out['addr'], out['value'] = address, value
        elif fc in (3, 4) and len(pdu) >= 2 and pdu[1] >= 2:
            # response side: byte_count followed by N×2 bytes
            bc = pdu[1]
            regs = list(struct.unpack(f'>{bc//2}H', pdu[2:2+bc])) if bc % 2 == 0 else []
            if regs:
                out['regs'] = regs[:8]
        return out
    except Exception:
        return None


def _wire_capture_thread():
    """Long-running tcpdump on l3-mon-01's eth0, parsing each Modbus frame
    and pushing into WIRE_FEED. Restarts tcpdump on failure.

    During the l1-plc-02 backfill gap, master ↔ sensor-sim traffic is
    loopback on l1-plc-01 — invisible here. Only attacker writes from
    other hosts (or the dashboard itself) appear on the wire. Post-
    backfill, the legitimate poll cadence returns to the wire."""
    while True:
        try:
            p = subprocess.Popen(
                ['sudo', '-n', '/usr/bin/tcpdump', '-i', 'eth0', '-nn', '-l',
                 '-x', '-tttt', '-s', '256',
                 'tcp port 5020 or tcp port 502'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=1, text=True,
            )
            current_hex = []
            for line in p.stdout:
                if line.startswith('\t') or line.startswith('    ') or line.startswith('  '):
                    parts = line.strip().split()
                    for tok in parts:
                        if len(tok) in (4, 8) and all(c in '0123456789abcdef' for c in tok):
                            current_hex.append(tok)
                else:
                    if current_hex:
                        try:
                            raw = bytes.fromhex(''.join(current_hex))
                            frame = _decode_modbus(raw)
                            if frame:
                                with WIRE_FEED_LOCK:
                                    WIRE_FEED.append(frame)
                                WIRE_NEW_EVENT.set()
                        except Exception:
                            pass
                    current_hex = []
        except Exception as e:
            print(f"[wire-capture] {type(e).__name__}: {e}", flush=True)
        time.sleep(2)


@app.route('/api/wire/recent')
@auth.login_required
def api_wire_recent():
    """Snapshot of the most recent wire frames — useful for initial fill
    when the page loads (before SSE catches up)."""
    with WIRE_FEED_LOCK:
        return jsonify({'frames': list(WIRE_FEED)[-50:]})


@app.route('/api/wire/stream')
@auth.login_required
def api_wire_stream():
    """SSE feed of decoded Modbus frames. Each event is one frame as JSON."""
    from flask import Response

    def generate():
        last_idx = 0
        while True:
            WIRE_NEW_EVENT.wait(timeout=10)
            WIRE_NEW_EVENT.clear()
            with WIRE_FEED_LOCK:
                snap = list(WIRE_FEED)
            # Stream all new frames since last tick. The deque may have
            # rolled over but we accept that — better to send a few extra
            # than stall the stream.
            for frame in snap[-20:]:
                yield f'data: {json.dumps(frame)}\n\n'

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


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
        audit('reboot', host, None, 'rejected:unknown-host')
        return jsonify({'ok': False, 'err': f'unknown host: {host}'}), 404
    print(f"[reboot] host={host} user={auth.current_user()}", flush=True)
    if HOSTS[host].get('self'):
        cmd = ['sudo', '-n', '/bin/systemctl', 'reboot']
    else:
        ip = HOSTS[host]['lab']
        cmd = SSH_BASE + [f'{SSH_USER}@{ip}', 'sudo systemctl reboot']
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    audit('reboot', host, None, 'fired')
    return jsonify({'ok': True, 'msg': f'reboot fired for {host}'})


@app.route('/api/audit')
@auth.login_required
def api_audit():
    """Last N audit events. Filterable by action= or user=. Read-only."""
    from flask import request
    limit  = min(500, int(request.args.get('limit', 100)))
    action = request.args.get('action')
    user   = request.args.get('user')
    sql = "SELECT id, ts, user, action, target, params, outcome FROM events"
    where, params = [], []
    if action: where.append("action = ?"); params.append(action)
    if user:   where.append("user = ?");   params.append(user)
    if where:  sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"; params.append(limit)
    try:
        with sqlite3.connect(AUDIT_DB, timeout=2) as conn:
            rows = conn.execute(sql, params).fetchall()
        events = [{'id': r[0], 'ts': r[1], 'user': r[2], 'action': r[3],
                   'target': r[4], 'params': r[5], 'outcome': r[6]}
                  for r in rows]
        return jsonify({'events': events, 'count': len(events)})
    except Exception as e:
        return jsonify({'events': [], 'err': f'{type(e).__name__}: {e}'}), 500


# Per-host allowlist of services that the dashboard is allowed to bounce.
# Lets us avoid full Pi reboots when only a single service needs a kick.
RESTARTABLE_SVCS = {
    'l1-plc-01':     {'openplc', 'sensor-sim', 'dnp3-outstation'},
    'l3-mon-01':     {'otlab-dashboard', 'suricata'},
    'l1-hp-01':      set(),  # docker compose handles its own restarts
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
        cmd = ['sudo', '-n', '/bin/systemctl', 'restart', svc]
    else:
        ip = HOSTS[host]['lab']
        cmd = SSH_BASE + [f'{SSH_USER}@{ip}', f'sudo systemctl restart {svc}']

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        if r.returncode == 0:
            audit('restart', f'{host}/{svc}', None, 'ok')
            return jsonify({'ok': True, 'msg': f'{svc} restarted on {host}'})
        audit('restart', f'{host}/{svc}', None, f'fail rc={r.returncode}')
        return jsonify({'ok': False,
                        'err': f'rc={r.returncode}: '
                               f'{r.stderr.decode(errors="ignore")[:200]}'}), 500
    except subprocess.TimeoutExpired:
        audit('restart', f'{host}/{svc}', None, 'timeout')
        return jsonify({'ok': False, 'err': 'restart timed out'}), 504
    except Exception as e:
        audit('restart', f'{host}/{svc}', None, f'exc:{type(e).__name__}')
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
    audit('capture', host, {'id': cap_id, 'duration': CAPTURE_SECS}, 'started')
    threading.Thread(target=_do_capture, args=(cap_id, host), daemon=True).start()
    return jsonify({'ok': True, 'id': cap_id, 'duration': CAPTURE_SECS})


# ---------------------------------------------------------------------------
# Fault injection — POST proxies to sensor-sim's /control endpoint.
# ---------------------------------------------------------------------------
INJECT_KEYS = {'paused', 'hb_paused', 'force_alarm'}


# ---------------------------------------------------------------------------
# Test library — discovers scripts in ~/lab/tests/, exposes them by name,
# runs them on demand and captures output. The first comment-block of each
# script is parsed as its description.
# ---------------------------------------------------------------------------
TESTS_DIR = Path('/home/otuser/lab/tests')


def _discover_tests():
    if not TESTS_DIR.is_dir():
        return []
    out = []
    for p in sorted(TESTS_DIR.glob('test-*')):
        if p.suffix not in ('.py', '.sh'):
            continue
        # First docstring or comment block as description
        desc = ''
        try:
            text = p.read_text(errors='ignore')
            if p.suffix == '.py':
                if text.startswith('#!'):
                    text = text.split('\n', 1)[1] if '\n' in text else ''
                if '"""' in text:
                    desc = text.split('"""', 2)[1].strip().split('\n\n')[0]
                elif "'''" in text:
                    desc = text.split("'''", 2)[1].strip().split('\n\n')[0]
            else:
                # bash: pull leading "# ..." block (skip shebang)
                lines = text.splitlines()
                start = 1 if lines and lines[0].startswith('#!') else 0
                doc = []
                for line in lines[start:]:
                    if line.startswith('#'):
                        doc.append(line.lstrip('#').strip())
                    elif line.strip() == '':
                        if doc:
                            break
                    else:
                        break
                desc = '\n'.join(doc).strip()
        except Exception:
            pass
        out.append({
            'id':   p.stem,
            'name': p.stem.replace('test-', '').replace('-', ' '),
            'kind': p.suffix.lstrip('.'),
            'path': str(p),
            'desc': desc[:500],
        })
    return out


# Last-result cache so the UI can re-show recent runs.
TEST_RESULTS = {}
TEST_RESULTS_LOCK = threading.Lock()


@app.route('/api/tests')
@auth.login_required
def api_tests():
    with TEST_RESULTS_LOCK:
        results = dict(TEST_RESULTS)
    return jsonify({'tests': _discover_tests(), 'last_results': results})


@app.route('/api/tests/run/<test_id>', methods=['POST'])
@auth.login_required
def api_tests_run(test_id):
    """Run a test script as otuser via local sudo. Captures stdout/stderr
    and returncode. Times out at 60 s."""
    # Defense in depth — only allow IDs we just discovered, no path injection.
    valid = {t['id']: t for t in _discover_tests()}
    if test_id not in valid:
        return jsonify({'ok': False, 'err': f'unknown test {test_id}'}), 404
    t = valid[test_id]
    user = auth.current_user()
    print(f"[tests] run id={test_id} user={user}", flush=True)

    if t['kind'] == 'py':
        cmd = ['/home/otuser/lab/.venv-modern/bin/python3', t['path']]
    else:  # sh
        cmd = ['/bin/bash', t['path']]

    started = datetime.now().isoformat(timespec='seconds')
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        outcome = {
            'id':         test_id,
            'started':    started,
            'finished':   datetime.now().isoformat(timespec='seconds'),
            'returncode': r.returncode,
            'stdout':     r.stdout.decode(errors='ignore')[-8000:],
            'stderr':     r.stderr.decode(errors='ignore')[-2000:],
            'user':       user,
        }
    except subprocess.TimeoutExpired:
        outcome = {'id': test_id, 'started': started, 'finished': None,
                   'returncode': -1, 'stdout': '', 'stderr': '(test timed out after 60 s)',
                   'user': user}
    except Exception as e:
        outcome = {'id': test_id, 'started': started, 'finished': None,
                   'returncode': -2, 'stdout': '', 'stderr': f'{type(e).__name__}: {e}',
                   'user': user}

    with TEST_RESULTS_LOCK:
        TEST_RESULTS[test_id] = outcome
    audit('test-run', test_id, None, f'rc={outcome.get("returncode")}')
    return jsonify({'ok': True, 'result': outcome})


@app.route('/api/scenario')
@auth.login_required
def api_scenario():
    """The active sensor-sim scenario (waveforms + thresholds + risks +
    walkthroughs + regulatory tags). Refreshed every SCENARIO_INTERVAL s."""
    with STATE_LOCK:
        return jsonify({'scenario': STATE.get('scenario')})


@app.route('/api/neighbors')
@auth.login_required
def api_neighbors():
    with STATE_LOCK:
        return jsonify({'neighbors': STATE.get('neighbors', []),
                        'updated': STATE.get('updated')})


@app.route('/api/creds')
@auth.login_required
def api_creds():
    """At-a-glance lab credentials for booth ops. All intentionally-public
    per project convention; rotate per DEF CON event. Auth required so
    they're not just sitting on a public endpoint."""
    return jsonify({
        'wifi':      {'label': 'Lab WiFi (MFCTP)',
                      'username': WIFI_SSID,
                      'password': WIFI_PASS,
                      'note':     'SSID broadcast on the booth AP. Bridges onto 10.20.30.0/24.'},
        'openplc':   {'label': 'OpenPLC web UI',
                      'username': OPENPLC_USER_NAME,
                      'password': OPENPLC_USER_PASS,
                      'note':     f'l1-plc-01 at http://{HOSTS["l1-plc-01"]["lab"]}:8080/  (l1-plc-02 future backfill)'},
        'dashboard': {'label': 'OTLab Dashboard',
                      'username': DASH_USER,
                      'password': DASH_PASS,
                      'note':     'this dashboard, basic-auth + self-signed TLS'},
    })


@app.route('/api/inject', methods=['POST'])
@auth.login_required
def api_inject():
    """Set fault flags. Body is a JSON object like {"paused": true}.
    Returns the new fault state from sensor-sim."""
    from flask import request
    payload = request.get_json(silent=True) or {}
    cleaned = {k: bool(v) for k, v in payload.items() if k in INJECT_KEYS}
    if not cleaned:
        return jsonify({'ok': False, 'err': 'no recognized keys (paused/hb_paused/force_alarm)'}), 400
    print(f"[inject] {cleaned} user={auth.current_user()}", flush=True)
    new_state = _sensor_sim_post(SENSOR_SIM_CTRL, cleaned)
    if new_state is None:
        audit('inject', None, cleaned, 'sensor-sim-unreachable')
        return jsonify({'ok': False, 'err': 'sensor-sim unreachable'}), 503
    audit('inject', None, cleaned, 'ok')
    return jsonify({'ok': True, 'state': new_state})


@app.route('/api/inject/clear', methods=['POST'])
@auth.login_required
def api_inject_clear():
    print(f"[inject] CLEAR user={auth.current_user()}", flush=True)
    new_state = _sensor_sim_post(SENSOR_SIM_CTRL + '/reset', {})
    if new_state is None:
        audit('inject-clear', None, None, 'sensor-sim-unreachable')
        return jsonify({'ok': False, 'err': 'sensor-sim unreachable'}), 503
    audit('inject-clear', None, None, 'ok')
    return jsonify({'ok': True, 'state': new_state})


# ---------------------------------------------------------------------------
# Modbus write playground — issue real FC5/FC6 (and FC15/FC16) writes against
# sensor-sim or l1-plc-01's :502 mirror. Demonstrates the "Modbus has no
# auth" teaching lesson — anything on the wire can change process state.
# ---------------------------------------------------------------------------
WRITE_TARGETS = {
    'l1-plc-01-sensor-sim': {'host': '10.20.30.47', 'port': 5020,
                             'label': 'sensor-sim @ l1-plc-01:5020',
                             'note':  'persistent override — value sticks until cleared'},
    'l1-plc-01-mirror':     {'host': '10.20.30.47', 'port': 502,
                             'label': 'l1-plc-01 :502 mirror',
                             'note':  'ephemeral — overwritten on next OpenPLC scan'},
}


@app.route('/api/write/targets')
@auth.login_required
def api_write_targets():
    return jsonify({'targets': WRITE_TARGETS})


@app.route('/api/write', methods=['POST'])
@auth.login_required
def api_write():
    """Issue a Modbus write to one of the lab's slaves. Body:
       {target: <key from WRITE_TARGETS>, kind: 'coil'|'reg',
        addr: <int>, value: <int|bool>}"""
    from flask import request
    p = request.get_json(silent=True) or {}
    target = p.get('target')
    kind   = p.get('kind')
    addr   = p.get('addr')
    value  = p.get('value')

    if target not in WRITE_TARGETS:
        return jsonify({'ok': False, 'err': f'unknown target: {target}'}), 400
    if kind not in ('coil', 'reg'):
        return jsonify({'ok': False, 'err': 'kind must be coil or reg'}), 400
    if not isinstance(addr, int) or not (0 <= addr < 256):
        return jsonify({'ok': False, 'err': 'addr must be int 0-255'}), 400

    t = WRITE_TARGETS[target]
    user = auth.current_user()
    print(f"[write] target={target} kind={kind} addr={addr} value={value!r} user={user}", flush=True)

    try:
        c = ModbusTcpClient(t['host'], port=t['port'], timeout=2)
        if not c.connect():
            return jsonify({'ok': False, 'err': f'cannot connect to {t["host"]}:{t["port"]}'}), 503

        if kind == 'coil':
            r = c.write_coil(address=addr, value=bool(value), device_id=0 if t['port'] == 502 else 1)
        else:
            r = c.write_register(address=addr, value=int(value) & 0xFFFF, device_id=0 if t['port'] == 502 else 1)
        c.close()

        if r.isError():
            audit('modbus-write', target, {'kind':kind,'addr':addr,'value':value}, f'modbus-err:{r}')
            return jsonify({'ok': False, 'err': f'modbus error: {r}'}), 502
        audit('modbus-write', target, {'kind':kind,'addr':addr,'value':value}, 'ok')
        return jsonify({'ok': True,
                        'target': target, 'kind': kind, 'addr': addr, 'value': value,
                        'note': t['note']})
    except Exception as e:
        audit('modbus-write', target, {'kind':kind,'addr':addr,'value':value}, f'exc:{type(e).__name__}')
        return jsonify({'ok': False, 'err': f'{type(e).__name__}: {e}'}), 500


@app.route('/api/write/clear', methods=['POST'])
@auth.login_required
def api_write_clear():
    """Clear sensor-sim's persistent write-overrides. Doesn't touch
    l1-plc-01's mirror (it overwrites itself anyway)."""
    print(f"[write] CLEAR user={auth.current_user()}", flush=True)
    r = _sensor_sim_post(SENSOR_SIM_WRITES + '/reset', {})
    if r is None:
        return jsonify({'ok': False, 'err': 'sensor-sim unreachable'}), 503
    return jsonify({'ok': True, 'cleared': r})


@app.route('/api/cohort/reset', methods=['POST'])
@auth.login_required
def api_cohort_reset():
    """Reset the lab to a known-clean state for the next cohort/student.

    Steps (each best-effort, individual failures don't abort the rest):
      1. Clear all sensor-sim fault injections + persistent writes
      2. Delete all stored pcap captures
      3. Restart sensor-sim (fresh heartbeat, fresh waveforms)
      4. Restart openplc on l1-plc-01 (fresh ST runtime, link_loss=0)

    The dashboard service itself is NOT restarted — the user clicking the
    button needs to see the result come back."""
    user = auth.current_user()
    print(f"[cohort-reset] user={user}", flush=True)
    audit('cohort-reset', None, None, 'started')
    results = []

    # 1. Clear sensor-sim fault state + persistent overrides (best-effort)
    s = _sensor_sim_post(SENSOR_SIM_CTRL + '/reset', {})
    results.append(('clear-faults', s is not None))

    # Sensor-sim's /control/reset clears fault flags. Writes (Modbus FC5/6
    # overrides) are cleared with /writes/reset (added below).
    w = _sensor_sim_post(os.environ.get('SENSOR_SIM_WRITES', 'http://127.0.0.1:5021/writes/reset'), {})
    results.append(('clear-writes', w is not None))

    # 2. Delete pcap captures from local disk + clear in-memory metadata
    deleted = 0
    try:
        for f in CAPTURES_DIR.glob('*.pcap'):
            try: f.unlink(); deleted += 1
            except Exception: pass
        with CAPTURES_LOCK:
            CAPTURES.clear()
        results.append(('delete-pcaps', deleted))
    except Exception as e:
        results.append(('delete-pcaps', f'err: {e}'))

    # 3. Restart sensor-sim on l1-plc-01 (sensor-sim host)
    try:
        cmd = SSH_BASE + [f'{SSH_USER}@{HOSTS["l1-plc-01"]["lab"]}',
                          'sudo systemctl restart sensor-sim']
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        results.append(('restart-sensor-sim', r.returncode == 0))
    except Exception as e:
        results.append(('restart-sensor-sim', f'err: {e}'))

    # 4. Restart OpenPLC on l1-plc-01 (fresh link-liveness counters)
    try:
        cmd = SSH_BASE + [f'{SSH_USER}@{HOSTS["l1-plc-01"]["lab"]}',
                          'sudo systemctl restart openplc']
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        results.append(('restart-openplc-s1', r.returncode == 0))
    except Exception as e:
        results.append(('restart-openplc-s1', f'err: {e}'))

    audit('cohort-reset', None, None, json.dumps(results)[:500])
    return jsonify({'ok': True, 'steps': results})


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
    audit_init()
    threading.Thread(target=probe_loop, daemon=True).start()
    threading.Thread(target=_wire_capture_thread, daemon=True,
                     name='wire-capture').start()
    print(f"[otlab-dashboard] listening on https://0.0.0.0:{LISTEN_PORT}/ "
          f"(user={DASH_USER}, probe={PROBE_INTERVAL}s, health={HEALTH_INTERVAL}s)",
          flush=True)
    app.run(host='0.0.0.0', port=LISTEN_PORT,
            ssl_context=(DASH_CERT, DASH_KEY),
            threaded=True, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
