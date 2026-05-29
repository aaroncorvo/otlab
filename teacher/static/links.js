// OTLab Links Hub — fetches /api/links to render layout, then /api/links/status
// every 15s to update the live status pills.
//
// Status semantics:
//   200/2xx/3xx -> "up"   (amber pill, amber left border)
//   401/403     -> "auth" (warmer amber pill — service is alive, needs login)
//   anything else (including 0 connect-fail) -> "down" (red)
//   empty probe_url (e.g. SSH link) -> "skip" (gray)

const PROBE_INTERVAL_MS = 15000;

let catalogue = null;     // last /api/links response
let pollTimer = null;

function classifyStatus(code) {
  if (code === null || code === undefined) return 'skip';
  const c = Number(code);
  if (!c) return 'down';
  if (c === 401 || c === 403) return 'auth';
  if (c >= 200 && c < 400) return 'up';
  return 'down';
}

function pillLabel(code, cls) {
  if (cls === 'skip') return '—';
  if (cls === 'down' && !code) return 'DOWN';
  return String(code);
}

function tileHTML(link) {
  // Defense-in-depth: every templated value is escapeHtml'd, even
  // server-generated keys/urls. classifyStatus returns one of a fixed
  // set of class names so it doesn't need escaping.
  const cls   = 'skip';   // initial state; updated after probe
  const label = escapeHtml(link.label);
  const url   = escapeHtml(link.url);
  const key   = escapeHtml(link.key);
  const note  = link.note ? escapeHtml(link.note) : '';
  return `
    <a class="link-tile ${cls}" href="${url}" target="_blank" rel="noopener" data-key="${key}">
      <div class="link-tile-head">
        <span class="link-tile-label">${label}</span>
        <span class="status-pill ${cls}" data-pill="${key}">—</span>
      </div>
      <div class="link-tile-url">${url}</div>
      ${note ? `<div class="link-tile-note">${note}</div>` : ''}
    </a>
  `;
}

function renderGrid(containerId, links) {
  const el = document.getElementById(containerId);
  if (!links || !links.length) {
    el.innerHTML = '<div class="link-placeholder">(none)</div>';
    return;
  }
  el.innerHTML = links.map(tileHTML).join('');
}

function renderHostCards(area, hosts, emptyMsg) {
  if (!hosts.length) {
    area.innerHTML = `<div class="link-placeholder">${emptyMsg}</div>`;
    return;
  }
  area.innerHTML = hosts.map(h => `
    <div class="student-card ${h.status === 'offline' ? 'offline' : ''}">
      <div class="student-card-head">
        <span class="student-card-name">${escapeHtml(h.label)}</span>
        <span class="student-card-meta">${escapeHtml(h.hostname || '')} · ${escapeHtml(h.ip)}</span>
      </div>
      <div class="link-grid">
        ${h.links.map(tileHTML).join('')}
      </div>
    </div>
  `).join('');
}

function renderStudents(students) {
  document.getElementById('student-count').textContent =
    students.length ? `· ${students.length} discovered` : '· none yet';
  renderHostCards(
    document.getElementById('students-area'),
    students,
    'No students in the roster yet. Hit Scan Now on the Monitor page, or wait for the next sweep (every 30 s by default).',
  );
}

function renderHoneypots(honeypots) {
  document.getElementById('honeypot-count').textContent =
    honeypots.length ? `· ${honeypots.length} deployed` : '· none configured';
  renderHostCards(
    document.getElementById('honeypots-area'),
    honeypots,
    'No honeypots configured. Set HONEYPOT_IPS env or label a discovered Pi with "honeypot" to surface it here.',
  );
}

function collectProbeItems() {
  if (!catalogue) return [];
  const items = [];
  for (const l of catalogue.teacher.links) {
    if (l.probe_url) items.push({key: l.key, probe_url: l.probe_url});
  }
  for (const l of catalogue.esp32) {
    if (l.probe_url) items.push({key: l.key, probe_url: l.probe_url});
  }
  for (const s of catalogue.students) {
    for (const l of s.links) {
      if (l.probe_url) items.push({key: l.key, probe_url: l.probe_url});
    }
  }
  for (const h of (catalogue.honeypots || [])) {
    for (const l of h.links) {
      if (l.probe_url) items.push({key: l.key, probe_url: l.probe_url});
    }
  }
  return items;
}

async function probeAll() {
  const items = collectProbeItems();
  if (!items.length) return;
  let res, body;
  try {
    res = await fetch('/api/links/status', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({urls: items}),
    });
    body = await res.json();
  } catch (e) {
    console.error('probe failed', e);
    return;
  }
  const statuses = body.statuses || {};
  let up = 0, down = 0, auth = 0, skip = 0;
  for (const [key, code] of Object.entries(statuses)) {
    const cls = classifyStatus(code);
    if (cls === 'up') up++;
    else if (cls === 'auth') auth++;
    else if (cls === 'skip') skip++;
    else down++;
    const pill = document.querySelector(`[data-pill="${cssEscape(key)}"]`);
    if (pill) {
      pill.classList.remove('up', 'auth', 'down', 'skip');
      pill.classList.add(cls);
      pill.textContent = pillLabel(code, cls);
    }
    const tile = document.querySelector(`a.link-tile[data-key="${cssEscape(key)}"]`);
    if (tile) {
      tile.classList.remove('up', 'auth', 'down', 'skip');
      tile.classList.add(cls);
    }
  }
  document.getElementById('status-summary').textContent =
    `${up} up · ${auth} auth · ${down} down`;
  document.getElementById('updated').textContent =
    `last probed ${new Date().toLocaleTimeString()}`;
}

async function loadCatalogue() {
  let res, body;
  try {
    res = await fetch('/api/links');
    body = await res.json();
  } catch (e) {
    console.error('catalogue fetch failed', e);
    return;
  }
  catalogue = body;
  document.getElementById('teacher-host').textContent = `· ${body.teacher.host}`;
  renderGrid('teacher-grid', body.teacher.links);
  renderGrid('esp32-grid', body.esp32);
  renderHoneypots(body.honeypots || []);
  renderStudents(body.students);
  probeAll();
}

// Trivial HTML escape — link.label/url come from the teacher panel itself
// (we control them) but be safe anyway.
function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// CSS.escape polyfill — handles dots in IP addresses inside data-attr selectors
function cssEscape(s) {
  if (window.CSS && CSS.escape) return CSS.escape(s);
  return String(s).replace(/[^a-zA-Z0-9_-]/g, ch => '\\' + ch);
}

// Wire up
document.getElementById('refresh-btn').addEventListener('click', () => {
  loadCatalogue();
});

// Initial load + periodic probe
loadCatalogue();
pollTimer = setInterval(probeAll, PROBE_INTERVAL_MS);
