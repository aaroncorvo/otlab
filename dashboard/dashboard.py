"""OTLab status dashboard — Flask app.

Runs on softplc-2 as the `otuser` system user. Probes every device on the
lab in a background thread and exposes:

  GET  /                  — single-page HTML dashboard
  GET  /api/status        — JSON snapshot of latest probes (basic-auth)
  POST /api/reboot/<host> — issue `sudo systemctl reboot` on a Pi (basic-auth)

Defaults:
  HTTPS on port 8000 with the self-signed cert/key the install script
  generated. Basic auth user `otlab` / pass `P@ssw0rd!` (override via
  DASH_USER / DASH_PASS env vars in dashboard.env).

Lab is intentionally a teaching environment — auth here is to keep booth
visitors from accidentally mashing the Reboot button, not to keep
determined attackers out. Hackers gonna hack.
"""

import os
import socket
import subprocess
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime

from flask import Flask, jsonify, render_template
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

from pymodbus.client import ModbusTcpClient


# ---------------------------------------------------------------------------
# Config — env-overridable, defaults baked in.
# ---------------------------------------------------------------------------
DASH_USER       = os.environ.get('DASH_USER', 'otlab')
DASH_PASS       = os.environ.get('DASH_PASS', 'P@ssw0rd!')
PROBE_INTERVAL  = float(os.environ.get('PROBE_INTERVAL', '2.5'))   # seconds
PROBE_TIMEOUT   = float(os.environ.get('PROBE_TIMEOUT',  '1.5'))   # per-probe budget
LISTEN_PORT     = int(os.environ.get('LISTEN_PORT',     '8000'))
SSH_USER        = os.environ.get('SSH_USER',            'otadmin')
DASH_CERT       = os.environ.get('DASH_CERT', '/home/otuser/lab/dashboard/cert.pem')
DASH_KEY        = os.environ.get('DASH_KEY',  '/home/otuser/lab/dashboard/key.pem')


# ---------------------------------------------------------------------------
# Topology — single source of truth for what we probe.
# ---------------------------------------------------------------------------
HOSTS = {
    'softplc-1':     {'lab': '10.20.30.47',  'mgmt': '192.168.120.216', 'reboot': True, 'self': False},
    'softplc-2':     {'lab': '10.20.30.49',  'mgmt': '192.168.120.19',  'reboot': True, 'self': True},   # self-reboot uses sudoers
    'honeypot-host': {'lab': '10.20.30.48',  'mgmt': '192.168.120.48',  'reboot': True, 'self': False},
}
CONPOTS = {
    # Per honeypot/README.md: each persona advertises its vendor's protocol mix.
    'siemens-PS4':       {'ip': '10.20.30.50', 'tcp_ports': [80, 102]},     # HTTP + S7comm
    'schneider-M340':    {'ip': '10.20.30.51', 'tcp_ports': [80, 502]},     # HTTP + Modbus
    'rockwell-CHEM':     {'ip': '10.20.30.52', 'tcp_ports': [80, 44818]},   # HTTP + EtherNet/IP
}


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
# Probe primitives — every function returns within PROBE_TIMEOUT (give or
# take); a hung host can't stall the whole loop.
# ---------------------------------------------------------------------------
def ping(host, timeout=PROBE_TIMEOUT):
    """ICMP ping. Returns {'up': bool, 'ms': float|None}."""
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(max(1, int(timeout))), host],
            capture_output=True,
            timeout=timeout + 0.5,
        )
        if result.returncode == 0:
            for line in result.stdout.decode(errors='ignore').splitlines():
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
    """True if we can open a TCP socket to host:port within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def http_probe(url, timeout=PROBE_TIMEOUT):
    """True if HEAD on url returns a status (any status — server is up)."""
    try:
        req = urllib.request.Request(url, method='HEAD')
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        # 4xx/5xx still means the server is alive
        return True
    except Exception:
        return False


def modbus_probe(host, port, hr_count=4, coil_count=2, timeout=PROBE_TIMEOUT):
    """Modbus TCP read of holding registers + coils. Returns dict or None."""
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
# Probe orchestration — background thread updates STATE every PROBE_INTERVAL
# seconds. HTTP requests just read the cached snapshot, so 10 viewers don't
# DDoS the lab.
# ---------------------------------------------------------------------------
STATE = {'updated': None, 'cards': {}}
STATE_LOCK = threading.Lock()


def probe_all():
    cards = {}

    # --- Network sanity row ---
    cards['wan']     = {**ping('1.1.1.1'),       'label': 'WAN (1.1.1.1)',      'group': 'net'}
    cards['mgmt_gw'] = {**ping('192.168.120.1'), 'label': 'Mgmt Gateway',       'group': 'net'}
    cards['fw']      = {**ping('10.20.30.1'),    'label': 'Firewall (TP-Link)', 'group': 'net'}

    # --- softplc-1 (Modbus master, lives on lab segment) ---
    s1 = HOSTS['softplc-1']
    s1c = ping(s1['lab'])
    s1c.update({
        'label':  'softplc-1 — RASPLC01 (PLC master)',
        'group':  'plc',
        'plc_ui': http_probe(f"http://{s1['lab']}:8080/login"),
        'modbus': modbus_probe(s1['lab'], 502, hr_count=6, coil_count=2),
        'reboot': True,
    })
    cards['softplc-1'] = s1c

    # --- softplc-2 (sensor-sim slave, runs the dashboard) ---
    s2 = HOSTS['softplc-2']
    s2c = ping(s2['lab'])
    s2c.update({
        'label':  'softplc-2 — RASPLC02 (sensor-sim)',
        'group':  'plc',
        'plc_ui': http_probe(f"http://{s2['lab']}:8080/login"),
        'modbus': modbus_probe(s2['lab'], 5020, hr_count=4, coil_count=2),
        'reboot': True,
    })
    cards['softplc-2'] = s2c

    # --- honeypot-host (Conpot fabric) ---
    hh = HOSTS['honeypot-host']
    hhc = ping(hh['lab'])
    hhc.update({
        'label':  'honeypot-host — Conpot fabric',
        'group':  'plc',
        'reboot': True,
    })
    cards['honeypot-host'] = hhc

    # --- Conpot personas (TCP probes, not pings — Conpot containers are
    # macvlan'd so they answer pings unreliably; service ports are the
    # authoritative liveness signal) ---
    for name, c in CONPOTS.items():
        cc = ping(c['ip'])  # informational
        cc.update({
            'label': name,
            'group': 'honeypot',
            'svcs':  {p: tcp_probe(c['ip'], p) for p in c['tcp_ports']},
        })
        cards[name] = cc

    with STATE_LOCK:
        STATE['updated'] = datetime.now().isoformat(timespec='seconds')
        STATE['cards']   = cards


def probe_loop():
    while True:
        try:
            probe_all()
        except Exception as e:
            # Log but keep looping — never let one bad probe kill the thread.
            print(f"[probe-loop] {type(e).__name__}: {e}", flush=True)
        time.sleep(PROBE_INTERVAL)


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
        return jsonify({'ok': False, 'err': f'unknown or non-rebootable host: {host}'}), 404

    user = auth.current_user()
    print(f"[reboot] host={host} user={user}", flush=True)

    if HOSTS[host].get('self'):
        # Self-reboot via the narrow sudoers rule the install script lays
        # down. Fire-and-forget so the HTTP response goes out before the
        # box dies.
        cmd = ['sudo', '-n', '/bin/systemctl', 'reboot']
    else:
        # Remote reboot via SSH as otadmin (NOPASSWD sudo on the target).
        ip = HOSTS[host]['lab']
        cmd = [
            'ssh',
            '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=accept-new',
            '-o', 'ConnectTimeout=5',
            f'{SSH_USER}@{ip}',
            'sudo systemctl reboot',
        ]

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({'ok': True, 'msg': f'reboot fired for {host}'})


# ---------------------------------------------------------------------------
# Entry.
# ---------------------------------------------------------------------------
def main():
    threading.Thread(target=probe_loop, daemon=True).start()
    print(f"[otlab-dashboard] listening on https://0.0.0.0:{LISTEN_PORT}/ "
          f"(user={DASH_USER}, probe={PROBE_INTERVAL}s)", flush=True)
    app.run(
        host='0.0.0.0',
        port=LISTEN_PORT,
        ssl_context=(DASH_CERT, DASH_KEY),
        threaded=True,
        debug=False,
        use_reloader=False,
    )


if __name__ == '__main__':
    main()
