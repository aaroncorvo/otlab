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
      ${rebootButton(name)}
    </div>`;
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
  const failedCls = h.failed > 0 ? 'down' : 'ok';
  return `
    <div class="card ${cls}">
      <div class="top">
        <span class="name">${name}</span>
        <span class="status">${fmtUptime(h.uptime)}</span>
      </div>
      <div class="data">
        ${kv('CPU',         `${h.cpu}%`,         pctCls(h.cpu))}
        ${kv('mem',         `${h.mem}%`,         pctCls(h.mem))}
        ${kv('disk /',      `${h.disk_pct}%`,    pctCls(h.disk_pct))}
        ${kv('disk size',   `${h.disk_used}/${h.disk_size} G`)}
        ${kv('temp',        h.temp ? `${h.temp} °C` : '–', tempCls(h.temp))}
        ${kv('load 1/5',    `${h.load1}/${h.load5}`)}
        ${kv('failed svcs', String(h.failed),    failedCls)}
        ${kv('boot dev',    h.boot_dev || '–')}
      </div>
    </div>`;
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

bindCaptureButtons();
setInterval(refresh,         3000);
setInterval(refreshCaptures, 5000);
refresh();
refreshCaptures();
