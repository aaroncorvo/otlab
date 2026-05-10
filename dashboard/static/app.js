// OTLab dashboard — client. Polls /api/status every 3 s, renders cards,
// sparklines, honeypot intel, system health; manages pcap captures.

// Cards are rendered into named row containers in index.html. Each row
// in ROW_ORDER maps to <div id="row-<key>">. Renaming a key here
// requires updating index.html too.
//
// V2.x+ topology: virtual master + virtual OpenPLC instances + virtual
// outstations all live alongside the physical Pis on pcn-br0. The "pcn"
// row groups all the L1 process artifacts (real + virtual) so the
// teaching narrative holds: poll loop → outstation → IDS detection.
const ROW_ORDER = {
  net:        ['wan', 'mgmt_gw', 'dmz_gw', 'pcn_gw'],
  infra:      ['fw-dmz-pcn', 'dhcp-dmz', 'dhcp-pcn'],
  plc:        ['l1-plc-01', 'l3-mon-01', 'l1-hp-01'],
  pcn:        ['modbus-master', 'sensor-sim', 'dnp3-outstation',
               'plc-1-virt', 'plc-2-virt'],
  honeypot:   ['siemens-PS4', 'schneider-M340', 'rockwell-CHEM'],
};

const HEALTH_ORDER = ['l1-plc-01', 'l3-mon-01', 'l1-hp-01'];
const REBOOTABLE   = new Set(['l1-plc-01', 'l3-mon-01', 'l1-hp-01']);

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
  // l1-plc-01 hosts BOTH master (mirror on :502) and sensor-sim (:5020 +
  // dnp3 :20000). Render both: top half = master view, bottom half = sensor.
  if (name === 'l1-plc-01' && c.modbus_master && c.modbus_master.hr) {
    const r  = c.modbus_master.hr;
    const co = c.modbus_master.co || [false, false];
    rows.push(kv('heartbeat',    String(r[3])));
    rows.push(kv('link_ok',      String(r[4]), r[4] === 1 ? 'ok' : 'down'));
    rows.push(kv('link_loss',    String(r[5]), r[5] > 0 ? 'warn' : 'ok'));
    rows.push(kv('RUN coil',     co[0] ? 'YES' : 'NO',  co[0] ? 'ok' : 'down'));
  }
  if (name === 'l1-plc-01' && c.modbus && c.modbus.hr) {
    const co = c.modbus.co || [false, false];
    rows.push(kv('sensor RUN',   co[0] ? 'YES' : 'NO',  co[0] ? 'ok' : 'down'));
    rows.push(kv('HI_ALARM',     co[1] ? 'YES' : 'NO',  co[1] ? 'down' : 'ok'));
    if (c.dnp3 !== undefined) {
      rows.push(kv('DNP3 :20000', c.dnp3 ? '✓' : '✗', c.dnp3 ? 'ok' : 'down'));
    }
  } else if (c.up && name === 'l1-plc-01') {
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

// V2.y+ — extras for the new container cards.
//
// modbus-master:  master_state from the shared volume (rate, polls,
//                 last hr/coils, uptime)
// dhcp-{dmz,pcn}: lease count + recent leases
// fw-dmz-pcn:     DNS reachability check on .1 in both zones
// plc-{1,2}-virt: OpenPLC web UI reachability
function infraExtras(name, c) {
  const rows = [];

  // --- modbus-master live tick ---
  if (c.master_state) {
    const s = c.master_state;
    const rate = s.rate_per_s != null ? s.rate_per_s.toFixed(1) : '–';
    const ok   = s.polls_ok   != null ? s.polls_ok   : '–';
    const err  = s.polls_err  != null ? s.polls_err  : '–';
    rows.push(kv('poll rate',  `${rate} /s`,  s.rate_per_s > 0 ? 'ok' : 'down'));
    rows.push(kv('polls ok',   String(ok)));
    rows.push(kv('polls err',  String(err),  err > 0 ? 'warn' : 'ok'));
    if (s.hr && s.hr.length) {
      rows.push(kv('last hr',  `[${s.hr.join(', ')}]`));
    }
    if (s.coils && s.coils.length) {
      rows.push(kv('last coils', `[${s.coils.map(b => b ? '1' : '0').join(', ')}]`));
    }
  } else if (name === 'modbus-master') {
    rows.push(kv('master state', 'no recent tick', 'down'));
  }

  // --- DHCP lease summary ---
  if (c.dhcp && c.dhcp.leases) {
    const n = c.dhcp.leases.length;
    rows.push(kv('active leases', String(n), n > 0 ? 'ok' : 'warn'));
    // Show the 3 most recently acquired (lowest expires_s = closest
    // to expiry = furthest from issuance, so we want highest = newest)
    const newest = [...c.dhcp.leases]
      .sort((a, b) => (b.expires_s || 0) - (a.expires_s || 0))
      .slice(0, 3);
    for (const lease of newest) {
      const tag = lease.hostname || lease.mac.slice(-5);
      rows.push(kv(tag, lease.ip));
    }
  }

  // --- Firewall DNS forwarder ---
  if (c.dns_dmz !== undefined) {
    rows.push(kv('DNS DMZ :53', c.dns_dmz ? '✓' : '✗', c.dns_dmz ? 'ok' : 'down'));
  }
  if (c.dns_pcn !== undefined) {
    rows.push(kv('DNS PCN :53', c.dns_pcn ? '✓' : '✗', c.dns_pcn ? 'ok' : 'down'));
  }

  // --- Virtual OpenPLC web UI ---
  if ((name === 'plc-1-virt' || name === 'plc-2-virt') && c.plc_ui !== undefined) {
    rows.push(kv('OpenPLC :8080', c.plc_ui ? '✓' : '✗', c.plc_ui ? 'ok' : 'down'));
  }

  // --- DNP3 outstation card ---
  if (name === 'dnp3-outstation' && c.dnp3 !== undefined) {
    rows.push(kv('DNP3 :20000', c.dnp3 ? '✓' : '✗', c.dnp3 ? 'ok' : 'down'));
  }

  // --- sensor-sim card extras (when shown standalone, not nested in l1-plc-01) ---
  if (name === 'sensor-sim' && c.modbus && c.modbus.hr) {
    const co = c.modbus.co || [false, false];
    rows.push(kv('hr[0..3]',   `[${c.modbus.hr.join(', ')}]`));
    rows.push(kv('RUN coil',   co[0] ? 'YES' : 'NO',  co[0] ? 'ok' : 'down'));
    rows.push(kv('HI_ALARM',   co[1] ? 'YES' : 'NO',  co[1] ? 'down' : 'ok'));
  }

  return rows.length ? `<div class="data">${rows.join('')}</div>` : '';
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
  'l1-plc-01':     ['openplc', 'sensor-sim', 'dnp3-outstation'],
  'l3-mon-01':     ['otlab-dashboard', 'suricata'],
  'l1-hp-01':      [],
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

  // "minimal" = render as a small status pill rather than a full data card.
  // True only when none of the data-carrying fields are present.
  const isMinimal = !c.modbus && !c.svcs && c.plc_ui === undefined
                  && !c.master_state && !c.dhcp && c.dnp3 === undefined
                  && c.dns_dmz === undefined && c.dns_pcn === undefined;
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
      ${infraExtras(name, c)}
      ${sparkBlock(name, c)}
      ${svcsExtras(c)}
      ${intel}
      ${svcButtons(name)}
      ${rebootButton(name)}
    </div>`;
}

// ---------- scenario header ----------

function renderScenarioPanel(scenario) {
  const el = document.getElementById('scenario-panel');
  if (!el) return;
  if (!scenario) {
    el.innerHTML = `<div class="scenario-loading">scenario data not yet loaded…</div>`;
    return;
  }
  const tags = (scenario.regulatory || []).map(r =>
    `<span class="reg-tag" title="${(r.scope||'').replace(/"/g,'&quot;')}">${r.name}</span>`
  ).join('');
  el.innerHTML = `
    <div class="scenario-head">
      <div>
        <span class="scenario-id">${scenario.id || ''}</span>
        <h3>${scenario.name || 'Scenario'}</h3>
        <div class="scenario-vertical">${scenario.vertical || ''}</div>
      </div>
      <div class="scenario-l1">L1 role: <span>${scenario.purdue_l1_role || '—'}</span></div>
    </div>
    <p class="scenario-desc">${scenario.description || ''}</p>
    ${tags ? `<div class="reg-tags-row">${tags}</div>` : ''}
  `;
}

// ---------- Purdue Reference Model view ----------
//
// Renders the lab's actual assets into the standard 6-level Purdue
// hierarchy (ISA-95). Trust-boundary lines drawn between L1↔L2 and
// L3↔L4↔L5 — those are the well-known segmentation choke points
// every ICS curriculum teaches.

const PURDUE_LEVELS = [
  { id: 'l5', label: 'L5  Internet / Enterprise WAN',                color: '#7d8794',
    assets: [{name:'Internet uplink', addr:'WAN'}, {name:'Tailscale tailnet', addr:'100.64/10'}] },
  { id: 'l4', label: 'L4  Enterprise Zone',                          color: '#7d8794',
    assets: [{name:'(none deployed)', addr:'planned: corp IT, AD'}] },
  { id: 'l3', label: 'L3  Industrial DMZ (dmz-br0 · 192.168.75.0/24)', color: '#58a6ff',
    assets: [{name:'OTLab Dashboard',  addr:'192.168.75.40 (container, l3-mon-01)', card:'l3-mon-01'},
             {name:'Cockpit',          addr:'l3-mon-01:9090 — Linux admin'},
             {name:'Portainer',        addr:'l3-mon-01:9443 — Docker mgmt'},
             {name:'EdgeShark',        addr:'l3-mon-01:5001 — live packet capture'},
             {name:'Ignition SCADA',   addr:'192.168.75.20 — planned V2', planned: true},
             {name:'Apache Guacamole', addr:'192.168.75.30 — planned V2', planned: true},
             {name:'Authentik IdP/SSO',addr:'192.168.75.10 — planned V2', planned: true},
             {name:'Suricata IDS',     addr:'sniffs pcn-br0 — planned V2', planned: true},
             {name:'Engineering laptop', addr:'tailscale, ad-hoc'}] },
  { id: 'conduit', label: '── Conduit (firewall container) ──',       color: '#e25555',
    assets: [{name:'fw-dmz-pcn',       addr:'enforces L3.5↔L1/2 policy', card:null}] },
  { id: 'l2', label: 'L2  Supervisory (HMI / SCADA)',                color: '#58a6ff',
    assets: [{name:'CODESYS Web HMI',  addr:'10.20.30.81 — planned V3', planned: true},
             {name:'OpenPLC web UI',   addr:'l1-plc-01:8080 (physical)',     card:'l1-plc-01'},
             {name:'OpenPLC web UI',   addr:'10.20.30.60:8080 (virtual #1)'},
             {name:'OpenPLC web UI',   addr:'10.20.30.61:8080 (virtual #2)'},
             {name:'Conpot operator UI', addr:':80 each persona'}] },
  { id: 'l1', label: 'L1  Basic Control (pcn-br0 · 10.20.30.0/24)',  color: '#3eb957',
    assets: [{name:'plc-1-virt (OpenPLC master)',   addr:'10.20.30.60 (virtual)'},
             {name:'plc-2-virt (OpenPLC outstation)',addr:'10.20.30.61 (virtual)'},
             {name:'sensor-sim',                     addr:'10.20.30.70 (virtual)'},
             {name:'dnp3-outstation',                addr:'10.20.30.71 (virtual)'},
             {name:'CODESYS PLC (Modbus + OPC-UA)',  addr:'10.20.30.80 — planned V3', planned: true},
             {name:'l1-plc-01 (physical, Phase 2 hw)',addr:'10.20.30.47', card:'l1-plc-01'},
             {name:'l1-hp-01 (physical, Conpot host)',addr:'10.20.30.48', card:'l1-hp-01'},
             {name:'Siemens Conpot',                 addr:'10.20.30.50', card:'siemens-PS4'},
             {name:'Schneider Conpot',               addr:'10.20.30.51', card:'schneider-M340'},
             {name:'Rockwell Conpot',                addr:'10.20.30.52', card:'rockwell-CHEM'}] },
  { id: 'l0', label: 'L0  Field / Process',                          color: '#e0a23a',
    assets: []  /* filled dynamically from the active scenario's registers + coils */ },
];

function renderPurdue(j) {
  const target = document.getElementById('purdue');
  if (!target) return;
  const scenario = j.scenario || null;

  // L0 assets come from the scenario (sensors + actuators)
  const l0Assets = [];
  if (scenario) {
    for (const r of scenario.registers || []) {
      if (r.name === 'HEARTBEAT') continue;
      l0Assets.push({name: r.label || r.name, addr: `${r.unit||''}` });
    }
    for (const c of scenario.coils || []) {
      l0Assets.push({name: c.label || c.name, addr: 'coil'});
    }
  }
  if (!l0Assets.length) {
    l0Assets.push({name: 'sensor-sim waveforms', addr: '(scenario not loaded)'});
  }
  const levels = PURDUE_LEVELS.map(L => L.id === 'l0' ? {...L, assets: l0Assets} : L);

  const ROW_H = 92;
  const W = 1000;
  const H = ROW_H * levels.length;
  const TRUST_BOUNDARIES = new Set(['l1', 'l3', 'l4']);  // boundaries below these levels

  const rowHtml = levels.map((L, i) => {
    const yTop = i * ROW_H;
    const yMid = yTop + ROW_H / 2;
    const showBoundary = TRUST_BOUNDARIES.has(L.id);

    // Asset chips, laid out horizontally
    const chipW = 170, chipH = 44, gap = 14;
    const totalW = L.assets.length * chipW + (L.assets.length - 1) * gap;
    const startX = Math.max(220, (W - totalW) / 2);
    const chips = L.assets.map((a, k) => {
      const x = startX + k * (chipW + gap);
      const cardState = a.card ? cardStateOf((j.cards || {})[a.card]) : null;
      const stroke = a.planned ? 'var(--fg-dim)' : (cardState ? stateColor(cardState) : L.color);
      const dash   = a.planned ? 'stroke-dasharray="4,3"' : '';
      const opacity = a.planned ? '0.55' : '1';
      const nameColor = a.planned ? 'var(--fg-dim)' : 'var(--fg)';
      const plannedTag = a.planned
        ? `<text x="${chipW - 4}" y="11" text-anchor="end" fill="var(--warn)" font-family="JetBrains Mono, monospace" font-size="7" letter-spacing="1">PLANNED</text>`
        : '';
      return `
        <g transform="translate(${x}, ${yMid - chipH/2})" opacity="${opacity}">
          <rect width="${chipW}" height="${chipH}" rx="4" fill="var(--panel-2)" stroke="${stroke}" stroke-width="${cardState && !a.planned ? 2 : 1.4}" ${dash}/>
          ${plannedTag}
          <text x="${chipW/2}" y="20" text-anchor="middle" fill="${nameColor}" font-family="JetBrains Mono, monospace" font-size="10" font-weight="600">${a.name}</text>
          <text x="${chipW/2}" y="34" text-anchor="middle" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="9">${a.addr}</text>
        </g>`;
    }).join('');

    // Trust-boundary divider (between this level and the one above)
    const boundary = showBoundary
      ? `<line x1="0" y1="${yTop}" x2="${W}" y2="${yTop}" stroke="var(--warn)" stroke-width="1" stroke-dasharray="6,4"/>
         <text x="${W - 14}" y="${yTop - 4}" text-anchor="end" fill="var(--warn)" font-family="JetBrains Mono, monospace" font-size="9" letter-spacing="2">TRUST BOUNDARY</text>`
      : '';

    return `
      <g class="purdue-row">
        <rect x="0" y="${yTop}" width="${W}" height="${ROW_H}" fill="var(--panel)" stroke="var(--border)" stroke-width="1"/>
        <text x="14" y="${yTop + 18}" fill="${L.color}" font-family="JetBrains Mono, monospace" font-size="10" letter-spacing="2">${L.label}</text>
        ${chips}
        ${boundary}
      </g>`;
  }).join('');

  target.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="purdue-svg">
      ${rowHtml}
    </svg>`;
}

// ---------- Risks heatmap + list ----------

function renderRisksPanel(j) {
  const el = document.getElementById('risks-panel');
  if (!el) return;
  const scenario = j.scenario;
  if (!scenario || !scenario.risks) {
    el.innerHTML = `<div class="risks-loading">scenario risks not yet loaded…</div>`;
    return;
  }
  const risks = scenario.risks;

  // Build a 5×5 matrix indexed [likelihood-1][impact-1] = number of risks landing there
  const cells = Array.from({length: 5}, () => Array(5).fill(0));
  risks.forEach(r => {
    const L = Math.max(1, Math.min(5, r.likelihood|0)) - 1;
    const I = Math.max(1, Math.min(5, r.impact|0)) - 1;
    cells[L][I] += 1;
  });

  // Build the heatmap (impact rises top-to-bottom, likelihood rises left-to-right)
  const W = 360, H = 280, cellSz = 50, ox = 60, oy = 30;
  let grid = '';
  for (let imp = 5; imp >= 1; imp--) {
    for (let like = 1; like <= 5; like++) {
      const x = ox + (like - 1) * cellSz;
      const y = oy + (5 - imp) * cellSz;
      const score = like * imp;
      // Color: green at low product, yellow mid, red high. score range 1..25
      const huePos = Math.min(1, Math.max(0, (score - 1) / 24));
      const fillR = Math.round(40 + huePos * 180);
      const fillG = Math.round(160 - huePos * 100);
      const fillB = 50;
      const count = cells[like-1][imp-1];
      const opacity = count > 0 ? 0.75 : 0.18;
      grid += `<rect x="${x}" y="${y}" width="${cellSz - 2}" height="${cellSz - 2}" rx="3"
                     fill="rgba(${fillR},${fillG},${fillB},${opacity})" stroke="var(--border)"/>`;
      if (count > 0) {
        grid += `<text x="${x + cellSz/2}" y="${y + cellSz/2 + 4}" text-anchor="middle" fill="#000" font-family="JetBrains Mono, monospace" font-size="13" font-weight="700">${count}</text>`;
      }
    }
    grid += `<text x="${ox - 8}" y="${oy + (5 - imp) * cellSz + cellSz/2 + 4}" text-anchor="end" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="10">${imp}</text>`;
  }
  for (let like = 1; like <= 5; like++) {
    grid += `<text x="${ox + (like - 1) * cellSz + cellSz/2}" y="${oy + 5 * cellSz + 16}" text-anchor="middle" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="10">${like}</text>`;
  }
  grid += `<text x="${ox + 2.5 * cellSz}" y="${oy + 5 * cellSz + 32}" text-anchor="middle" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="9" letter-spacing="2">← LIKELIHOOD →</text>`;
  grid += `<text x="${ox - 36}" y="${oy + 2.5 * cellSz}" text-anchor="middle" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="9" letter-spacing="2" transform="rotate(-90 ${ox - 36} ${oy + 2.5 * cellSz})">← IMPACT →</text>`;

  // Risk list, sorted by score desc
  const sorted = risks.slice().sort((a,b) => (b.likelihood*b.impact) - (a.likelihood*a.impact));
  const listHtml = sorted.map(r => {
    const score = r.likelihood * r.impact;
    const cls = score >= 16 ? 'high' : (score >= 9 ? 'med' : 'low');
    return `
      <li class="risk-row ${cls}">
        <span class="risk-score">${score}</span>
        <span class="risk-name">${r.name}</span>
        <span class="risk-note">${r.note || ''}</span>
        ${r.demo ? `<span class="risk-demo" data-demo="${r.demo}">${r.demo}</span>` : ''}
      </li>`;
  }).join('');

  el.innerHTML = `
    <div class="risks-grid">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${grid}</svg>
    </div>
    <ul class="risks-list">${listHtml}</ul>
  `;
}

// ---------- Incident Walkthroughs ----------

const WALKTHROUGH_STATE = { active: null, step: 0 };

function renderWalkthroughs(j) {
  const el = document.getElementById('walkthroughs-panel');
  if (!el) return;
  const scenario = j.scenario;
  if (!scenario || !(scenario.walkthroughs || []).length) {
    el.innerHTML = `<div class="walkthroughs-loading">no walkthroughs defined for this scenario.</div>`;
    return;
  }

  const list = scenario.walkthroughs.map(w => {
    const isActive = WALKTHROUGH_STATE.active === w.id;
    return `
      <button class="walkthrough-card ${isActive ? 'active' : ''}" data-walkthrough-id="${w.id}">
        <span class="wt-name">${w.name}</span>
        <span class="wt-meta">${w.minutes || '?'} min · ${(w.steps||[]).length} steps</span>
      </button>`;
  }).join('');

  let player = '';
  if (WALKTHROUGH_STATE.active) {
    const w = scenario.walkthroughs.find(w => w.id === WALKTHROUGH_STATE.active);
    if (w) {
      const s = w.steps[WALKTHROUGH_STATE.step] || w.steps[0];
      const stepNum = WALKTHROUGH_STATE.step + 1;
      const total = w.steps.length;
      player = `
        <div class="walkthrough-player">
          <div class="wt-player-head">
            <span class="wt-player-name">${w.name}</span>
            <span class="wt-player-progress">step ${stepNum} of ${total}</span>
            <button class="wt-close" id="wt-close">×</button>
          </div>
          <h4 class="wt-step-title">${s.title || `Step ${stepNum}`}</h4>
          <p class="wt-step-body">${s.body || ''}</p>
          ${s.highlight ? `<div class="wt-highlight">→ scroll to <code>${s.highlight}</code> on the page</div>` : ''}
          <div class="wt-controls">
            <button class="wt-prev" id="wt-prev" ${stepNum === 1 ? 'disabled' : ''}>← Prev</button>
            <button class="wt-next" id="wt-next" ${stepNum === total ? 'disabled' : ''}>Next →</button>
          </div>
        </div>`;
    }
  }

  el.innerHTML = `
    <div class="walkthroughs-list">${list}</div>
    ${player}
  `;
  bindWalkthroughs();
}

function bindWalkthroughs() {
  document.querySelectorAll('button.walkthrough-card').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      WALKTHROUGH_STATE.active = btn.dataset.walkthroughId;
      WALKTHROUGH_STATE.step = 0;
      refreshWalkthroughOnly();
    });
  });
  const prev = document.getElementById('wt-prev');
  const next = document.getElementById('wt-next');
  const close = document.getElementById('wt-close');
  if (prev && !prev.dataset.bound)  { prev.dataset.bound  = '1'; prev.addEventListener('click', () => { WALKTHROUGH_STATE.step = Math.max(0, WALKTHROUGH_STATE.step - 1); refreshWalkthroughOnly(); }); }
  if (next && !next.dataset.bound)  { next.dataset.bound  = '1'; next.addEventListener('click', () => { WALKTHROUGH_STATE.step += 1; refreshWalkthroughOnly(); }); }
  if (close && !close.dataset.bound){ close.dataset.bound = '1'; close.addEventListener('click', () => { WALKTHROUGH_STATE.active = null; refreshWalkthroughOnly(); }); }
}

function refreshWalkthroughOnly() {
  // Re-render just the walkthroughs panel from cached state, no full /api/status hit
  fetch('/api/scenario', { credentials: 'include' })
    .then(r => r.json())
    .then(j => renderWalkthroughs({ scenario: j.scenario }))
    .catch(() => {});
}

// ---------- network topology graph ----------
//
// Layout matches the actual physical/logical lab plumbing:
//   internet uplink → TP-Link router → switch → 3 Pis (eth0)
//   l1-hp-01 → 3 Conpot personas (macvlan child interfaces)
//   any other 10.20.30.x device discovered via ARP shows as an extra
//   node along the bottom row.
//
// Coords are in an 800×460 viewBox.

const TOPO_KNOWN_IPS = new Set([
  '10.20.30.1',                      // TP-Link
  '10.20.30.47', '10.20.30.49', '10.20.30.48',  // Pis
  '10.20.30.50', '10.20.30.51', '10.20.30.52',  // Conpot personas
]);

function stateColor(s) {
  return s === 'ok'   ? 'var(--ok)'
       : s === 'warn' ? 'var(--warn)'
       : s === 'down' ? 'var(--down)'
       : 'var(--fg-dim)';
}

function nodeBox(x, y, label, color, w = 130, h = 38, sub = '') {
  // label can be multi-line (\n separated). sub is small grey footer text.
  const lines = label.split('\n');
  const tspans = lines.map((line, i) =>
    `<tspan x="${x}" dy="${i === 0 ? -2 + (sub ? -4 : 0) : 12}">${line}</tspan>`
  ).join('');
  const subSvg = sub
    ? `<text x="${x}" y="${y + 14}" text-anchor="middle" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="8">${sub}</text>`
    : '';
  return `
    <g class="topo-node" transform="translate(${x - w/2}, ${y - h/2})">
      <rect x="0" y="0" width="${w}" height="${h}" rx="4" ry="4"
            fill="var(--panel)" stroke="${color}" stroke-width="2"/>
      <circle cx="${w - 9}" cy="9" r="4" fill="${color}"/>
    </g>
    <text x="${x}" y="${y}" text-anchor="middle"
          fill="var(--fg)" font-family="JetBrains Mono, monospace"
          font-size="10">${tspans}</text>
    ${subSvg}`;
}

function line(x1, y1, x2, y2, color, opts = {}) {
  const dash = opts.dash ? `stroke-dasharray="${opts.dash}"` : '';
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"
                stroke="${color}" stroke-width="${opts.w || 1.5}" ${dash} />`;
}

function lineLabel(x, y, text) {
  return `<text x="${x}" y="${y}" text-anchor="middle"
                fill="var(--fg-dim)" font-family="JetBrains Mono, monospace"
                font-size="9">${text}</text>`;
}

function renderTopology(j) {
  const target = document.getElementById('topology');
  if (!target) return;

  // ── card states ─────────────────────────────────────────────────────────
  const sS1   = cardStateOf(j.cards && j.cards['l1-plc-01']);
  const sS2   = cardStateOf(j.cards && j.cards['l3-mon-01']);
  const sHH   = cardStateOf(j.cards && j.cards['l1-hp-01']);
  const sFW   = cardStateOf(j.cards && j.cards['fw']);     // TP-Link ping
  const sWAN  = cardStateOf(j.cards && j.cards['wan']);    // 1.1.1.1 ping
  const sCS   = cardStateOf(j.cards && j.cards['siemens-PS4']);
  const sCSc  = cardStateOf(j.cards && j.cards['schneider-M340']);
  const sCR   = cardStateOf(j.cards && j.cards['rockwell-CHEM']);

  const linkColor = (() => {
    const m = j.cards && j.cards['l1-plc-01'] && j.cards['l1-plc-01'].modbus;
    if (m && m.hr && m.hr.length >= 5) {
      return m.hr[4] === 1 ? 'var(--ok)' : 'var(--down)';
    }
    return 'var(--fg-dim)';
  })();

  // ── auto-discovered other clients on 10.20.30.0/24 ─────────────────────
  const others = (j.neighbors || [])
    .filter(n => !TOPO_KNOWN_IPS.has(n.ip) && n.state !== 'FAILED')
    .slice(0, 4);   // cap to keep the diagram readable
  const otherCount = (j.neighbors || []).filter(n => !TOPO_KNOWN_IPS.has(n.ip) && n.state !== 'FAILED').length;
  const overflow = otherCount - others.length;

  // ── layout ──────────────────────────────────────────────────────────────
  // y-rows: 30=internet, 100=tp-link, 175=switch, 215=bus, 270=Pi row, 380=conpot+others
  const SW_Y    = 175;        // switch box center
  const SW_BOT  = SW_Y + 18;  // switch box bottom edge
  const BUS_Y   = 220;        // shared horizontal bus on the lab segment
  const PI_Y    = 290;
  const CP_Y    = 400;
  const xS1 = 200, xS2 = 360, xHH = 520;
  const xCpot = [430, 540, 650];
  const xOther = [80, 200, 320, 720];

  // X-range that the bus needs to cover: leftmost child to rightmost.
  // Compute dynamically so the bus always reaches every drop.
  const childXs = [xS1, xS2, xHH, ...others.map((_n, i) => xOther[i])];
  const busL = Math.min(...childXs) - 20;
  const busR = Math.max(...childXs) + 20;

  // ── edges ───────────────────────────────────────────────────────────────
  const edges = [
    // INTERNET → TP-Link
    line(400, 50, 400, 78,  stateColor(sWAN), { w: 2 }),
    lineLabel(440, 67, 'WAN'),

    // TP-Link → switch
    line(400, 122, 400, SW_Y - 18, stateColor(sFW), { w: 2 }),
    lineLabel(430, SW_Y - 30, 'LAN'),

    // Switch → bus drop (so all children visibly attach to the switch)
    line(400, SW_BOT, 400, BUS_Y, 'var(--accent)', { w: 2 }),
    // Horizontal bus spanning all children (the lab segment 10.20.30.0/24)
    line(busL, BUS_Y, busR, BUS_Y, 'var(--accent)', { w: 2 }),

    // Bus → each Pi
    line(xS1, BUS_Y, xS1, PI_Y - 18, stateColor(sS1)),
    line(xS2, BUS_Y, xS2, PI_Y - 18, stateColor(sS2)),
    line(xHH, BUS_Y, xHH, PI_Y - 18, stateColor(sHH)),

    // Phase 1 modbus loop — during the l1-plc-02 backfill gap, master polls
    // sensor-sim on the SAME box (l1-plc-01 loopback). Drawn as a self-loop
    // around l1-plc-01 to indicate "loopback." Once l1-plc-02 backfills,
    // the loop returns as an arc between l1-plc-01 and l1-plc-02 (.49).
    `<path d="M ${xS1 - 35} ${PI_Y - 5} q -25 -25 0 -45 q 25 -20 50 0 q 25 25 0 45"
            stroke="${linkColor}" stroke-width="2.5" fill="none" stroke-dasharray="3,3" />`,
    lineLabel(xS1, PI_Y - 60, 'Modbus :5020 (loopback during gap)'),

    // l1-hp-01 → conpot personas (macvlan, dashed)
    line(xHH, PI_Y + 18, xCpot[0], CP_Y - 18, stateColor(sCS),  { dash: '2,4' }),
    line(xHH, PI_Y + 18, xCpot[1], CP_Y - 18, stateColor(sCSc), { dash: '2,4' }),
    line(xHH, PI_Y + 18, xCpot[2], CP_Y - 18, stateColor(sCR),  { dash: '2,4' }),
    lineLabel(xHH + 80, PI_Y + 35, 'macvlan'),

    // Bus → each auto-discovered other client (off the bus, not the switch)
    ...others.map((_n, i) => line(xOther[i], BUS_Y, xOther[i], CP_Y - 18,
                                   'var(--fg-dim)', { dash: '4,3' })),
  ].join('');

  // ── nodes ──────────────────────────────────────────────────────────────
  const internet = nodeBox(400, 30, 'INTERNET\nWAN uplink', stateColor(sWAN), 160, 32, '');
  const tplink   = nodeBox(400, 100, 'TP-Link router\n10.20.30.1', stateColor(sFW), 170, 44, 'gateway · MFCTP AP');
  const sw       = nodeBox(400, SW_Y, 'SWITCH\nlab segment 10.20.30.0/24', 'var(--accent)', 320, 36, '');

  const pi1 = nodeBox(xS1, PI_Y, 'l1-plc-01\n10.20.30.47', stateColor(sS1), 130, 38, 'master · sensor-sim · DNP3');
  const pi2 = nodeBox(xS2, PI_Y, 'l3-mon-01\n10.20.30.49', stateColor(sS2), 130, 38, 'dashboard · Suricata · Guacamole');
  const pi3 = nodeBox(xHH, PI_Y, 'l1-hp-01\n10.20.30.48', stateColor(sHH), 130, 38, 'Conpot fabric');

  const cp1 = nodeBox(xCpot[0], CP_Y, 'Siemens\n.50', stateColor(sCS),  100, 32, 'PS4-CPU01');
  const cp2 = nodeBox(xCpot[1], CP_Y, 'Schneider\n.51', stateColor(sCSc), 100, 32, 'HVAC-M340');
  const cp3 = nodeBox(xCpot[2], CP_Y, 'Rockwell\n.52', stateColor(sCR),  100, 32, 'CHEM-LGX01');

  const otherNodes = others.map((n, i) => {
    const sub = (n.vendor || n.mac.slice(-8));
    return nodeBox(xOther[i], CP_Y, `unknown\n${n.ip}`, 'var(--fg-dim)', 110, 32, sub);
  }).join('');
  const overflowLabel = overflow > 0
    ? `<text x="${xOther[3] + 60}" y="${CP_Y + 4}" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="10">+${overflow} more</text>`
    : '';

  target.innerHTML = `
    <svg viewBox="0 0 800 460" preserveAspectRatio="xMidYMid meet" class="topology-svg">
      <text x="14" y="18" fill="var(--fg-dim)" font-family="JetBrains Mono, monospace" font-size="10" letter-spacing="2">PHYSICAL TOPOLOGY · live ARP discovery</text>
      ${edges}
      ${internet}${tplink}${sw}
      ${pi1}${pi2}${pi3}
      ${cp1}${cp2}${cp3}
      ${otherNodes}${overflowLabel}
    </svg>`;
}

// ---------- audit log panel ----------

async function loadAudit() {
  const action = (document.getElementById('audit-filter-action')?.value || '').trim();
  const user   = (document.getElementById('audit-filter-user')?.value   || '').trim();
  const params = new URLSearchParams({limit: 100});
  if (action) params.set('action', action);
  if (user)   params.set('user', user);
  try {
    const r = await fetch('/api/audit?' + params.toString(), { credentials: 'include' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const list = document.getElementById('audit-list');
    if (!list) return;
    if (!j.events || !j.events.length) {
      list.innerHTML = '<div class="audit-empty">no events match the filters</div>';
      return;
    }
    list.innerHTML = j.events.map(ev => {
      const okCls = (ev.outcome || '').toLowerCase().includes('ok') ? 'ok' :
                    (ev.outcome || '').toLowerCase().includes('fail') ? 'fail' :
                    (ev.outcome || '').toLowerCase().includes('rejected') ? 'fail' : '';
      return `
        <div class="audit-row ${okCls}">
          <span class="audit-ts">${ev.ts}</span>
          <span class="audit-user">${ev.user}</span>
          <span class="audit-action">${ev.action}</span>
          <span class="audit-target">${ev.target || ''}</span>
          <span class="audit-outcome">${ev.outcome || ''}</span>
          ${ev.params ? `<details class="audit-params"><summary>params</summary><pre>${escapeHtml(ev.params)}</pre></details>` : ''}
        </div>`;
    }).join('');
  } catch(e) {
    const list = document.getElementById('audit-list');
    if (list) list.innerHTML = `<div class="audit-error">load failed: ${e.message}</div>`;
  }
}

function bindAudit() {
  const refresh = document.getElementById('audit-refresh');
  if (refresh && !refresh.dataset.bound) {
    refresh.dataset.bound = '1';
    refresh.addEventListener('click', loadAudit);
  }
  ['audit-filter-action', 'audit-filter-user'].forEach(id => {
    const el = document.getElementById(id);
    if (el && !el.dataset.bound) {
      el.dataset.bound = '1';
      let t = null;
      el.addEventListener('input', () => {
        clearTimeout(t);
        t = setTimeout(loadAudit, 300);
      });
    }
  });
}

// ---------- test library panel ----------

let TESTS_CACHE = { tests: [], last_results: {} };

async function loadTests() {
  try {
    const r = await fetch('/api/tests', { credentials: 'include' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    TESTS_CACHE = await r.json();
    renderTestsPanel();
  } catch (e) {
    const el = document.getElementById('tests-panel');
    if (el) el.innerHTML = `<div class="tests-error">failed to load tests: ${e.message}</div>`;
  }
}

function renderTestsPanel() {
  const el = document.getElementById('tests-panel');
  if (!el) return;
  const { tests, last_results } = TESTS_CACHE;
  if (!tests || !tests.length) {
    el.innerHTML = `<div class="tests-empty">no tests discovered. Run install-sensor-sim.sh to deploy plc/tests/ to the Pi.</div>`;
    return;
  }
  el.innerHTML = tests.map(t => {
    const last = last_results[t.id];
    const lastBadge = last
      ? `<span class="test-last ${last.returncode === 0 ? 'ok' : 'fail'}">last: ${last.returncode === 0 ? 'PASS' : 'FAIL (rc=' + last.returncode + ')'}  ${last.started}</span>`
      : `<span class="test-last">last: —</span>`;
    const out = last
      ? `<pre class="test-output">${escapeHtml(last.stdout || '(no stdout)')}${last.stderr ? '\n[stderr]\n' + escapeHtml(last.stderr) : ''}</pre>`
      : '';
    const desc = (t.desc || '').split('\n').slice(0, 4).join('\n');
    return `
      <div class="test-card" data-test-id="${t.id}">
        <div class="test-card-head">
          <span class="test-name">${t.name}</span>
          <span class="test-kind">${t.kind}</span>
          ${lastBadge}
        </div>
        <pre class="test-desc">${escapeHtml(desc)}</pre>
        <div class="test-controls">
          <button class="test-run" data-test-id="${t.id}">▶ Run</button>
          ${out ? `<button class="test-toggle-out" data-test-id="${t.id}">show last output</button>` : ''}
        </div>
        <div class="test-out-wrap" id="test-out-${t.id}" hidden>${out}</div>
      </div>`;
  }).join('');
  bindTestButtons();
}

function escapeHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function bindTestButtons() {
  document.querySelectorAll('button.test-run').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => runTest(btn));
  });
  document.querySelectorAll('button.test-toggle-out').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      const w = document.getElementById('test-out-' + btn.dataset.testId);
      if (w) w.hidden = !w.hidden;
    });
  });
}

async function runTest(btn) {
  const id = btn.dataset.testId;
  btn.classList.add('busy');
  const orig = btn.textContent;
  btn.textContent = 'running…';
  try {
    const r = await fetch(`/api/tests/run/${encodeURIComponent(id)}`, {
      method: 'POST', credentials: 'include',
    });
    const j = await r.json();
    if (r.ok && j.ok) {
      TESTS_CACHE.last_results[id] = j.result;
      renderTestsPanel();
      const w = document.getElementById('test-out-' + id);
      if (w) w.hidden = false;   // auto-show output after a run
    } else {
      alert(`Test failed: ${j.err || 'HTTP ' + r.status}`);
    }
  } catch(e) {
    alert('Test request error: ' + e.message);
  } finally {
    btn.classList.remove('busy');
    btn.textContent = orig;
  }
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
//
// Data source priority (V2.x+):
//   1. modbus-master.master_state    — the live tick file written by
//      the master container every LOG_INTERVAL_S. Authoritative for
//      "what the master is seeing on the wire", and link health
//      derives from rate_per_s + polls_err.
//   2. sensor-sim.modbus             — direct probe of sensor-sim from
//      the dashboard. Same data as #1, refreshed at the dashboard's
//      probe cadence.
//   3. Legacy l1-plc-01.modbus_master — the V0/V1 path where l1-plc-01
//      ran a Modbus mirror at :502. Kept as last-resort fallback for
//      any deployment that hasn't migrated to the modbus-master
//      container yet.
//
// Process register layout (from plc/sensor-sim.py):
//   hr[0] TANK_LEVEL_PCT  (×10, e.g. 750 = 75.0%)
//   hr[1] WATER_TEMP_F    (×10)
//   hr[2] DISCHARGE_PRESS (×10)
//   hr[3] HEARTBEAT       (seconds since process start)
//   co[0] RUNNING
//   co[1] HIGH_TEMP_ALARM
function renderSynoptic(j) {
  const target = document.getElementById('synoptic');
  if (!target) return;

  const cards = j.cards || {};
  const mm    = (cards['modbus-master'] || {}).master_state;
  const ss    = (cards['sensor-sim']    || {}).modbus;
  const s1    = cards['l1-plc-01'] || {};
  const m1    = s1.modbus_master;

  let tank = null, temp = null, press = null, hb = null,
      linkOk = null, linkLoss = null, running = null, hiAlarm = null;

  if (mm && mm.hr && mm.hr.length >= 4) {
    // Source #1: modbus-master tick file. The master uses HR_COUNT=4
    // by default, so hr is exactly the 4 process registers.
    tank   = mm.hr[0] / 10.0;
    temp   = mm.hr[1] / 10.0;
    press  = mm.hr[2] / 10.0;
    hb     = mm.hr[3];
    if (mm.coils && mm.coils.length >= 2) {
      running = mm.coils[0];
      hiAlarm = mm.coils[1];
    }
    // Link health derived from poll quality. polls_err > 0 in the
    // last interval means the master is having to reconnect — reflect
    // that as link_loss > 0 + linkOk = 0.
    linkOk   = (mm.rate_per_s > 0 && mm.polls_err === 0) ? 1 : 0;
    linkLoss = mm.polls_err || 0;
  } else if (ss && ss.hr && ss.hr.length >= 4) {
    // Source #2: direct sensor-sim probe.
    tank  = ss.hr[0] / 10.0;
    temp  = ss.hr[1] / 10.0;
    press = ss.hr[2] / 10.0;
    hb    = ss.hr[3];
    if (ss.co && ss.co.length >= 2) {
      running = ss.co[0];
      hiAlarm = ss.co[1];
    }
    // No master telemetry available — link_ok comes from the probe
    // succeeding at all (we wouldn't be here otherwise).
    linkOk   = 1;
    linkLoss = 0;
  } else if (m1 && m1.hr && m1.hr.length >= 6) {
    // Source #3: legacy l1-plc-01 master mirror (V0/V1).
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
  }

  const hasData = tank != null;

  // Scenario-aware labels (fall back to water-treatment defaults).
  const scn = (j.scenario && j.scenario.synoptic) || {};
  const titleText  = scn.title          || 'MAPLE RIDGE — DISTRIBUTION SYSTEM';
  const tankLabel  = scn.tank_label     || 'RAW WATER TANK · TK-101';
  const tempLabel  = scn.thermo_label   || 'WATER TEMP · TT-201';
  const pressLabel = scn.pressure_label || 'DISCHARGE · PT-301';
  const alarmLabel = scn.alarm_label    || 'HI_TEMP_ALARM';
  const tankUnit   = scn.tank_unit      || '%';
  const tempUnit   = scn.temp_unit      || '°F';
  const pressUnit  = (j.scenario && (j.scenario.registers||[])[2] && j.scenario.registers[2].unit) || 'PSI';

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
      <text x="14"  y="19" fill="#3eb957" font-family="JetBrains Mono, monospace" font-size="13" font-weight="700" letter-spacing="2">${titleText}</text>
      ${(j.faults && j.faults.any_active)
        ? `<g><rect x="540" y="4" width="160" height="20" fill="#e25555" rx="2"/><text x="620" y="18" text-anchor="middle" fill="#000" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" letter-spacing="2">FAULT INJECTED</text></g>`
        : ''}
      ${(j.writes && j.writes.any_active)
        ? `<g><rect x="370" y="4" width="160" height="20" fill="#e0a23a" rx="2"/><text x="450" y="18" text-anchor="middle" fill="#000" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" letter-spacing="2">WRITES OVERRIDE</text></g>`
        : ''}
      <text x="786" y="19" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11" text-anchor="end">P&amp;ID v1 · live</text>

      <!-- ====== TANK ====== -->
      <text x="115" y="55" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11">${tankLabel}</text>
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
      <text x="115" y="225" text-anchor="middle" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="14" font-weight="700">${fmt(tank, tankUnit, 1)}</text>
      <text x="115" y="240" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="9">LT-101 · level</text>

      <!-- ====== TEMP ====== -->
      <text x="280" y="55" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11">${tempLabel}</text>
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
      <text x="280" y="225" text-anchor="middle" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="14" font-weight="700">${fmt(temp, tempUnit, 1)}</text>
      <text x="280" y="240" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="9">TT-201 · temp</text>

      <!-- ====== PRESSURE GAUGE ====== -->
      <text x="430" y="55" text-anchor="middle" fill="#7d8794" font-family="JetBrains Mono, monospace" font-size="11">${pressLabel}</text>
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
      <text x="430" y="225" text-anchor="middle" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="14" font-weight="700">${fmt(press, pressUnit, 1)}</text>
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

        <!-- ALARM -->
        <circle cx="22" cy="60" r="8" fill="${alarmColor}" class="${alarmCls}"/>
        <text x="42" y="64" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="11">${alarmLabel}</text>
        <text x="230" y="64" fill="${hiAlarm ? '#e25555' : '#7d8794'}" font-family="JetBrains Mono, monospace" font-size="11" font-weight="700" text-anchor="end">${hiAlarm == null ? '–' : (hiAlarm ? 'YES' : 'no')}</text>

        <!-- LINK -->
        <circle cx="22" cy="90" r="8" fill="${linkColor}" />
        <text x="42" y="94" fill="#d4dae0" font-family="JetBrains Mono, monospace" font-size="11">LINK master↔outstation</text>
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

  // Modbus poll rate is only present on l1-plc-01 (sensor-sim host).
  // During the l1-plc-02 backfill gap, master polls are loopback so this
  // reads 0; post-backfill it returns to ~20 pps.
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

// ---------- Suricata IDS alerts panel ----------

function fmtAlert(a) {
  // Color-code by severity (1=highest, 3=lowest)
  const sevClass = (a.severity === 1) ? 'sev-high' :
                   (a.severity === 2) ? 'sev-mid'  : 'sev-low';
  const ts = (a.ts || '').replace(/T/, ' ').replace(/\..*/, '');
  return `
    <div class="ids-row ${sevClass}">
      <span class="ids-ts">${ts}</span>
      <span class="ids-sig">${a.signature || '?'}</span>
      <span class="ids-flow">${a.src} → ${a.dst}</span>
      <span class="ids-sid">sid:${a.sid || '?'}</span>
    </div>`;
}

async function refreshIDSAlerts() {
  try {
    const r = await fetch('/api/suricata/alerts', { credentials: 'include' });
    if (!r.ok) return;
    const j = await r.json();
    const feed = document.getElementById('ids-feed');
    if (!feed) return;
    if (!j.alerts || j.alerts.length === 0) {
      feed.innerHTML = '<div class="ids-empty">no alerts yet — run a non-master Modbus write to trigger one</div>';
      return;
    }
    // Newest at top, last 25
    feed.innerHTML = j.alerts.slice().reverse().map(fmtAlert).join('');
  } catch(_e) {}
}

function bootIDSAlerts() {
  // Manual refresh button
  const btn = document.getElementById('ids-refresh');
  if (btn) btn.addEventListener('click', refreshIDSAlerts);
  // Initial fill + auto-refresh every 8s
  refreshIDSAlerts();
  setInterval(refreshIDSAlerts, 8000);
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
    '  • Restart OpenPLC on l1-plc-01 (link_loss resets)\n\n' +
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

// ---------- tab navigation ----------

const TAB_DEFAULT = 'overview';
const TAB_VALID   = new Set(['overview', 'architecture', 'live-data', 'teaching']);

function setActiveTab(name) {
  if (!TAB_VALID.has(name)) name = TAB_DEFAULT;
  try { localStorage.setItem('otlab-tab', name); } catch(_e) {}
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  document.querySelectorAll('.tab-pane').forEach(p => {
    p.classList.toggle('active', p.dataset.tab === name);
  });
}

function initTabs() {
  let saved = TAB_DEFAULT;
  try { saved = localStorage.getItem('otlab-tab') || TAB_DEFAULT; } catch(_e) {}
  setActiveTab(saved);
  document.querySelectorAll('.tab-btn').forEach(b => {
    if (b.dataset.bound) return;
    b.dataset.bound = '1';
    b.addEventListener('click', () => setActiveTab(b.dataset.tab));
  });
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

const NOTIFY_NAMES = ['wan', 'fw', 'l1-plc-01', 'l3-mon-01', 'l1-hp-01',
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

    renderScenarioPanel(j.scenario);
    renderPurdue(j);
    renderSynoptic(j);
    renderTopology(j);
    renderRisksPanel(j);
    renderWalkthroughs(j);
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

initTabs();
initTheme();
ensureNotifyPermission();
bindCaptureButtons();
bindCredsToggle();
bindWritePanel();
bindCohortReset();
bootWireFeed();
bootIDSAlerts();
loadTests();
setInterval(loadTests, 60000);
bindAudit();
loadAudit();
setInterval(loadAudit, 15000);
setInterval(refresh,         3000);
setInterval(refreshCaptures, 5000);
refresh();
refreshCaptures();
