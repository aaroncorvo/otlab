/* Classroom Monitor — frontend
 *
 * Polls /api/status every 10 s. Maintains a cards{} map (ip → DOM element)
 * so updates patch metrics in-place without disturbing drag positions.
 * Layout positions come from the server on first render; after that the
 * card's own style.left/top is authoritative (saved to backend on drag-end).
 */

'use strict';

// ─── State ───────────────────────────────────────────────────────────────────
const cards = {};          // ip  → .pi-card element
let locked = false;
let dragging = null;
let dragOX   = 0;
let dragOY   = 0;

// ─── Utilities ───────────────────────────────────────────────────────────────
function fmtUptime(s) {
  if (!s || s < 0) return '--';
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0)  return `${d}d ${h}h`;
  if (h > 0)  return `${h}h ${m}m`;
  return `${m}m`;
}

function tempClass(t) {
  if (t >= 80) return 'temp-hot';
  if (t >= 70) return 'temp-warm';
  return 'temp-ok';
}

async function apiFetch(path, method = 'GET', body = null) {
  try {
    const opts = { method, headers: {} };
    if (body !== null) {
      opts.body = JSON.stringify(body);
      opts.headers['Content-Type'] = 'application/json';
    }
    const r = await fetch(path, opts);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

// ─── Card creation ────────────────────────────────────────────────────────────
function createCard(ip) {
  const card = document.createElement('div');
  card.className = 'pi-card';
  card.dataset.ip = ip;
  card.innerHTML = `
    <div class="card-top">
      <span class="status-dot"></span>
      <input class="label-input" placeholder="Student name" autocomplete="off">
      <button class="remove-btn" title="Remove from roster">&#x2715;</button>
    </div>
    <div class="card-host"></div>
    <div class="card-ip">${ip}</div>
    <div class="metric-row">
      <span class="metric-lbl">CPU</span>
      <div class="bar"><div class="bar-fill cpu"></div></div>
      <span class="metric-val" data-m="cpu">--%</span>
    </div>
    <div class="metric-row">
      <span class="metric-lbl">RAM</span>
      <div class="bar"><div class="bar-fill mem"></div></div>
      <span class="metric-val" data-m="mem">--%</span>
    </div>
    <div class="badge-strip">
      <span class="pill" data-m="temp">--°C</span>
      <span class="pill" data-m="disk">Disk --%</span>
      <span class="pill" data-m="uptime">--</span>
      <span class="pill" data-m="load">-- load</span>
    </div>
    <div class="offline-label">Offline</div>
    <div class="card-footer" data-m="ts">--</div>
  `;

  // Drag
  card.addEventListener('mousedown', onCardMouseDown);

  // Label save on blur / enter
  const input = card.querySelector('.label-input');
  input.addEventListener('keydown', e => { if (e.key === 'Enter') input.blur(); });
  input.addEventListener('blur', async () => {
    await apiFetch(`/api/label/${ip}`, 'POST', { label: input.value.trim() });
  });
  // Stop drag starting from the input
  input.addEventListener('mousedown', e => e.stopPropagation());

  // Remove button
  card.querySelector('.remove-btn').addEventListener('click', async () => {
    await apiFetch(`/api/remove/${ip}`, 'POST');
    card.remove();
    delete cards[ip];
    updateHostCount();
  });

  return card;
}

// ─── Card update (metrics only — never touches position) ──────────────────────
function updateCard(ip, host, layout) {
  const card = cards[ip];
  if (!card) return;

  const status = host.status || 'offline';
  card.className = 'pi-card ' + status;
  card.querySelector('.status-dot').className = 'status-dot ' + status;

  // Label (don't overwrite while user is typing)
  const input = card.querySelector('.label-input');
  if (document.activeElement !== input) {
    input.value = host.label || '';
  }

  // Hostname
  const hostname = host.hostname && host.hostname !== ip ? host.hostname : '';
  card.querySelector('.card-host').textContent = hostname;

  const h = host.health || {};

  if (status === 'online' && h.cpu !== undefined) {
    const cpu  = h.cpu  ?? 0;
    const mem  = h.mem  ?? 0;
    const temp = h.temp ?? 0;
    const disk = h.disk ?? 0;

    card.querySelector('.bar-fill.cpu').style.width = Math.min(cpu, 100) + '%';
    card.querySelector('.bar-fill.mem').style.width = Math.min(mem, 100) + '%';
    card.querySelector('[data-m="cpu"]').textContent = cpu.toFixed(0) + '%';
    card.querySelector('[data-m="mem"]').textContent = mem.toFixed(0) + '%';

    const tc = card.querySelector('[data-m="temp"]');
    tc.textContent = temp.toFixed(1) + '°C';
    tc.className = 'pill ' + tempClass(temp);

    card.querySelector('[data-m="disk"]').textContent   = 'Disk ' + disk + '%';
    card.querySelector('[data-m="uptime"]').textContent = fmtUptime(h.uptime);
    card.querySelector('[data-m="load"]').textContent   = (h.load1 ?? 0).toFixed(2) + ' load';

    const ts = host.last_seen || '';
    card.querySelector('[data-m="ts"]').textContent = ts ? 'Updated ' + ts.slice(11) : '';
  }

  // Apply layout position only once — on first render (card has no position yet)
  if (!card.style.left && layout) {
    card.style.left = layout.x + 'px';
    card.style.top  = layout.y + 'px';
  }
}

// ─── Drag ─────────────────────────────────────────────────────────────────────
function onCardMouseDown(e) {
  // Ignore clicks on buttons / inputs
  if (e.target.closest('button, input')) return;
  dragging = e.currentTarget;
  const rect = dragging.getBoundingClientRect();
  const cwr  = document.getElementById('canvas-wrapper');
  dragOX = e.clientX - rect.left;
  dragOY = e.clientY - rect.top;
  dragging.classList.add('dragging');
  e.preventDefault();
}

document.addEventListener('mousemove', e => {
  if (!dragging) return;
  const cw   = document.getElementById('canvas-wrapper');
  const rect = cw.getBoundingClientRect();
  let x = e.clientX - rect.left + cw.scrollLeft - dragOX;
  let y = e.clientY - rect.top  + cw.scrollTop  - dragOY;
  x = Math.max(0, x);
  y = Math.max(0, y);
  dragging.style.left = x + 'px';
  dragging.style.top  = y + 'px';
});

document.addEventListener('mouseup', async () => {
  if (!dragging) return;
  dragging.classList.remove('dragging');
  const ip = dragging.dataset.ip;
  const x  = parseInt(dragging.style.left,  10);
  const y  = parseInt(dragging.style.top,   10);
  dragging = null;
  await apiFetch('/api/layout', 'POST', { ip, x, y });
});

// ─── Header controls ──────────────────────────────────────────────────────────
function updateHostCount() {
  document.getElementById('host-count').textContent =
    Object.keys(cards).length + ' host' + (Object.keys(cards).length === 1 ? '' : 's');
}

function applyLockState(isLocked) {
  locked = isLocked;
  const btn   = document.getElementById('lock-btn');
  const badge = document.getElementById('lock-badge');
  if (isLocked) {
    btn.textContent    = 'Unlock Roster';
    btn.classList.add('active');
    badge.textContent  = 'Locked';
    badge.className    = 'badge locked';
  } else {
    btn.textContent    = 'Lock Roster';
    btn.classList.remove('active');
    badge.textContent  = 'Scanning';
    badge.className    = 'badge scanning';
  }
}

document.getElementById('lock-btn').addEventListener('click', async () => {
  const res = await apiFetch(locked ? '/api/unlock' : '/api/lock', 'POST');
  if (res) applyLockState(res.locked);
});

document.getElementById('scan-btn').addEventListener('click', async () => {
  const btn = document.getElementById('scan-btn');
  btn.textContent = 'Scanning…';
  btn.disabled = true;
  await apiFetch('/api/scan', 'POST');
  setTimeout(() => { btn.textContent = 'Scan Now'; btn.disabled = false; }, 2000);
});

document.getElementById('arrange-btn').addEventListener('click', async () => {
  const res = await apiFetch('/api/arrange', 'POST');
  if (res?.ok) {
    // Re-fetch layout and apply positions
    const status = await apiFetch('/api/status');
    if (status) {
      for (const [ip, pos] of Object.entries(status.layout || {})) {
        const card = cards[ip];
        if (card) {
          card.style.left = pos.x + 'px';
          card.style.top  = pos.y + 'px';
        }
      }
    }
  }
});

document.getElementById('clear-btn').addEventListener('click', async () => {
  const res = await apiFetch('/api/clear_offline', 'POST');
  if (res?.ok) {
    // Remove offline cards from DOM
    for (const [ip, card] of Object.entries(cards)) {
      if (card.classList.contains('offline')) {
        card.remove();
        delete cards[ip];
      }
    }
    updateHostCount();
  }
});

// ─── Main poll loop ───────────────────────────────────────────────────────────
async function poll() {
  const data = await apiFetch('/api/status');
  if (!data) return;

  // Update scan range display
  const c = data.config || {};
  const rangeStr = `${c.base || ''}.${c.start || ''}–${c.base || ''}.${c.end || ''}`;
  document.getElementById('scan-range').textContent  = rangeStr;
  document.getElementById('hint-range').textContent  = rangeStr;
  document.getElementById('last-updated').textContent =
    data.updated ? 'Updated ' + data.updated.slice(11) : '';

  applyLockState(data.locked);

  const canvas = document.getElementById('canvas');
  const hint   = document.getElementById('empty-hint');

  // Add or update cards
  for (const [ip, host] of Object.entries(data.roster || {})) {
    const layout = (data.layout || {})[ip] || { x: 20, y: 20 };
    if (!cards[ip]) {
      const card = createCard(ip);
      cards[ip] = card;
      canvas.appendChild(card);
    }
    updateCard(ip, host, layout);
  }

  // Remove cards that left the roster
  for (const ip of Object.keys(cards)) {
    if (!data.roster?.[ip]) {
      cards[ip].remove();
      delete cards[ip];
    }
  }

  updateHostCount();
  hint.style.display = Object.keys(cards).length > 0 ? 'none' : 'flex';
}

// Start polling
poll();
setInterval(poll, 10_000);

// ─── Instructor zone ─────────────────────────────────────────────────────────

const INSTR_X = 1600;
const INSTR_Y = 20;

let fortiConnected   = false;
let fortiRefreshTimer = null;

function fmtBytes(b) {
  if (!b || b < 0) return '0 B';
  if (b < 1024)        return b + ' B';
  if (b < 1048576)     return (b / 1024).toFixed(1) + ' K';
  if (b < 1073741824)  return (b / 1048576).toFixed(1) + ' M';
  return (b / 1073741824).toFixed(2) + ' G';
}

function fmtSpeed(mbps) {
  if (!mbps) return '';
  return mbps >= 1000 ? (mbps / 1000) + 'G' : mbps + 'M';
}

// ── Port → student card connection lines ───────────────────────────────────

let activePortLine = null;
let activePortRow  = null;

function getCanvasPos(el) {
  // getBoundingClientRect is already in viewport space, so subtracting the
  // canvas rect gives canvas-local coordinates without any scroll adjustment.
  const canvas = document.getElementById('canvas').getBoundingClientRect();
  const rect   = el.getBoundingClientRect();
  return {
    x: rect.left - canvas.left,
    y: rect.top  - canvas.top,
    w: rect.width,
    h: rect.height,
  };
}

function drawPortLine(ip, rowEl) {
  clearPortLine();
  const studentCard = cards[ip];
  if (!studentCard) return;

  const fc = getCanvasPos(document.getElementById('forti-card'));
  const sc = getCanvasPos(studentCard);
  const rc = getCanvasPos(rowEl);

  // Line: right edge of student card → y-center of clicked port row on FortiGate card
  const x1 = sc.x + sc.w;
  const y1 = sc.y + sc.h / 2;
  const x2 = fc.x;
  const y2 = rc.y + rc.h / 2;

  const svg  = document.getElementById('canvas-svg');
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  line.setAttribute('x1', x1);
  line.setAttribute('y1', y1);
  line.setAttribute('x2', x2);
  line.setAttribute('y2', y2);
  line.id = 'port-line';
  svg.appendChild(line);

  activePortLine = line;
  activePortRow  = rowEl;
  rowEl.classList.add('port-row-active');
}

function clearPortLine() {
  activePortLine?.remove();
  activePortLine = null;
  activePortRow?.classList.remove('port-row-active');
  activePortRow  = null;
}

// Clicking anywhere on the canvas (outside the FortiGate card) clears the line
document.getElementById('canvas').addEventListener('click', e => {
  if (!activePortLine) return;
  if (!e.target.closest('#forti-card')) clearPortLine();
});

function buildInstructorZone() {
  const zone = document.createElement('div');
  zone.id = 'instructor-zone';
  zone.style.left = INSTR_X + 'px';
  zone.style.top  = INSTR_Y + 'px';

  zone.innerHTML = `
    <div class="zone-label">INSTRUCTOR AREA · FRONT OF ROOM</div>
    <div id="forti-card" class="forti-card">
      <div class="forti-header">
        <div>
          <div class="forti-title">&#9632; FortiGate Firewall</div>
          <div class="forti-ip">192.168.0.10</div>
        </div>
        <div id="forti-status-badge"></div>
      </div>
      <div id="forti-body">
        <div class="forti-auth-prompt">
          Click to authenticate
          <span>Retrieves live port statistics via FortiOS REST API</span>
        </div>
      </div>
    </div>
  `;

  // SVG overlay for port connection lines
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.id = 'canvas-svg';
  svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  svg.innerHTML = `
    <defs>
      <filter id="line-glow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="2.5" result="blur"/>
        <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>`;

  document.getElementById('canvas').appendChild(svg);
  document.getElementById('canvas').appendChild(zone);
  document.getElementById('forti-card').addEventListener('click', e => {
    if (e.target.closest('.forti-disc-btn')) return;
    if (!fortiConnected) openFortiModal();
  });
}

// ── Modal ──────────────────────────────────────────────────────────────────

function openFortiModal() {
  document.getElementById('forti-err').textContent = '';
  // Pre-fill saved credentials
  try {
    const saved = JSON.parse(localStorage.getItem('forti-creds') || '{}');
    if (saved.user)  document.getElementById('forti-user').value  = saved.user;
    if (saved.pass)  document.getElementById('forti-pass').value  = saved.pass;
    if (saved.token) document.getElementById('forti-token').value = saved.token;
  } catch { /* ignore */ }
  document.getElementById('forti-modal').classList.add('open');
  document.getElementById('forti-user').focus();
}

function closeFortiModal() {
  document.getElementById('forti-modal').classList.remove('open');
}

async function connectForti() {
  const user  = document.getElementById('forti-user').value.trim();
  const pass  = document.getElementById('forti-pass').value.trim();
  const token = document.getElementById('forti-token').value.trim();
  const errEl = document.getElementById('forti-err');

  if (!token && (!user || !pass)) {
    errEl.textContent = 'Enter username + password, or an API token.';
    return;
  }

  const btn = document.getElementById('forti-connect-btn');
  btn.textContent = 'Connecting…';
  btn.disabled    = true;

  const res = await apiFetch('/api/fortigate/connect', 'POST', { user, pass, token });
  btn.textContent = 'Connect';
  btn.disabled    = false;

  if (res?.ok) {
    // Persist credentials so the modal pre-fills on next open
    try {
      localStorage.setItem('forti-creds', JSON.stringify({ user, pass, token }));
    } catch { /* ignore */ }
    fortiConnected = true;
    closeFortiModal();
    document.getElementById('forti-status-badge').innerHTML =
      '<span class="forti-connected-badge">CONNECTED</span>';
    await refreshFortiStats();
    fortiRefreshTimer = setInterval(refreshFortiStats, 5_000);
  } else {
    errEl.textContent = res?.err || 'Connection failed.';
  }
}

// ── Stats fetch + render ───────────────────────────────────────────────────

let _fortiCache     = null;   // last successful interface list
let _fortiCacheTime = 0;      // timestamp of that fetch
const FORTI_CACHE_MS = 4900;  // reuse cache if called within ~5 s

async function refreshFortiStats() {
  const now = Date.now();
  if (_fortiCache && (now - _fortiCacheTime) < FORTI_CACHE_MS) {
    renderFortiInterfaces(_fortiCache);
    return;
  }
  const res = await apiFetch('/api/fortigate/interfaces');
  if (!res) return;
  if (res.auth_required) {
    fortiConnected = false;
    clearInterval(fortiRefreshTimer);
    _fortiCache = null;
    renderFortiDisconnected();
    return;
  }
  if (res.ok) {
    _fortiCache     = res.interfaces || [];
    _fortiCacheTime = Date.now();
    renderFortiInterfaces(_fortiCache);
  }
}

function renderFortiInterfaces(ifaces) {
  // Skip loopback, tunnel/ssl, and aggregate meta-interfaces
  const SKIP = new Set(['ssl.root', 'VDOM_LINK', 'npu0_vlink0', 'npu0_vlink1']);
  const shown = ifaces.filter(i =>
    i.type !== 'loopback' &&
    !SKIP.has(i.name) &&
    !i.name.startsWith('vdom-link') &&
    !i.name.startsWith('npu')
  );

  const rows = shown.map(iface => {
    const up    = iface.link === true || iface.status === 'up';
    const alias = iface.alias ? `<span class="iface-alias"> ${iface.alias}</span>` : '';
    const speed = fmtSpeed(iface.speed);
    const ip    = iface.ip ? iface.ip.split('/')[0] : (iface.ipv4_address || '--');
    const rx    = fmtBytes(iface.rx_bytes);
    const tx    = fmtBytes(iface.tx_bytes);
    const rxPkt = (iface.rx_packets || 0).toLocaleString();
    const txPkt = (iface.tx_packets || 0).toLocaleString();
    const errs  = (iface.rx_errors || 0) + (iface.tx_errors || 0);

    const bareIp = iface.ip ? iface.ip.split('/')[0] : '';
    const hasCard = bareIp && !!cards[bareIp];

    return `
      <div class="iface-row${hasCard ? ' linkable' : ''}" data-ip="${bareIp}">
        <span class="iface-name">${iface.name}${alias}</span>
        <span class="iface-link ${up ? 'link-up' : 'link-down'}">${up ? '● UP' : '○ DN'}</span>
        <span class="iface-speed">${speed}</span>
        <span class="iface-ip">${ip}</span>
        <div class="iface-traffic">
          <span class="rx">&#8595; ${rx}</span>
          <span class="tx">&#8593; ${tx}</span>
          <span>${rxPkt} / ${txPkt} pkts</span>
          ${errs ? `<span class="err">&#9888; ${errs} err</span>` : ''}
        </div>
      </div>`;
  }).join('');

  document.getElementById('forti-body').innerHTML = `
    <div class="iface-table">${rows || '<div class="forti-auth-prompt">No interfaces returned</div>'}</div>
    <button class="btn-secondary forti-disc-btn">Disconnect</button>
  `;

  // Port → student card line: click to show, click again to clear
  document.querySelector('.iface-table').addEventListener('click', e => {
    const row = e.target.closest('.iface-row.linkable');
    if (!row) return;
    if (activePortRow === row) { clearPortLine(); return; }
    drawPortLine(row.dataset.ip, row);
  });

  document.querySelector('.forti-disc-btn').addEventListener('click', async e => {
    e.stopPropagation();
    clearPortLine();
    await apiFetch('/api/fortigate/disconnect', 'POST');
    fortiConnected = false;
    clearInterval(fortiRefreshTimer);
    _fortiCache = null;
    renderFortiDisconnected();
  });
}

function renderFortiDisconnected() {
  document.getElementById('forti-status-badge').innerHTML = '';
  document.getElementById('forti-body').innerHTML = `
    <div class="forti-auth-prompt">
      Click to authenticate
      <span>Retrieves live port statistics via FortiOS REST API</span>
    </div>`;
}

// ── Header button: toggle between classroom and instructor views ───────────

let instrView = false;

document.getElementById('goto-instr-btn').addEventListener('click', () => {
  const wrapper = document.getElementById('canvas-wrapper');
  const btn     = document.getElementById('goto-instr-btn');
  if (!instrView) {
    wrapper.scrollTo({ left: INSTR_X - 40, top: 0, behavior: 'smooth' });
    instrView = true;
    btn.innerHTML = '&#9664; Classroom';
    btn.title     = 'Return to student area';
  } else {
    wrapper.scrollTo({ left: 0, top: 0, behavior: 'smooth' });
    instrView = false;
    btn.innerHTML = '&#9654; Instructor';
    btn.title     = 'Jump to instructor area';
  }
});

// ── Wire up modal buttons ──────────────────────────────────────────────────

document.getElementById('forti-connect-btn').addEventListener('click', connectForti);
document.getElementById('forti-cancel-btn').addEventListener('click', closeFortiModal);

// Close modal on backdrop click
document.getElementById('forti-modal').addEventListener('click', e => {
  if (e.target === document.getElementById('forti-modal')) closeFortiModal();
});

// Enter key in modal fields
['forti-user', 'forti-pass', 'forti-token'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', e => {
    if (e.key === 'Enter') connectForti();
    if (e.key === 'Escape') closeFortiModal();
  });
});

// ── Init instructor zone ───────────────────────────────────────────────────

buildInstructorZone();

// ── Secret demo loader (dot in top-left) ───────────────────────────────────

let demoLoaded = false;

document.getElementById('secret-dot').addEventListener('click', async () => {
  if (demoLoaded) return;
  demoLoaded = true;
  const res = await apiFetch('/api/demo', 'POST');
  if (res?.ok) poll();
});
