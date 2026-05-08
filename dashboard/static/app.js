// OTLab dashboard — client. Polls /api/status every 3 s, renders cards,
// sparklines, honeypot intel, system health; manages pcap captures.

const ROW_ORDER = {
  net:      ['wan', 'mgmt_gw', 'fw'],
  plc:      ['softplc-1', 'softplc-2', 'honeypot-host'],
  honeypot: ['siemens-PS4', 'schneider-M340', 'rockwell-CHEM'],
};

const HEALTH_ORDER = ['softplc-1', 'softplc-2', 'honeypot-host'];
const REBOOTABLE   = new Set(['softplc-1', 'softplc-2', 'honeypot-host']);

// ---------- helpers ----------

function kv(key, val, cls = '') {
  return `<div class="key">${key}</div><div class="val ${cls}">${val}</div>`;
}

function pingStatus(c) {
  if (c.up === true)  return c.ms != null ? `UP · ${c.ms} ms` : 'UP';
  if (c.up === false) return 'DOWN';
  return '–';
}

function fmtUptime(secs) {
  if (secs == null) return '–';
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtBytes(n) {
  if (n == null || n === 0) return '–';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${u[i]}`;
}

// ---------- sparkline ----------

function sparkline(data, width = 100, height = 18) {
  if (!data || data.length < 2) return '<svg></svg>';
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = (max - min) || 1;
  const step = width / (data.length - 1);
  const pts = data.map((v, i) => {
    const x = (i * step).toFixed(1);
    const y = (height - ((v - min) / range) * (height - 2) - 1).toFixed(1);
    return `${x},${y}`;
  }).join(' ');
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;
}

// ---------- card renderers ----------

function plcExtras(name, c) {
  const rows = [];
  if (c.plc_ui !== undefined) {
    rows.push(kv('OpenPLC :8080', c.plc_ui ? '✓' : '✗', c.plc_ui ? 'ok' : 'down'));
  }
  if (c.modbus && c.modbus.hr) {
    const r = c.modbus.hr;
    if (name === 'softplc-1') {
      const co = c.modbus.co || [false, false];
      rows.push(kv('heartbeat', String(r[3])));
      rows.push(kv('link_ok',   String(r[4]), r[4] === 1 ? 'ok' : 'down'));
      rows.push(kv('link_loss', String(r[5]), r[5] > 0 ? 'warn' : 'ok'));
      rows.push(kv('RUN coil',  co[0] ? 'YES' : 'NO',  co[0] ? 'ok' : 'down'));
    } else if (name === 'softplc-2') {
      const co = c.modbus.co || [false, false];
      rows.push(kv('heartbeat', String(r[3])));
      rows.push(kv('RUN',       co[0] ? 'YES' : 'NO',  co[0] ? 'ok' : 'down'));
      rows.push(kv('HI_ALARM',  co[1] ? 'YES' : 'NO',  co[1] ? 'down' : 'ok'));
    }
  } else if (c.up && (name === 'softplc-1' || name === 'softplc-2')) {
    rows.push(kv('Modbus', 'no read', 'down'));
  }
  return rows.length ? `<div class="data">${rows.join('')}</div>` : '';
}

function sparkBlock(name, c) {
  if (!c.history) return '';
  const h = c.history;
  if (!h.tank || h.tank.length < 2) return '';
  const cur = (arr, suffix) => {
    const v = arr[arr.length - 1];
    return v != null ? `${v.toFixed(1)} ${suffix}` : '–';
  };
  return `
    <div class="spark-block">
      <div class="spark-row">
        <span class="label">tank</span>
        ${sparkline(h.tank)}
        <span class="cur">${cur(h.tank, '%')}</span>
      </div>
      <div class="spark-row">
        <span class="label">temp</span>
        ${sparkline(h.temp)}
        <span class="cur">${cur(h.temp, '°F')}</span>
      </div>
      <div class="spark-row">
        <span class="label">press</span>
        ${sparkline(h.press)}
        <span class="cur">${cur(h.press, 'PSI')}</span>
      </div>
    </div>`;
}

function svcsExtras(c) {
  if (!c.svcs) return '';
  const html = Object.entries(c.svcs).map(([port, ok]) =>
    `<span class="svc ${ok ? 'ok' : 'down'}">:${port} ${ok ? '✓' : '✗'}</span>`
  ).join('');
  return `<div class="svcs">${html}</div>`;
}

function intelBlock(name, intel) {
  if (!intel) return '';
  const c1 = intel.conn_1m || {all: 0, ext: 0};
  const c5 = intel.conn_5m || {all: 0, ext: 0};
  const ch = intel.conn_1h || {all: 0, ext: 0};
  const ips = intel.top_ips || [];
  const rows = ips.length
    ? ips.map(r => `<div class="row"><span class="ip">${r.ip}</span><span class="hits">${r.hits} hit${r.hits === 1 ? '' : 's'}</span></div>`).join('')
    : `<div class="empty">no external connections in tail</div>`;
  return `
    <div class="intel">
      <div class="conn-row">
        <div class="conn-cell ext"><span class="v">${c1.ext}</span><span class="l">EXT 1M</span></div>
        <div class="conn-cell ext"><span class="v">${c5.ext}</span><span class="l">EXT 5M</span></div>
        <div class="conn-cell ext"><span class="v">${ch.ext}</span><span class="l">EXT 1H</span></div>
      </div>
      <div class="ip-list">${rows}</div>
    </div>`;
}

function rebootButton(name) {
  if (!REBOOTABLE.has(name)) return '';
  return `<button class="reboot" data-host="${name}">Reboot ${name}</button>`;
}

const RESTART_SVCS = {
  'softplc-1':     ['openplc'],
  'softplc-2':     ['sensor-sim', 'openplc', 'otlab-dashboard'],
  'honeypot-host': [],
};

function svcButtons(name) {
  const svcs = RESTART_SVCS[name] || [];
  if (svcs.length === 0) return '';
  return `
    <div class="svc-restart">
      ${svcs.map(s => `<button class="svc-btn" data-host="${name}" data-svc="${s}" title="systemctl restart ${s}">↻ ${s}</button>`).join('')}
    </div>`;
}

function renderCard(name, c, j) {
  if (!c) c = { up: null, label: name };
  let stateCls = c.up === true ? 'ok' : (c.up === false ? 'down' : '');

  // Conpot personas: degrade state based on TCP-port probes (more
  // reliable than ICMP through the macvlan).
  if (c.svcs) {
    const allOk  = Object.values(c.svcs).every(v => v);
    const anyOk  = Object.values(c.svcs).some(v => v);
    if (allOk)         stateCls = 'ok';
    else if (anyOk)    stateCls = 'warn';
    else               stateCls = 'down';
  }

  const isMinimal = !c.modbus && !c.svcs && c.plc_ui === undefined;
  const status    = c.svcs
    ? (Object.values(c.svcs).every(v => v) ? 'UP' :
       Object.values(c.svcs).some(v => v)  ? 'DEGRADED' : 'DOWN')
    : pingStatus(c);

  // Honeypot persona intel comes from j.honeypot; PLC sparklines from
  // c.history (already inlined in the card).
  const intel = (j.honeypot && j.honeypot[name]) ? intelBlock(name, j.honeypot[name]) : '';

  return `
    <div class="card ${stateCls} ${isMinimal ? 'minimal' : ''}">
      <div class="top">
        <span class="name">${c.label || name}</span>
        <span class="status">${status}</span>
      </div>
      ${plcExtras(name, c)}
      ${sparkBlock(name, c)}
      ${svcsExtras(c)}
      ${intel}
      ${svcButtons(name)}
      ${rebootButton(name)}
    </div>`;
}

// ---------- network topology graph ----------

// Static layout: 3 columns. Mac (left, via tailscale) → 3 lab Pis (middle)
// → conpot personas (right). Coords are within an 800×280 viewBox.
const TOPO_NODES = [
  { id: 'mac',            label: 'Mac\n(tailscale)',        x: 60,  y: 140, type: 'client'  },
  { id: 'softplc-1',      label: 'softplc-1\n10.20.30.47',  x: 320, y: 60,  type: 'plc',     card: 'softplc-1' },
  { id: 'softplc-2',      label: 'softplc-2\n10.20.30.49',  x: 320, y: 140, type: 'plc',     card: 'softplc-2' },
  { id: 'honeypot-host',  label: 'honeypot\n10.20.30.48',   x: 320, y: 220, type: 'host',    card: 'honeypot-host' },
  { id: 'siemens',        label: 'Siemens\n.50',            x: 660, y: 60,  type: 'conpot',  card: 'siemens-PS4' },
  { id: 'schneider',      label: 'Schneider\n.51',          x: 660, y: 140, type: 'conpot',  card: 'schneider-M340' },
  { id: 'rockwell',       label: 'Rockwell\n.52',           x: 660, y: 220, type: 'conpot',  card: 'rockwell-CHEM' },
];

// Edges describe persistent or current relationships. Active inference
// from card state — edges go gray if either endpoint is down/unknown.
const TOPO_EDGES = [
  // Tailscale (mac to all 3 Pis via subnet route)
  { from: 'mac', to: 'softplc-1',     label: 'tailscale',      kind: 'mgmt' },
  { from: 'mac', to: 'softplc-2',     label: 'tailscale +rt',  kind: 'mgmt' },
  { from: 'mac', to: 'honeypot-host', label: 'tailscale',      kind: 'mgmt' },
  // Phase 1 master/slave loop (the headline relationship)
  { from: 'softplc-1', to: 'softplc-2', label: 'Modbus :5020',  kind: 'modbus', primary: true },
  // Conpot containers run on honeypot-host
  { from: 'honeypot-host', to: 'siemens',   label: 'macvlan',  kind: 'macvlan' },
  { from: 'honeypot-host', to: 'schneider', label: 'macvlan',  kind: 'macvlan' },
  { from: 'honeypot-host', to: 'rockwell',  label: 'macvlan',  kind: 'macvlan' },
];

function topoNodeColor(node, j) {
  if (!node.card) return 'var(--accent)';
  const c = j.cards && j.cards[node.card];
  const s = cardStateOf(c);
  return s === 'ok'   ? 'var(--ok)'
       : s === 'warn' ? 'var(--warn)'
       : s === 'down' ? 'var(--down)'
       : 'var(--fg-dim)';
}

function topoEdgeColor(edge, j) {
  // The Phase 1 modbus loop: drive color from softplc-1's link_ok mirror.
  if (edge.kind === 'modbus' && j.cards && j.cards['softplc-1']) {
    const m = j.cards['softplc-1'].modbus;
    if (m && m.hr && m.hr.length >= 5) {
      const linkOk = m.hr[4];
      return linkOk === 1 ? 'var(--ok)' : 'var(--down)';
    }
  }
  // Generic: edge is "alive" if both endpoints are at least partially up.
  const a = TOPO_NODES.find(n => n.id === edge.from);
  const b = TOPO_NODES.find(n => n.id === edge.to);
  const sA = a && a.card ? cardStateOf(j.cards && j.cards[a.card]) : 'ok';
  const sB = b && b.card ? cardStateOf(j.cards && j.cards[b.card]) : 'ok';
  if (sA === 'down' || sB === 'down') return 'var(--down)';
  if (sA === 'warn' || sB === 'warn') return 'var(--warn)';
  return 'var(--fg-dim)';
}

function renderTopology(j) {
  const target = document.getElementById('topology');
  if (!target) return;
  const nodeMap = Object.fromEntries(TOPO_NODES.map(n => [n.id, n]));

  const edgeSvg = TOPO_EDGES.map(e => {
    const a = nodeMap[e.from], b = nodeMap[e.to];
    const color = topoEdgeColor(e, j);
    const stroke = e.primary ? 2.5 : 1.4;
    const dash   = e.kind === 'mgmt' ? '4,3' : (e.kind === 'macvlan' ? '2,4' : '');
    const midX = (a.x + b.x) / 2, midY = (a.y + b.y) / 2;
    return `
      <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"
            stroke="${color}" stroke-width="${stroke}"
            ${dash ? `stroke-dasharray="${dash}"` : ''} />
      <text x="${midX}" y="${midY - 4}" text-anchor="middle"
            fill="var(--fg-dim)" font-family="JetBrains Mono, monospace"
            font-size="9">${e.label}</text>`;
  }).join('');

  const nodeSvg = TOPO_NODES.map(n => {
    const color = topoNodeColor(n, j);
    const w = 110, h = 38;
    const tspans = n.label.split('\n').map((line, i) =>
      `<tspan x="${n.x}" dy="${i === 0 ? -2 : 12}">${line}</tspan>`
    ).join('');
    return `
      <g class="topo-node" transform="translate(${n.x - w/2}, ${n.y - h/2})">
        <rect x="0" y="0" width="${w}" height="${h}" rx="4" ry="4"
              fill="var(--panel)" stroke="${color}" stroke-width="2"/>
        <circle cx="${w - 9}" cy="9" r="4" fill="${color}"/>
      </g>
      <text x="${n.x}" y="${n.y}" text-anchor="middle"
            fill="var(--fg)" font-family="JetBrains Mono, monospace"
            font-size="10">${tspans}</text>`;
  }).join('');

  target.innerHTML = `
    <svg viewBox="0 0 800 280" preserveAspectRatio="xMidYMid meet" class="topology-svg">
      <text x="14" y="18" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="10" letter-spacing="2">NODES + LIVE FLOWS</text>
      ${edgeSvg}
      ${nodeSvg}
    </svg>`;
}

// ---------- inject-fault panel ----------

function renderInjectPanel(faults) {
  const panel = document.getElementById('inject-panel');
  if (!panel) return;
  const f = faults || {};
  const stateEl = document.getElementById('inject-state');
  if (stateEl) {
    if (f.any_active) {
      const labels = [];
      if (f.paused)      labels.push('PAUSED');
      if (f.hb_paused)   labels.push('HB PAUSED');
      if (f.force_alarm) labels.push('FORCED ALARM');
      stateEl.innerHTML = `<span class="badge active">FAULT ACTIVE</span> <span class="state-list">${labels.join(' · ')}</span>`;
    } else {
      stateEl.innerHTML = `<span class="badge ok">no faults injected</span>`;
    }
  }
  document.querySelectorAll('button.inject-btn').forEach(btn => {
    const key = btn.dataset.key;
    const on = !!f[key];
    btn.classList.toggle('on', on);
    btn.setAttribute('title', btn.dataset.tip || '');
  });
}

function bindInjectButtons() {
  document.querySelectorAll('button.inject-btn').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => doInjectToggle(btn));
  });
  const clearBtn = document.getElementById('inject-clear');
  if (clearBtn && !clearBtn.dataset.bound) {
    clearBtn.dataset.bound = '1';
    clearBtn.addEventListener('click', () => doInjectClear(clearBtn));
  }
}

async function doInjectToggle(btn) {
  const key = btn.dataset.key;
  const turnOn = !btn.classList.contains('on');
  btn.classList.add('busy');
  try {
    const r = await fetch('/api/inject', {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({[key]: turnOn}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) alert(`Inject ${key} failed: ${j.err || 'HTTP ' + r.status}`);
    else if (j.state) renderInjectPanel(j.state);
  } catch (e) {
    alert('Inject request error: ' + e.message);
  } finally {
    btn.classList.remove('busy');
  }
}

async function doInjectClear(btn) {
  btn.classList.add('busy');
  try {
    const r = await fetch('/api/inject/clear', {method: 'POST', credentials: 'include'});
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) alert(`Clear faults failed: ${j.err || 'HTTP ' + r.status}`);
    else if (j.state) renderInjectPanel(j.state);
  } catch (e) {
    alert('Clear request error: ' + e.message);
  } finally {
    btn.classList.remove('busy');
  }
}

// ---------- synoptic HMI view ----------

// Reads from softplc-1's mirror (the canonical "what the master sees"
// view of the process). Falls back gracefully when data is missing.
function renderSynoptic(j) {
  const target = document.getElementById('synoptic');
  if (!target) return;

  const s1 = (j.cards && j.cards['softplc-1']) || {};
  const s2 = (j.cards && j.cards['softplc-2']) || {};
  const m1 = s1.modbus;
  const m2 = s2.modbus;

  // Prefer softplc-1's mirror; fall back to softplc-2 sensor-sim direct read.
  let tank = null, temp = null, press = null, hb = null,
      linkOk = null, linkLoss = null, running = null, hiAlarm = null;

  if (m1 && m1.hr && m1.hr.length >= 6) {
    tank     = m1.hr[0] / 10.0;
    temp     = m1.hr[1] / 10.0;
    press    = m1.hr[2] / 10.0;
    hb       = m1.hr[3];
    linkOk   = m1.hr[4];
    linkLoss = m1.hr[5];
    if (m1.co && m1.co.length >= 2) {
      running  = m1.co[0];
      hiAlarm  = m1.co[1];
    }
  } else if (m2 && m2.hr && m2.hr.length >= 4) {
    tank  = m2.hr[0] / 10.0;
    temp  = m2.hr[1] / 10.0;
    press = m2.hr[2] / 10.0;
    hb    = m2.hr[3];
    if (m2.co && m2.co.length >= 2) {
      running = m2.co[0];
      hiAlarm = m2.co[1];
    }
  }

  const hasData = tank != null;
  const tankPct = hasData ? Math.max(0, Math.min(100, tank)) : 0;

  // Color regions for temp gauge: 65-73 normal, 73-75 warn, >75 alarm.
  const tempRange = { min: 60, max: 80 };
  const tempPct = hasData ? Math.max(0, Math.min(100,
                    ((temp - tempRange.min) / (tempRange.max - tempRange.min)) * 100)) : 0;
  const tempCls = !hasData ? 'unknown' : (temp >= 75 ? 'alarm' : (temp >= 73 ? 'warn' : 'ok'));

  // Pressure gauge: 50-80 PSI nominal range
  const pressRange = { min: 40, max: 90 };
  const pressPct = hasData ? Math.max(0, Math.min(100,
                     ((press - pressRange.min) / (pressRange.max - pressRange.min)) * 100)) : 0;

  const tankFillY    = 70 + (1 - tankPct / 100) * 130;  // tank box: y=70..200
  const tankFillH    = 200 - tankFillY;
  const linkColor    = linkOk === 1 ? '#3eb957' : '#e25555';
  const runColor     = running === true ? '#3eb957' : '#7d8794';
  const alarmColor   = hiAlarm === true ? '#e25555' : '#2a323f';
  const alarmCls     = hiAlarm === true ? 'pulsing' : '';
  const lossColor    = (linkLoss != null && linkLoss > 0) ? '#e0a23a' : '#7d8794';

  const fmt = (v, suffix, digits = 1) =>
    v == null ? '–' : `${v.toFixed(digits)} ${suffix}`;

  target.innerHTML = `
    <svg viewBox="0 0 800 320" preserveAspectRatio="xMidYMid meet" class="synoptic-svg">
      <defs>
        <linearGradient id="water" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#58a6ff" stop-opacity="0.85"/>
          <stop offset="100%" stop-color="#1f6feb" stop-opacity="1"/>
        </linearGradient>
        <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="#2a323f" stroke-width="1"/>
        </pattern>
        <filter id="glow"><feGaussianBlur stdDeviation="2"/></filter>
      </defs>

      <!-- title strip -->
      <rect x="0" y="0" width="800" height="28" fill="#000" />
      <text x="14"  y="19" fill="#3eb957" font-family="JetBrains Mono, monospace" font-size="13" font-weight="700" letter-spacing="2">MAPLE RIDGE — DISTRIBUTION SYSTEM</text>
      ${(j.faults && j.faults.any_active)
        ? `<g><rect x="540" y="4" width="160" height="20" fill="#e25555" rx="2"/><text x="620" y="18" text-anchor="middle" fill="#000" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" letter-spacing="2">FAULT INJECTED</text></g>`
        : ''}
      ${(j.writes && j.writes.any_active)
        ? `<g><rect x="370" y="4" width="160" height="20" fill="#e0a23a" rx="2"/><text x="450" y="18" text-anchor="middle" fill="#000" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" letter-spacing="2">WRITES OVERRIDE</text></g>`
        : ''}
      <text x="786" y="19" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11" text-anchor="end">P&amp;ID v1 · live</text>

      <!-- ====== TANK ====== -->
      <text x="115" y="55" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11">RAW WATER TANK · TK-101</text>
      <!-- tank shell -->
      <rect x="60" y="65" width="110" height="140" rx="6" ry="6" fill="#0b0e13" stroke="#2a323f" stroke-width="2"/>
      <!-- water fill -->
      ${hasData ? `<rect x="62" y="${tankFillY.toFixed(1)}" width="106" height="${tankFillH.toFixed(1)}" fill="url(#water)"/>` : ''}
      <!-- level marks -->
      ${[0, 25, 50, 75, 100].map(p => {
        const y = 200 - (p/100)*130;
        return `<line x1="58" y1="${y}" x2="64" y2="${y}" stroke="#7d8794" stroke-width="1"/>` +
               `<text x="52" y="${y+3}" font-size="9" fill="#7d8794" font-family="JetBrains Mono, monospace" text-anchor="end">${p}</text>`;
      }).join('')}
      <!-- value readout -->
      <text x="115" y="225" text-anchor="middle" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="14" font-weight="700">${fmt(tank,'%',1)}</text>
      <text x="115" y="240" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="9">LT-101 · level</text>

      <!-- ====== TEMP ====== -->
      <text x="280" y="55" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11">WATER TEMP · TT-201</text>
      <rect x="240" y="70" width="80" height="135" rx="4" fill="#0b0e13" stroke="#2a323f" stroke-width="1.5"/>
      <!-- bulb at bottom -->
      <circle cx="280" cy="200" r="14" fill="${tempCls === 'alarm' ? '#e25555' : tempCls === 'warn' ? '#e0a23a' : '#3eb957'}" opacity="0.85"/>
      <!-- column fill -->
      ${hasData ? `<rect x="272" y="${(80 + (1 - tempPct/100) * 110).toFixed(1)}" width="16" height="${(110 - (1 - tempPct/100) * 110).toFixed(1)}" fill="${tempCls === 'alarm' ? '#e25555' : tempCls === 'warn' ? '#e0a23a' : '#3eb957'}"/>` : ''}
      <!-- gradient marks (60, 65, 70, 75, 80) -->
      ${[60, 65, 70, 73, 75, 80].map(t => {
        const y = 80 + (1 - (t-60)/20) * 110;
        const tickColor = t >= 75 ? '#e25555' : t >= 73 ? '#e0a23a' : '#7d8794';
        return `<line x1="295" y1="${y}" x2="305" y2="${y}" stroke="${tickColor}" stroke-width="1"/>` +
               `<text x="310" y="${y+3}" font-size="9" fill="${tickColor}" font-family="JetBrains Mono, monospace">${t}°</text>`;
      }).join('')}
      <text x="280" y="225" text-anchor="middle" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="14" font-weight="700">${fmt(temp,'°F',1)}</text>
      <text x="280" y="240" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="9">TT-201 · temp</text>

      <!-- ====== PRESSURE GAUGE ====== -->
      <text x="430" y="55" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11">DISCHARGE · PT-301</text>
      <!-- gauge face -->
      <circle cx="430" cy="135" r="60" fill="#0b0e13" stroke="#2a323f" stroke-width="2"/>
      <!-- arc ticks -->
      ${(() => {
        let ticks = '';
        for (let i = 0; i <= 10; i++) {
          const angle = (-150 + (300/10)*i) * Math.PI / 180;
          const x1 = 430 + Math.cos(angle) * 50;
          const y1 = 135 + Math.sin(angle) * 50;
          const x2 = 430 + Math.cos(angle) * 56;
          const y2 = 135 + Math.sin(angle) * 56;
          ticks += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="#7d8794" stroke-width="1.5"/>`;
        }
        return ticks;
      })()}
      <!-- pointer -->
      ${(() => {
        if (!hasData) return '';
        const angle = (-150 + (300 * pressPct / 100)) * Math.PI / 180;
        const x = 430 + Math.cos(angle) * 48;
        const y = 135 + Math.sin(angle) * 48;
        return `<line x1="430" y1="135" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#58a6ff" stroke-width="2.5" stroke-linecap="round"/>` +
               `<circle cx="430" cy="135" r="4" fill="#58a6ff"/>`;
      })()}
      <text x="430" y="225" text-anchor="middle" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="14" font-weight="700">${fmt(press,'PSI',1)}</text>
      <text x="430" y="240" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="9">PT-301 · press</text>

      <!-- ====== PIPE FROM TANK TO PUMP ====== -->
      <line x1="115" y1="205" x2="115" y2="265" stroke="#444c5a" stroke-width="6" stroke-linecap="round"/>
      <line x1="115" y1="265" x2="220" y2="265" stroke="#444c5a" stroke-width="6" stroke-linecap="round"/>
      <!-- pump symbol -->
      <circle cx="240" cy="265" r="22" fill="#0b0e13" stroke="${runColor}" stroke-width="2"/>
      <text x="240" y="270" text-anchor="middle" fill="${runColor}" font-family="JetBrains Mono, monospace" font-size="12" font-weight="700">P</text>
      <text x="240" y="298" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="9">P-101</text>

      <line x1="262" y1="265" x2="380" y2="265" stroke="#444c5a" stroke-width="6" stroke-linecap="round"/>
      <line x1="380" y1="265" x2="380" y2="200" stroke="#444c5a" stroke-width="6" stroke-linecap="round"/>

      <!-- ====== STATUS PANEL (right side) ====== -->
      <g transform="translate(530, 60)">
        <text x="0" y="-5" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11" letter-spacing="1">STATUS</text>
        <rect x="0" y="0" width="240" height="200" fill="#141a23" stroke="#2a323f" rx="3"/>

        <!-- RUN -->
        <circle cx="22" cy="30" r="8" fill="${runColor}" />
        <text x="42" y="34" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="11">RUN</text>
        <text x="230" y="34" fill="${runColor}" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" text-anchor="end">${running == null ? '–' : (running ? 'YES' : 'NO')}</text>

        <!-- HI_TEMP_ALARM -->
        <circle cx="22" cy="60" r="8" fill="${alarmColor}" class="${alarmCls}"/>
        <text x="42" y="64" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="11">HI_TEMP_ALARM</text>
        <text x="230" y="64" fill="${hiAlarm ? '#e25555' : '#7d8794'}" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" text-anchor="end">${hiAlarm == null ? '–' : (hiAlarm ? 'YES' : 'no')}</text>

        <!-- LINK -->
        <circle cx="22" cy="90" r="8" fill="${linkColor}" />
        <text x="42" y="94" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="11">LINK softplc-1↔softplc-2</text>
        <text x="230" y="94" fill="${linkColor}" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" text-anchor="end">${linkOk == null ? '–' : (linkOk ? 'OK' : 'DOWN')}</text>

        <!-- HEARTBEAT -->
        <text x="22" y="124" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="11">HEARTBEAT</text>
        <text x="230" y="124" fill="#58a6ff" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" text-anchor="end">${hb == null ? '–' : hb}</text>

        <!-- LINK_LOSS -->
        <text x="22" y="150" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="11">LINK_LOSS</text>
        <text x="230" y="150" fill="${lossColor}" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" text-anchor="end">${linkLoss == null ? '–' : linkLoss}</text>

        <!-- TIMESTAMP -->
        <line x1="0" y1="170" x2="240" y2="170" stroke="#2a323f"/>
        <text x="22" y="187" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="9">last poll: ${j.updated || '–'}</text>
      </g>
    </svg>`;
}

// ---------- system health ----------

function tempCls(temp) {
  if (temp == null || temp === '') return '';
  const t = parseFloat(temp);
  if (t >= 75) return 'down';
  if (t >= 65) return 'warn';
  return 'ok';
}

function pctCls(pct) {
  if (pct == null) return '';
  const p = parseFloat(pct);
  if (p >= 90) return 'down';
  if (p >= 75) return 'warn';
  return 'ok';
}

function renderHealthCard(name, h) {
  const ok = !!h;
  const cls = ok ? 'ok' : 'down';
  if (!ok) {
    return `
      <div class="card down">
        <div class="top">
          <span class="name">${name}</span>
          <span class="status">UNREACHABLE</span>
        </div>
      </div>`;
  }
  const failedCls       = h.failed       > 0 ? 'down' : 'ok';
  const failedSshCls    = h.failed_ssh_1h > 5 ? 'warn' : (h.failed_ssh_1h > 0 ? 'warn' : 'ok');
  const aptCls          = h.apt_pending  > 30 ? 'warn' : (h.apt_pending > 0 ? 'warn' : 'ok');
  const tsCls           = h.ts_online === 'active' ? 'ok' : 'down';
  const ppsCls          = (h.modbus_pps_in != null && h.modbus_pps_in < 5) ? 'warn' : 'ok';

  // Format last-bootstrap as "5h ago" if we have a timestamp.
  let bootstrap = '–';
  if (h.bootstrap_ts) {
    try {
      const t = new Date(h.bootstrap_ts);
      const diff = (Date.now() - t.getTime()) / 1000;
      const ago  = diff < 3600 ? `${Math.floor(diff/60)}m`
                 : diff < 86400 ? `${Math.floor(diff/3600)}h`
                 : `${Math.floor(diff/86400)}d`;
      const tag  = h.bootstrap_commit ? ` @ ${h.bootstrap_commit.slice(0,7)}` : '';
      bootstrap = `${ago} ago${tag}`;
    } catch(_e) { /* fall through */ }
  }

  // Tailscale row: show advertised routes if any, otherwise just IP+state.
  const tsRoutes = h.ts_routes ? ` ⟶ ${h.ts_routes}` : '';
  const tsLabel  = h.ts_ip ? `${h.ts_ip}${tsRoutes}` : '–';

  // Modbus poll rate is only present on softplc-2 (where we sniff).
  const ppsRow = (h.modbus_pps_in != null)
    ? kv('Modbus pps in', `${h.modbus_pps_in.toFixed(1)} /s`, ppsCls)
    : '';

  return `
    <div class="card ${cls}">
      <div class="top">
        <span class="name">${name}</span>
        <span class="status">${fmtUptime(h.uptime)}</span>
      </div>
      <div class="data">
        ${kv('CPU',         `${h.cpu}%`,                   pctCls(h.cpu))}
        ${kv('mem',         `${h.mem}%`,                   pctCls(h.mem))}
        ${kv('disk /',      `${h.disk_pct}%`,              pctCls(h.disk_pct))}
        ${kv('disk size',   `${h.disk_used}/${h.disk_size} G`)}
        ${kv('temp',        h.temp ? `${h.temp} °C` : '–', tempCls(h.temp))}
        ${kv('load 1/5',    `${h.load1}/${h.load5}`)}
        ${kv('failed svcs', String(h.failed),              failedCls)}
        ${kv('boot dev',    h.boot_dev || '–')}
        ${kv('SSH fails 1h',String(h.failed_ssh_1h ?? 0),  failedSshCls)}
        ${kv('apt pending', String(h.apt_pending ?? 0),    aptCls)}
        ${kv('tailscale',   tsLabel,                       tsCls)}
        ${ppsRow}
        ${kv('bootstrap',   bootstrap)}
      </div>
    </div>`;
}

// ---------- creds panel ----------

let CREDS_LOADED = false;

async function loadCreds() {
  try {
    const r = await fetch('/api/creds', { credentials: 'include' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const body = document.getElementById('creds-body');
    body.innerHTML = Object.entries(j).map(([_k, v]) => `
      <div class="cred-row">
        <div class="cred-label">${v.label}</div>
        <div class="cred-fields">
          <span class="cred-key">user:</span><span class="cred-val">${v.username}</span>
          <span class="cred-key">pass:</span><span class="cred-val mono">${v.password}</span>
        </div>
        <div class="cred-note">${v.note || ''}</div>
      </div>`).join('');
    CREDS_LOADED = true;
  } catch (e) {
    document.getElementById('creds-body').innerHTML =
      `<div class="cred-row error">failed to load: ${e.message}</div>`;
  }
}

function bindCredsToggle() {
  const btn = document.getElementById('creds-toggle');
  const body = document.getElementById('creds-body');
  if (!btn || btn.dataset.bound) return;
  btn.dataset.bound = '1';
  btn.addEventListener('click', async () => {
    if (body.hidden) {
      if (!CREDS_LOADED) await loadCreds();
      body.hidden = false;
      btn.textContent = 'Hide credentials';
    } else {
      body.hidden = true;
      btn.textContent = 'Show credentials';
    }
  });
}

// ---------- live Modbus wire feed (SSE) ----------

function fmtFrame(f) {
  let detail;
  if (f.regs)        detail = `[${f.regs.join(', ')}]`;
  else if (f.value !== undefined) detail = `addr=${f.addr} val=${f.value}`;
  else if (f.count !== undefined) detail = `addr=${f.addr} cnt=${f.count}`;
  else detail = '';
  const cls = (f.fc & 0x80) ? 'wire-exc' : (f.fc >= 5 ? 'wire-write' : 'wire-read');
  return `<div class="wire-row ${cls}">
    <span class="t">${f.t}</span>
    <span class="src">${f.src}</span>
    <span class="arrow">→</span>
    <span class="dst">${f.dst}</span>
    <span class="fc">${f.name}</span>
    <span class="detail">${detail}</span>
  </div>`;
}

function renderWireFrame(f) {
  const feed = document.getElementById('wire-feed');
  if (!feed) return;
  feed.insertAdjacentHTML('afterbegin', fmtFrame(f));
  while (feed.childElementCount > 80) feed.lastElementChild.remove();
}

async function bootWireFeed() {
  // Initial fill from snapshot
  try {
    const r = await fetch('/api/wire/recent', { credentials: 'include' });
    if (r.ok) {
      const j = await r.json();
      const feed = document.getElementById('wire-feed');
      if (feed && j.frames) {
        feed.innerHTML = j.frames.slice().reverse().map(fmtFrame).join('');
      }
    }
  } catch(_e) {}

  // Subscribe to SSE
  const status = document.getElementById('wire-status');
  try {
    const es = new EventSource('/api/wire/stream', { withCredentials: true });
    es.onopen    = () => { if (status) { status.textContent = 'live'; status.classList.add('ok'); } };
    es.onmessage = (e) => { try { renderWireFrame(JSON.parse(e.data)); } catch(_e) {} };
    es.onerror   = () => { if (status) { status.textContent = 'reconnecting…'; status.classList.remove('ok'); } };
  } catch(e) {
    if (status) status.textContent = 'unsupported';
  }
}

// ---------- Modbus write playground ----------

function renderWriteState(writes) {
  const el = document.getElementById('write-state');
  if (!el) return;
  if (!writes || !writes.any_active) {
    el.innerHTML = `<span class="badge ok">no overrides active</span>`;
    return;
  }
  const reg  = Object.entries(writes.reg_overrides  || {});
  const coil = Object.entries(writes.coil_overrides || {});
  const parts = [];
  for (const [a, v] of reg)  parts.push(`reg[${a}] = ${v}`);
  for (const [a, v] of coil) parts.push(`coil[${a}] = ${v ? 1 : 0}`);
  el.innerHTML = `<span class="badge active">SENSOR-SIM OVERRIDES ACTIVE</span>
    <span class="state-list">${parts.join(' · ')}</span>`;
}

async function doWriteSubmit() {
  const target = document.getElementById('write-target').value;
  const kind   = document.getElementById('write-kind').value;
  const addr   = parseInt(document.getElementById('write-addr').value, 10);
  const valRaw = document.getElementById('write-value').value.trim();
  let value;
  if (kind === 'coil') {
    value = (valRaw === '1' || valRaw.toLowerCase() === 'true');
  } else {
    value = parseInt(valRaw, 10);
    if (isNaN(value)) { alert('register value must be an integer 0-65535'); return; }
  }
  const btn = document.getElementById('write-submit');
  btn.classList.add('busy');
  try {
    const r = await fetch('/api/write', {
      method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({target, kind, addr, value}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) {
      alert(`Write failed: ${j.err || 'HTTP ' + r.status}`);
    } else {
      // Brief inline ack — the next refresh will surface the override.
      const note = document.getElementById('write-state');
      if (note) note.insertAdjacentHTML('beforeend',
        ` <span class="ok-flash">✓ wrote ${kind}[${addr}]=${value}</span>`);
      setTimeout(() => {
        document.querySelectorAll('.ok-flash').forEach(e => e.remove());
      }, 2500);
    }
  } catch(e) {
    alert('Write request error: ' + e.message);
  } finally {
    btn.classList.remove('busy');
  }
}

async function doWriteClear() {
  if (!confirm('Clear all sensor-sim Modbus write overrides?')) return;
  const btn = document.getElementById('write-clear');
  btn.classList.add('busy');
  try {
    const r = await fetch('/api/write/clear', {method: 'POST', credentials: 'include'});
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) alert(`Clear failed: ${j.err || 'HTTP ' + r.status}`);
  } finally {
    btn.classList.remove('busy');
  }
}

function bindWritePanel() {
  const submit = document.getElementById('write-submit');
  const clear  = document.getElementById('write-clear');
  if (submit && !submit.dataset.bound) { submit.dataset.bound = '1'; submit.addEventListener('click', doWriteSubmit); }
  if (clear  && !clear.dataset.bound)  { clear.dataset.bound  = '1'; clear.addEventListener('click', doWriteClear); }
}

// ---------- cohort reset ----------

async function doCohortReset() {
  if (!confirm(
    'Reset the lab for the next cohort?\n\n' +
    'This will:\n' +
    '  • Clear all sensor-sim faults + write overrides\n' +
    '  • Delete all pcap captures\n' +
    '  • Restart sensor-sim (heartbeat resets)\n' +
    '  • Restart OpenPLC on softplc-1 (link_loss resets)\n\n' +
    'The dashboard itself will keep running.'
  )) return;
  const btn = document.getElementById('cohort-reset');
  const out = document.getElementById('cohort-state');
  btn.classList.add('busy');
  btn.textContent = 'Resetting…';
  try {
    const r = await fetch('/api/cohort/reset', {method: 'POST', credentials: 'include'});
    const j = await r.json().catch(() => ({}));
    if (out) {
      const items = (j.steps || []).map(([k, v]) =>
        `<div class="step ${v === true || (typeof v === 'number' && v >= 0) ? 'ok' : 'down'}">
           <span class="step-name">${k}</span>
           <span class="step-result">${v === true ? '✓' : (v === false ? '✗' : v)}</span>
         </div>`).join('');
      out.innerHTML = `<div class="cohort-result">${items || 'reset done'}</div>`;
    }
    btn.textContent = 'Reset Done';
    setTimeout(() => { btn.textContent = 'Reset Lab for Next Cohort'; btn.classList.remove('busy'); }, 4000);
  } catch(e) {
    alert('Cohort reset error: ' + e.message);
    btn.classList.remove('busy');
    btn.textContent = 'Reset Lab for Next Cohort';
  }
}

function bindCohortReset() {
  const btn = document.getElementById('cohort-reset');
  if (btn && !btn.dataset.bound) {
    btn.dataset.bound = '1';
    btn.addEventListener('click', doCohortReset);
  }
}

// ---------- theme toggle ----------

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('otlab-theme', theme); } catch(_e) {}
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = theme === 'light' ? '◑ dark' : '◐ light';
}

function initTheme() {
  let theme = 'dark';
  try { theme = localStorage.getItem('otlab-theme') || 'dark'; } catch(_e) {}
  applyTheme(theme);
  const btn = document.getElementById('theme-btn');
  if (btn) btn.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(cur === 'dark' ? 'light' : 'dark');
  });
}

// ---------- favicon — reflects worst card state ----------

function setFavicon(state) {
  // state: 'ok' (green), 'warn' (yellow), 'down' (red)
  const colors = { ok: '#3eb957', warn: '#e0a23a', down: '#e25555' };
  const fill = colors[state] || '#7d8794';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="${fill}"/><circle cx="16" cy="16" r="13" fill="none" stroke="#000" stroke-width="2"/></svg>`;
  const url = 'data:image/svg+xml;base64,' + btoa(svg);
  let link = document.querySelector('link[rel~="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = url;
  // Also flip the title bar for tab-buried-in-back visibility.
  const baseTitle = 'OTLab Status — Maple Ridge Treatment Plant';
  document.title = state === 'down' ? `!!! ${baseTitle}` :
                   state === 'warn' ? `! ${baseTitle}` :
                   baseTitle;
}

function worstStateOverall(j) {
  if (!j || !j.cards) return 'ok';
  let worst = 'ok';
  for (const name of NOTIFY_NAMES) {
    const s = cardStateOf(j.cards[name]);
    if (s === 'down')               return 'down';
    if (s === 'warn' && worst==='ok') worst = 'warn';
  }
  // Also consider injected faults as a "warn" since the lab is in non-normal state.
  if (j.faults && j.faults.any_active && worst === 'ok') worst = 'warn';
  return worst;
}

// ---------- browser notifications on state transitions ----------

const NOTIFY_NAMES = ['wan', 'fw', 'softplc-1', 'softplc-2', 'honeypot-host',
                      'siemens-PS4', 'schneider-M340', 'rockwell-CHEM'];
const PREV_STATE = {};   // name -> 'ok' | 'down' | 'warn' | undefined
let NOTIFY_ENABLED = false;

function ensureNotifyPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') { NOTIFY_ENABLED = true; return; }
  if (Notification.permission === 'denied') return;
  Notification.requestPermission().then(p => { NOTIFY_ENABLED = (p === 'granted'); });
}

function cardStateOf(c) {
  if (!c) return undefined;
  if (c.svcs) {
    const vals = Object.values(c.svcs);
    if (vals.every(v => v))   return 'ok';
    if (vals.some(v => v))    return 'warn';
    return 'down';
  }
  if (c.up === true)  return 'ok';
  if (c.up === false) return 'down';
  return undefined;
}

function detectStateTransitions(j) {
  setFavicon(worstStateOverall(j));
  if (!NOTIFY_ENABLED || !j || !j.cards) return;
  for (const name of NOTIFY_NAMES) {
    const cur = cardStateOf(j.cards[name]);
    const prev = PREV_STATE[name];
    if (prev && cur && prev !== cur && (cur === 'down' || cur === 'warn')) {
      const title = `OTLab: ${name} → ${cur.toUpperCase()}`;
      const body  = `was ${prev.toUpperCase()}, now ${cur.toUpperCase()}`;
      try { new Notification(title, {body, tag: 'otlab-' + name, renotify: false}); }
      catch (_e) { /* notification might fail silently — ignore */ }
    }
    if (cur) PREV_STATE[name] = cur;
  }
}

// ---------- captures ----------

function renderCaptures(captures) {
  const ul = document.getElementById('captures-list');
  if (!captures || captures.length === 0) {
    ul.innerHTML = '<li class="empty">no captures yet</li>';
    return;
  }
  ul.innerHTML = captures.map(c => {
    const status = `<span class="cap-status ${c.status}">${c.status.toUpperCase()}</span>`;
    const host = `<span class="cap-host">${c.host}</span>`;
    const time = `<span class="cap-time">${c.started}</span>`;
    const size = `<span class="cap-size">${c.size ? fmtBytes(c.size) : (c.status === 'running' ? '...' : '–')}</span>`;
    let action = '';
    if (c.status === 'complete') {
      action = `<span class="cap-action"><a href="/api/capture-download/${c.id}">Download</a></span>`;
    } else if (c.status === 'failed') {
      action = `<span class="cap-action" title="${(c.err||'').replace(/"/g,'&quot;')}">FAILED</span>`;
    } else {
      action = `<span class="cap-action">capturing…</span>`;
    }
    return `<li>${status}${host}${time}${size}${action}</li>`;
  }).join('');
}

// ---------- polling ----------

async function refresh() {
  try {
    const r = await fetch('/api/status', { credentials: 'include' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();

    document.getElementById('updated').textContent =
      j.updated ? `last poll: ${j.updated}` : 'awaiting first poll…';

    renderSynoptic(j);
    renderTopology(j);
    renderInjectPanel(j.faults);
    renderWriteState(j.writes);
    detectStateTransitions(j);

    for (const [row, names] of Object.entries(ROW_ORDER)) {
      const target = document.getElementById('row-' + row);
      if (target) {
        target.innerHTML = names.map(n => renderCard(n, j.cards[n], j)).join('');
      }
    }

    const healthRow = document.getElementById('row-health');
    if (healthRow) {
      healthRow.innerHTML = HEALTH_ORDER.map(n =>
        renderHealthCard(n, (j.health || {})[n])
      ).join('');
    }

    bindRebootButtons();
    bindServiceButtons();
    bindInjectButtons();
  } catch (e) {
    document.getElementById('updated').textContent = 'fetch error: ' + e.message;
  }
}

async function refreshCaptures() {
  try {
    const r = await fetch('/api/captures', { credentials: 'include' });
    if (!r.ok) return;
    const j = await r.json();
    renderCaptures(j.captures);
  } catch (e) { /* ignore */ }
}

function bindRebootButtons() {
  document.querySelectorAll('button.reboot').forEach(btn => {
    btn.addEventListener('click', () => doReboot(btn.dataset.host, btn));
  });
}

function bindServiceButtons() {
  document.querySelectorAll('button.svc-btn').forEach(btn => {
    btn.addEventListener('click', () => doServiceRestart(btn.dataset.host, btn.dataset.svc, btn));
  });
}

async function doServiceRestart(host, svc, btn) {
  if (!confirm(`Restart ${svc} on ${host}?`)) return;
  btn.classList.add('busy');
  const orig = btn.textContent;
  btn.textContent = '↻ restarting…';
  try {
    const r = await fetch(`/api/restart/${encodeURIComponent(host)}/${encodeURIComponent(svc)}`, {
      method: 'POST',
      credentials: 'include',
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok) {
      btn.textContent = '↻ done';
      setTimeout(() => { btn.classList.remove('busy'); btn.textContent = orig; }, 2500);
    } else {
      alert(`Restart ${svc} failed: ${j.err || 'HTTP ' + r.status}`);
      btn.classList.remove('busy'); btn.textContent = orig;
    }
  } catch (e) {
    alert('Restart request error: ' + e.message);
    btn.classList.remove('busy'); btn.textContent = orig;
  }
}

function bindCaptureButtons() {
  document.querySelectorAll('button.capture-btn').forEach(btn => {
    btn.addEventListener('click', () => doCapture(btn.dataset.host, btn));
  });
}

async function doReboot(host, btn) {
  const ok = confirm(
    `Reboot ${host}?\n\n` +
    `This will issue 'sudo systemctl reboot' on the target. The device will be ` +
    `unreachable for ~60-90 seconds while it comes back up.`
  );
  if (!ok) return;
  btn.classList.add('busy');
  btn.textContent = 'Rebooting…';
  try {
    const r = await fetch(`/api/reboot/${encodeURIComponent(host)}`, {
      method: 'POST',
      credentials: 'include',
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok) {
      btn.textContent = 'Reboot fired';
    } else {
      alert('Reboot failed: ' + (j.err || 'HTTP ' + r.status));
      btn.classList.remove('busy');
      btn.textContent = `Reboot ${host}`;
    }
  } catch (e) {
    alert('Reboot request error: ' + e.message);
    btn.classList.remove('busy');
    btn.textContent = `Reboot ${host}`;
  }
}

async function doCapture(host, btn) {
  btn.classList.add('busy');
  const orig = btn.textContent;
  btn.textContent = 'Starting…';
  try {
    const r = await fetch(`/api/capture/${encodeURIComponent(host)}`, {
      method: 'POST',
      credentials: 'include',
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok) {
      btn.textContent = `Capturing ${j.duration}s…`;
      // Refresh captures list immediately so the running entry shows
      refreshCaptures();
      // Re-enable button after ~ duration so user can fire another
      setTimeout(() => {
        btn.classList.remove('busy');
        btn.textContent = orig;
      }, (j.duration + 5) * 1000);
    } else {
      alert('Capture failed: ' + (j.err || 'HTTP ' + r.status));
      btn.classList.remove('busy');
      btn.textContent = orig;
    }
  } catch (e) {
    alert('Capture request error: ' + e.message);
    btn.classList.remove('busy');
    btn.textContent = orig;
  }
}

// ---------- boot ----------

initTheme();
ensureNotifyPermission();
bindCaptureButtons();
bindCredsToggle();
bindWritePanel();
bindCohortReset();
bootWireFeed();
setInterval(refresh,         3000);
setInterval(refreshCaptures, 5000);
refresh();
refreshCaptures();
