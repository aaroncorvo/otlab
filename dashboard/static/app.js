// OTLab dashboard — client. Polls /api/status every 3 s, renders cards,
// fires reboots on demand.

const ROW_ORDER = {
  net:      ['wan', 'mgmt_gw', 'fw'],
  plc:      ['softplc-1', 'softplc-2', 'honeypot-host'],
  honeypot: ['siemens-PS4', 'schneider-M340', 'rockwell-CHEM'],
};

const REBOOTABLE = new Set(['softplc-1', 'softplc-2', 'honeypot-host']);

// ---------- card renderers ----------

function pingStatusText(c) {
  if (c.up === true)  return c.ms != null ? `UP · ${c.ms} ms` : 'UP';
  if (c.up === false) return 'DOWN';
  return '–';
}

function plcExtras(name, c) {
  const rows = [];
  if (c.plc_ui !== undefined) {
    rows.push(kv('OpenPLC :8080', c.plc_ui ? '✓' : '✗', c.plc_ui ? 'ok' : 'down'));
  }

  if (c.modbus && c.modbus.hr) {
    const r = c.modbus.hr;
    if (name === 'softplc-1') {
      // [tank, temp, press, hb, link_ok, link_loss]
      rows.push(kv('tank',      `${(r[0]/10).toFixed(1)} %`));
      rows.push(kv('temp',      `${(r[1]/10).toFixed(1)} °F`));
      rows.push(kv('press',     `${(r[2]/10).toFixed(1)} PSI`));
      rows.push(kv('heartbeat', String(r[3])));
      rows.push(kv('link_ok',   String(r[4]), r[4] === 1 ? 'ok' : 'down'));
      rows.push(kv('link_loss', String(r[5]), r[5] > 0 ? 'down' : 'ok'));
    } else if (name === 'softplc-2') {
      // [tank, temp, press, hb] + coils [running, hi_alarm]
      const co = c.modbus.co || [false, false];
      rows.push(kv('tank',      `${(r[0]/10).toFixed(1)} %`));
      rows.push(kv('temp',      `${(r[1]/10).toFixed(1)} °F`));
      rows.push(kv('press',     `${(r[2]/10).toFixed(1)} PSI`));
      rows.push(kv('heartbeat', String(r[3])));
      rows.push(kv('RUN',       co[0] ? 'YES' : 'NO',  co[0] ? 'ok' : 'down'));
      rows.push(kv('HI_ALARM',  co[1] ? 'YES' : 'NO',  co[1] ? 'down' : 'ok'));
    }
  } else if (c.up && (name === 'softplc-1' || name === 'softplc-2')) {
    rows.push(kv('Modbus', 'no read', 'down'));
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

function rebootButton(name) {
  if (!REBOOTABLE.has(name)) return '';
  return `<button class="reboot" data-host="${name}">Reboot ${name}</button>`;
}

function kv(key, val, cls = '') {
  return `<div class="key">${key}</div><div class="val ${cls}">${val}</div>`;
}

function renderCard(name, c) {
  if (!c) c = { up: null, label: name };
  const cls = c.up === true ? 'ok' : (c.up === false ? 'down' : '');

  // Honeypot personas: a TCP-port probe failing is a stronger signal than
  // a flaky ICMP reply, so degrade to 'warn' if pings are mixed with
  // services.
  let stateCls = cls;
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
                      : pingStatusText(c);

  return `
    <div class="card ${stateCls} ${isMinimal ? 'minimal' : ''}">
      <div class="top">
        <span class="name">${c.label || name}</span>
        <span class="status">${status}</span>
      </div>
      ${plcExtras(name, c)}
      ${svcsExtras(c)}
      ${rebootButton(name)}
    </div>`;
}

// ---------- polling + reboot ----------

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
        target.innerHTML = names.map(n => renderCard(n, j.cards[n])).join('');
      }
    }
    bindRebootButtons();
  } catch (e) {
    document.getElementById('updated').textContent = 'fetch error: ' + e.message;
  }
}

function bindRebootButtons() {
  document.querySelectorAll('button.reboot').forEach(btn => {
    btn.addEventListener('click', () => doReboot(btn.dataset.host, btn));
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

// ---------- boot ----------

setInterval(refresh, 3000);
refresh();
