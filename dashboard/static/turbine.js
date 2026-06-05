// turbine.js — self-contained Wind Turbine panel for the Overview tab.
//
// Reads /api/turbine (the dashboard proxies the Pi host's otlab-modbus-io
// :502 over Modbus) and renders a live spinning turbine + temperature.
// Deliberately isolated from app.js so it can't affect the existing card
// rendering. The panel hides itself when the turbine I/O is unreachable.
(function () {
  let speed = 0, angle = 0, last = (typeof performance !== 'undefined' ? performance.now() : 0);

  function frame(now) {
    const dt = (now - last) / 1000; last = now;
    angle += speed * 3.0 * dt;               // deg/s tracks motor %
    const r = document.getElementById('turb-rotor');
    if (r) r.setAttribute('transform', 'translate(150,116) rotate(' + angle.toFixed(1) + ')');
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  async function poll() {
    const row = document.getElementById('turbine-row');
    if (!row) return;
    try {
      const res = await fetch('/api/turbine', { cache: 'no-store' });
      const s = await res.json();
      if (!s || !s.up) { row.style.display = 'none'; speed = 0; return; }
      row.style.display = '';
      speed = s.motor_a || 0;
      const tf = document.getElementById('turb-tf');
      const tc = document.getElementById('turb-tc');
      const ma = document.getElementById('turb-ma');
      const rl = document.getElementById('turb-relay');
      const st = document.getElementById('turb-status');
      if (tf) tf.textContent = (s.temp_f != null ? s.temp_f.toFixed(1) : '--');
      if (tc) tc.textContent = (s.temp_c != null ? s.temp_c.toFixed(1) : '--');
      if (ma) ma.textContent = (s.motor_a || 0) + '%';
      if (rl) { rl.textContent = s.relay ? 'ON' : 'off'; rl.style.color = s.relay ? '#ffb84a' : ''; }
      if (st) st.textContent = (s.motor_a ? 'cooling' : 'idle') + ' · Modbus ' + (s.host || '') + ':502';
    } catch (e) {
      row.style.display = 'none'; speed = 0;
    }
  }
  poll();
  setInterval(poll, 1000);
})();
