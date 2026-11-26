/* =========================================================
   app.js — Zimbra Mail Tracker Intelligence System
   ========================================================= */

'use strict';

// ── State ─────────────────────────────────────────────────
const state = {
  page: 1,
  perPage: 50,
  sort: 'timestamp',
  sortDir: 'desc',
  search: '',
  statusFilter: '',
  mainClassFilter: '',
  domainFilter: '',
  dateFrom: '',
  dateTo: '',
  selectedRows: new Set(),
  totalRows: 0,
  totalPages: 1,
  isDemo: true,
  columns: {
    message_id:    { label: 'Message ID',    visible: true },
    timestamp:     { label: 'Timestamp',     visible: true },
    subject:       { label: 'Email Subject',  visible: true },
    auth_domain:   { label: 'Auth Domain',   visible: true },
    auth_username: { label: 'Auth Username', visible: true },
    identity:      { label: 'Identity',      visible: true },
    from:          { label: 'Sender',        visible: true },
    to:            { label: 'Recipient',     visible: true },
    main_class:    { label: 'Main Class',    visible: true },
    status:        { label: 'Status',        visible: true },
  },
};

// ── Clock ──────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  const str = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  document.getElementById('clock').textContent = str;
}
setInterval(updateClock, 1000);
updateClock();

// ── Toast ──────────────────────────────────────────────────
function showToast(msg, type = 'success', duration = 3000) {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type === 'error' ? 'error' : type === 'warning' ? 'warning' : ''}`;
  el.textContent = (type === 'success' ? '✔ ' : type === 'error' ? '✖ ' : '⚠ ') + msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── Loading ────────────────────────────────────────────────
function setLoading(show, text = 'PROCESSING...') {
  const ov = document.getElementById('loading-overlay');
  const tx = document.getElementById('loading-text');
  tx.textContent = text;
  ov.classList.toggle('show', show);
}

// ── API ────────────────────────────────────────────────────
async function fetchLogs() {
  const params = new URLSearchParams({
    page: state.page,
    per_page: state.perPage,
    sort: state.sort,
    dir: state.sortDir,
  });
  if (state.search)          params.set('q', state.search);
  if (state.statusFilter)    params.set('status', state.statusFilter);
  if (state.mainClassFilter) params.set('main_class', state.mainClassFilter);
  if (state.domainFilter)    params.set('auth_domain', state.domainFilter);
  if (state.dateFrom)        params.set('date_from', state.dateFrom);
  if (state.dateTo)          params.set('date_to', state.dateTo);

  const res = await fetch(`/api/logs?${params}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Render ─────────────────────────────────────────────────
function renderTable(rows) {
  const tbody = document.getElementById('log-tbody');
  tbody.innerHTML = '';

  if (!rows.length) {
    tbody.innerHTML = `
      <tr><td colspan="20">
        <div class="empty-state">
          <div class="empty-state-icon">🔍</div>
          <div class="empty-state-title">NO RECORDS FOUND</div>
          <div class="empty-state-sub">Adjust filters or upload a log file</div>
        </div>
      </td></tr>`;
    return;
  }

  const visibleCols = Object.entries(state.columns).filter(([,c]) => c.visible);

  rows.forEach((row, idx) => {
    const tr = document.createElement('tr');
    tr.dataset.idx = idx;
    if (state.selectedRows.has(row.queue_id + '_' + row.to)) tr.classList.add('selected');

    // checkbox cell
    const tdCheck = document.createElement('td');
    tdCheck.className = 'td-check';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    const rowKey = row.queue_id + '_' + row.to;
    cb.checked = state.selectedRows.has(rowKey);
    cb.addEventListener('change', () => {
      if (cb.checked) state.selectedRows.add(rowKey);
      else state.selectedRows.delete(rowKey);
      tr.classList.toggle('selected', cb.checked);
      updateSelectAllState();
      updateSelectionInfo();
    });
    tdCheck.appendChild(cb);
    tr.appendChild(tdCheck);

    visibleCols.forEach(([col]) => {
      const td = document.createElement('td');
      td.title = row[col] || '';

      switch (col) {
        case 'message_id':
          td.innerHTML = `<span class="msg-id td-monospace">${esc(row.message_id || row.queue_id)}</span>`;
          break;
        case 'timestamp':
          td.innerHTML = `<span class="td-monospace" style="color:var(--green-dim)">${esc(row.timestamp)}</span>`;
          break;
        case 'status':
          td.innerHTML = statusBadge(row.status);
          break;
        case 'main_class':
          td.innerHTML = mainClassBadge(row.main_class);
          break;
        case 'subject':
          td.className = 'td-subject';
          td.title = row.subject || 'Subject tidak tersedia pada log sumber';
          td.innerHTML = `<span class="subject-text">${esc(row.subject || '— Subject tidak tercatat —')}</span>`;
          break;
        case 'from':
        case 'to':
        case 'identity':
        case 'auth_username':
          td.innerHTML = `<span style="color:#87ceab">${esc(row[col])}</span>`;
          break;
        case 'auth_domain':
          td.innerHTML = `<span style="color:#5ba88a">${esc(row[col])}</span>`;
          break;
        default:
          td.textContent = row[col] || '';
      }
      tr.appendChild(td);
    });

    // Click row → open detail
    tr.addEventListener('click', (e) => {
      if (e.target.type === 'checkbox') return;
      openDetail(row);
    });

    tbody.appendChild(tr);
  });
}

function statusBadge(status) {
  const map = {
    delivered: 'badge-delivered',
    sent: 'badge-sent',
    bounced: 'badge-bounced',
    deferred: 'badge-deferred',
  };
  const cls = map[status] || 'badge-unknown';
  return `<span class="badge ${cls}">${esc(status || 'unknown')}</span>`;
}

function mainClassBadge(cls) {
  if (cls === 'spam') return `<span class="badge-spam">SPAM</span>`;
  return `<span class="badge-notspam">NOT-SPAM</span>`;
}

function truncate(str, n) {
  if (!str) return '';
  return str.length > n ? str.slice(0, n) + '…' : str;
}

function esc(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Table Header ───────────────────────────────────────────
function renderHeader() {
  const thead = document.getElementById('log-thead-row');
  thead.innerHTML = '';

  // checkbox col
  const thCheck = document.createElement('th');
  thCheck.className = 'th-check';
  const selAll = document.createElement('input');
  selAll.type = 'checkbox';
  selAll.id = 'select-all';
  selAll.addEventListener('change', () => {
    const checks = document.querySelectorAll('#log-tbody input[type=checkbox]');
    checks.forEach(c => {
      c.checked = selAll.checked;
      const tr = c.closest('tr');
      if (selAll.checked) { tr.classList.add('selected'); }
      else { tr.classList.remove('selected'); }
    });
    // update state
    if (!selAll.checked) state.selectedRows.clear();
    else {
      document.querySelectorAll('#log-tbody tr').forEach(tr => {
        const cb = tr.querySelector('input[type=checkbox]');
        if (cb) {
          const idx = tr.dataset.idx;
          // just mark all visible as selected via key combo
        }
      });
    }
    updateSelectionInfo();
  });
  thCheck.appendChild(selAll);
  thead.appendChild(thCheck);

  const colDefs = {
    message_id:    { label: 'Message ID', w: '130px' },
    timestamp:     { label: 'Timestamp ⇅', w: '140px', sortKey: 'timestamp' },
    auth_domain:   { label: 'Auth Domain ⇅', sortKey: 'auth_domain' },
    auth_username: { label: 'Auth Username ⇅', sortKey: 'auth_username' },
    identity:      { label: 'Identity ⇅', sortKey: 'identity' },
    from:          { label: 'Sender ⇅', sortKey: 'from' },
    to:            { label: 'Recipient ⇅', sortKey: 'to' },
    main_class:    { label: 'Main Class ⇅', sortKey: 'main_class', w: '100px' },
    subject:       { label: 'Email Subject', sortKey: 'subject', w: '300px' },
    status:        { label: 'Status ⇅', sortKey: 'status', w: '90px' },
  };

  Object.entries(state.columns).forEach(([col, cfg]) => {
    if (!cfg.visible) return;
    const def = colDefs[col] || { label: col };
    const th = document.createElement('th');
    th.className = 'sortable';
    if (def.w) th.style.width = def.w;

    if (state.sort === (def.sortKey || col)) {
      th.classList.add(state.sortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
    }

    th.innerHTML = `${def.label.replace(' ⇅','')}<span class="sort-arrow"></span>`;
    if (def.sortKey) {
      th.addEventListener('click', () => {
        if (state.sort === def.sortKey) {
          state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          state.sort = def.sortKey;
          state.sortDir = 'desc';
        }
        state.page = 1;
        load();
      });
    }
    thead.appendChild(th);
  });
}

// ── Stats Cards ────────────────────────────────────────────
function renderStats(stats) {
  document.getElementById('stat-total').textContent    = stats.total.toLocaleString();
  document.getElementById('stat-delivered').textContent = stats.delivered.toLocaleString();
  document.getElementById('stat-bounced').textContent  = stats.bounced.toLocaleString();
  document.getElementById('stat-deferred').textContent = stats.deferred.toLocaleString();
  document.getElementById('stat-spam').textContent     = stats.spam.toLocaleString();
}

// ── Mini stats in sidebar
function renderSidebarStats(stats) {
  document.getElementById('side-total').textContent    = stats.total.toLocaleString();
  document.getElementById('side-bounced').textContent  = stats.bounced.toLocaleString();
  document.getElementById('side-deferred').textContent = stats.deferred.toLocaleString();
  document.getElementById('side-spam').textContent     = stats.spam.toLocaleString();
}

// ── Pagination ─────────────────────────────────────────────
function renderPagination(total, page, totalPages, perPage) {
  const start = (page - 1) * perPage + 1;
  const end   = Math.min(page * perPage, total);
  document.getElementById('pag-info').innerHTML =
    `Showing <strong>${start.toLocaleString()}–${end.toLocaleString()}</strong> of <strong>${total.toLocaleString()}</strong> records`;

  const container = document.getElementById('pag-controls');
  container.innerHTML = '';

  const addBtn = (label, pg, disabled = false, active = false) => {
    const btn = document.createElement('button');
    btn.className = `page-btn${active ? ' active' : ''}`;
    btn.textContent = label;
    btn.disabled = disabled;
    btn.addEventListener('click', () => { state.page = pg; load(); });
    container.appendChild(btn);
  };

  addBtn('«', 1, page <= 1);
  addBtn('‹', page - 1, page <= 1);

  // page window
  const window_size = 5;
  let startPage = Math.max(1, page - Math.floor(window_size / 2));
  let endPage   = Math.min(totalPages, startPage + window_size - 1);
  if (endPage - startPage < window_size - 1) startPage = Math.max(1, endPage - window_size + 1);

  for (let p = startPage; p <= endPage; p++) {
    addBtn(p, p, false, p === page);
  }

  addBtn('›', page + 1, page >= totalPages);
  addBtn('»', totalPages, page >= totalPages);
}

// ── Chart (canvas bar chart, generik) ─────────────────────
// series: [{name, color, values[]}]; mode 'group' | 'stack'
function drawBarChart(canvas, labels, series, opts = {}) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
  const cssH = opts.height || parseInt(canvas.getAttribute('height')) || 120;
  canvas.style.height = cssH + 'px';
  canvas.width = Math.round(cssW * dpr); canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const mode = opts.mode || 'group';
  const n = labels.length;
  const padL = 34, padR = 8, padT = 10, padB = 18;
  const W = cssW - padL - padR, H = cssH - padT - padB;
  const totals = labels.map((_, i) => series.reduce((s, se) => s + (se.values[i] || 0), 0));
  const maxV = Math.max(1, ...(mode === 'stack' ? totals : series.flatMap(se => se.values)));
  const nice = (v) => { const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; };
  const top = nice(maxV);

  ctx.font = '9px "Share Tech Mono", monospace';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const y = padT + H - (H / 4) * i;
    ctx.strokeStyle = 'rgba(0,61,16,0.55)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, Math.round(y) + 0.5); ctx.lineTo(padL + W, Math.round(y) + 0.5); ctx.stroke();
    ctx.fillStyle = '#2a5c38'; ctx.textAlign = 'right';
    ctx.fillText(Math.round(top / 4 * i).toLocaleString(), padL - 5, y);
  }
  const gw = W / Math.max(n, 1);
  const inner = Math.min(gw * 0.72, 46);
  const bw = mode === 'stack' ? inner : Math.max(2, (inner - (series.length - 1) * 2) / series.length);
  const boxes = [];
  labels.forEach((lab, i) => {
    const x0 = padL + gw * i + (gw - inner) / 2;
    let yAcc = padT + H;
    series.forEach((se, k) => {
      const v = se.values[i] || 0;
      const h = v ? Math.max(2, (v / top) * H) : 0;
      const x = mode === 'stack' ? x0 : x0 + k * (bw + 2);
      const y = mode === 'stack' ? yAcc - h : padT + H - h;
      if (h) { ctx.fillStyle = se.color; ctx.fillRect(x, y, bw, h); }
      if (mode === 'stack') yAcc -= h;
    });
    boxes.push({ x: padL + gw * i, w: gw, i });
    const every = Math.ceil(n / Math.max(1, Math.floor(W / 46)));
    if (i % every === 0) {
      ctx.fillStyle = '#4a8a5c'; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      ctx.fillText(lab, padL + gw * i + gw / 2, cssH - 4); ctx.textBaseline = 'middle';
    }
  });
  // tooltip
  let tip = canvas.parentElement.querySelector('.chart-tip');
  if (!tip) { tip = document.createElement('div'); tip.className = 'chart-tip'; canvas.parentElement.style.position = 'relative'; canvas.parentElement.appendChild(tip); }
  canvas.onmousemove = (e) => {
    const r = canvas.getBoundingClientRect(); const mx = e.clientX - r.left;
    const bx = boxes.find(b => mx >= b.x && mx < b.x + b.w);
    if (!bx) { tip.style.display = 'none'; return; }
    const title = (opts.tipLabels || labels)[bx.i];
    tip.innerHTML = `<b>${esc(title)}</b>` + series.map(se =>
      `<div><i style="background:${se.color}"></i>${esc(se.name)}: <b>${(se.values[bx.i] || 0).toLocaleString()}</b></div>`).join('');
    tip.style.display = 'block';
    const left = Math.min(Math.max(0, bx.x + bx.w / 2 - tip.offsetWidth / 2), r.width - tip.offsetWidth);
    tip.style.left = left + 'px'; tip.style.top = (canvas.offsetTop + 4) + 'px';
  };
  canvas.onmouseleave = () => { tip.style.display = 'none'; };
}

let _lastChart = [];
function renderChart(chartData) {
  _lastChart = chartData || [];
  const canvas = document.getElementById('chart-canvas');
  if (!canvas) return;
  const dayName = (d) => new Date(d + 'T00:00:00').toLocaleDateString('id-ID', { weekday: 'short', day: '2-digit', month: 'short' });
  drawBarChart(canvas, _lastChart.map(d => d.date.slice(5)), [
    { name: 'Delivered', color: '#00ff41', values: _lastChart.map(d => d.delivered) },
    { name: 'Bounced', color: '#ff2020', values: _lastChart.map(d => d.bounced) },
    { name: 'Spam', color: '#cc44ff', values: _lastChart.map(d => d.spam) },
  ], { height: 110, tipLabels: _lastChart.map(d => dayName(d.date)) });
  const tot = _lastChart.reduce((a, d) => a + d.delivered, 0);
  const el = document.getElementById('chart-total');
  if (el) el.textContent = tot ? `· ${tot.toLocaleString()} terkirim` : '';
}

// ── Column chooser ─────────────────────────────────────────
function buildColChooser() {
  const menu = document.getElementById('col-chooser-menu');
  menu.innerHTML = '';
  Object.entries(state.columns).forEach(([col, cfg]) => {
    const div = document.createElement('div');
    div.className = 'col-option';
    div.innerHTML = `<input type="checkbox" ${cfg.visible ? 'checked' : ''} />
                     <span>${cfg.label}</span>`;
    div.querySelector('input').addEventListener('change', e => {
      state.columns[col].visible = e.target.checked;
      renderHeader();
      load();
    });
    menu.appendChild(div);
  });

  document.getElementById('col-chooser-btn').addEventListener('click', e => {
    e.stopPropagation();
    menu.classList.toggle('open');
  });
  document.addEventListener('click', () => menu.classList.remove('open'));
}

// ── Selection info ─────────────────────────────────────────
function updateSelectionInfo() {
  const info = document.getElementById('selection-info');
  const n = state.selectedRows.size;
  info.textContent = n > 0 ? `${n} row(s) selected` : '';
}

function updateSelectAllState() {
  const selAll = document.getElementById('select-all');
  if (!selAll) return;
  const checks = document.querySelectorAll('#log-tbody input[type=checkbox]');
  const checked = [...checks].filter(c => c.checked).length;
  selAll.indeterminate = checked > 0 && checked < checks.length;
  selAll.checked = checked === checks.length && checks.length > 0;
}

// ── Main load function ─────────────────────────────────────
async function load() {
  setLoading(true, 'RETRIEVING INTELLIGENCE DATA...');
  try {
    const data = await fetchLogs();
    state.totalRows  = data.total;
    state.totalPages = data.total_pages;
    state.isDemo     = data.is_demo;

    renderHeader();
    renderTable(data.rows);
    renderStats(data.stats);
    renderSidebarStats(data.stats);
    renderPagination(data.total, data.page, data.total_pages, state.perPage);
    renderChart(data.chart);

    // Demo indicator
    const demoBadge = document.getElementById('demo-badge');
    if (demoBadge) demoBadge.style.display = data.is_demo ? 'inline-block' : 'none';

  } catch (err) {
    showToast('Failed to load log data: ' + err.message, 'error');
  } finally {
    setLoading(false);
  }
}

// ── Upload ─────────────────────────────────────────────────
async function uploadFile(file) {
  if (!file) return;
  setLoading(true, `PARSING LOG FILE: ${file.name}`);
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    showToast(`✔ ${data.count.toLocaleString()} record dimuat dari ${data.filename || 'file'}`);
    state.page = 1;
    await load();
  } catch (err) {
    showToast('Upload failed: ' + err.message, 'error');
    setLoading(false);
  }
}

// ── Export CSV ─────────────────────────────────────────────
function currentFilterParams() {
  const p = new URLSearchParams({ sort: state.sort, dir: state.sortDir });
  if (state.search)          p.set('q', state.search);
  if (state.statusFilter)    p.set('status', state.statusFilter);
  if (state.mainClassFilter) p.set('main_class', state.mainClassFilter);
  if (state.domainFilter)    p.set('auth_domain', state.domainFilter);
  if (state.dateFrom)        p.set('date_from', state.dateFrom);
  if (state.dateTo)          p.set('date_to', state.dateTo);
  return p;
}

function exportCSV() {
  const p = currentFilterParams();
  const filtered = [...p.keys()].some(k => !['sort', 'dir'].includes(k));
  window.location = '/api/export/csv?' + p.toString();
  showToast(filtered ? `Exporting CSV (filter aktif, ${state.totalRows.toLocaleString()} baris)…` : 'Exporting CSV…');
}

function resetFilters() {
  Object.assign(state, { search: '', statusFilter: '', mainClassFilter: '', domainFilter: '', dateFrom: '', dateTo: '',
                         sort: 'timestamp', sortDir: 'desc', page: 1 });
  document.getElementById('search-input').value = '';
  document.getElementById('status-filter').value = '';
  document.getElementById('class-filter').value = '';
  renderHeader();
  load();
  showToast('Filter direset');
}

async function syncNow(btn) {
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = '⟳ Syncing…';
  try {
    const res = await fetch('/api/sync', { method: 'POST', headers: { 'X-ZMT-Request': 'sync' } });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
    const before = d.sync && d.sync.last_sync;
    // tunggu siklus sync selesai (maks ~30 detik)
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const s2 = await fetch('/api/session', { cache: 'no-store' }).then(r => r.json()).catch(() => null);
      if (s2 && s2.sync && s2.sync.last_sync !== before && !s2.sync.running) {
        showToast(`Sync selesai: ${s2.sync.files_processed} file, ${s2.sync.total_records} record diperbarui`);
        break;
      }
    }
    await load();
  } catch (e) {
    showToast('Sync gagal: ' + e.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
}

// ── Detail Modal ───────────────────────────────────────────
function fmtBytes(n) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  if (n < 1024) return n + ' B';
  const u = ['KB', 'MB', 'GB'];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 ? 2 : 1) + ' ' + u[i];
}

// "'Nama' <a@b>" -> {name, addr}; tanda kutip literal di nama dibuang
function splitAddr(s) {
  const m = String(s || '').match(/^(.*?)\s*<([^>]+)>\s*$/);
  let name = m ? m[1] : '', addr = m ? m[2] : String(s || '');
  name = name.trim().replace(/^(['"])(.*)\1$/, '$2').trim();
  if (name.toLowerCase() === addr.toLowerCase()) name = '';
  return { name, addr: addr.trim() };
}

const dField = (label, valueHtml, cls = '') =>
  `<div class="dfield ${cls}"><div class="dlabel">${label}</div><div class="dvalue">${valueHtml}</div></div>`;
const dText = (v) => (v === null || v === undefined || v === '') ? '<span class="dmuted">—</span>' : esc(v);
const dSection = (title, inner, extra = '') =>
  `<section class="dsection ${extra}"><h4 class="dsection-title">${title}</h4>${inner}</section>`;

function openDetail(row) {
  const modal = document.getElementById('detail-modal');
  const body  = document.getElementById('modal-body');
  const sizeTxt = row.size ? `${fmtBytes(row.size)} <span class="dmuted">(${row.size.toLocaleString()} bytes)</span>` : dText('');

  body.innerHTML =
    dSection('Identitas pesan', `
      <div class="dgrid">
        ${dField('Message ID', `<span class="mono-wrap">${dText(row.message_id)}</span>`, 'span-2')}
        ${dField('Queue ID', dText(row.queue_id))}
        ${dField('Timestamp', dText(row.timestamp))}
        ${dField('Status', statusBadge(row.status))}
        ${dField('Main class', mainClassBadge(row.main_class))}
      </div>`) +
    dSection('Pengirim', `
      <div class="dgrid">
        ${dField('Sender', dText(row.from))}
        ${dField('Auth username', dText(row.auth_username))}
        ${dField('Auth domain', dText(row.auth_domain))}
        ${dField('Identity', dText(row.identity))}
      </div>`) +
    dSection('Subject', `<div class="dvalue dsubject">${row.subject ? esc(row.subject) : '<span class="dmuted">— Subject tidak tercatat —</span>'}</div>`) +
    `<div id="detail-extra"><div class="dloading"><span class="dspinner"></span>Memuat To / CC / lampiran…</div></div>` +
    dSection('Teknis', `
      <div class="dgrid">
        ${dField('Recipient (baris ini)', dText(row.to))}
        ${dField('Relay', `<span class="mono-wrap">${dText(row.relay)}</span>`)}
        ${dField('Ukuran pesan', sizeTxt + (_zmtLimits && row.size ? `<div class="dlimit-inline">${limitBar(row.size, _zmtLimits.effective_message_limit)}</div>` : ''))}
      </div>
      ${row.reason ? `<div class="dlabel" style="margin-top:10px">Reason / error</div><div class="reason-box">${esc(row.reason)}</div>` : ''}`);

  body.scrollTop = 0;
  modal.classList.add('show');
  loadDetailExtra(row);
}

let _detailSeq = 0;
async function loadDetailExtra(row) {
  const seq = ++_detailSeq;
  const params = new URLSearchParams();
  if (row.message_id) params.set('message_id', row.message_id);
  if (row.queue_id) params.set('queue_id', row.queue_id);
  let d;
  try {
    const res = await fetch(`/api/message?${params}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    d = await res.json();
  } catch (e) {
    if (seq !== _detailSeq) return;
    const box = document.getElementById('detail-extra');
    if (box) box.innerHTML = `<div class="dloading">Gagal memuat detail tambahan: ${esc(e.message)}</div>`;
    return;
  }
  if (seq !== _detailSeq) return;   // modal sudah berganti ke pesan lain
  const box = document.getElementById('detail-extra');
  if (!box) return;

  const chips = (list) => {
    if (!list || !list.length) return '<span class="dmuted">—</span>';
    return `<div class="chips">${list.map(a => {
      const p = splitAddr(a);
      return `<span class="chip" title="${esc(a)}">${p.name ? `<span class="chip-name">${esc(p.name)}</span>` : ''}<span class="chip-addr">${esc(p.addr)}</span></span>`;
    }).join('')}</div>`;
  };

  // Penerima (header)
  let recipientsHtml;
  if (d.headers_available) {
    recipientsHtml = `
      <div class="drow"><div class="drow-label">To <span class="count">${d.to.length}</span></div>${chips(d.to)}</div>
      <div class="drow"><div class="drow-label">CC <span class="count">${d.cc.length}</span></div>${chips(d.cc)}</div>`;
  } else {
    recipientsHtml = `<div class="dnote">Header tidak tersedia (pesan tidak tersimpan di mailbox lokal). Penerima aktual ada di bagian Pengiriman.</div>`;
  }

  // Lampiran
  let attHtml;
  if (d.attachments === null || d.attachments === undefined) {
    attHtml = `<div class="dnote">${d.headers_available ? 'Tidak diketahui (blob terlalu besar untuk dianalisis).' : 'Tidak diketahui.'}</div>`;
  } else if (!d.attachments.length) {
    attHtml = `<div class="dnote">Tidak ada lampiran.</div>`;
  } else {
    attHtml = `
      <div class="dsummary"><b>${d.att_count}</b> file · total <b>${fmtBytes(d.att_total)}</b></div>
      ${_zmtLimits && _zmtLimits.effective_attachment_limit ? `<div class="dlimit"><span class="dlabel">Terhadap batas lampiran efektif</span>${limitBar(d.att_total, _zmtLimits.effective_attachment_limit)}</div>` : ''}
      <div class="mini-wrap"><table class="mini-table">
        <colgroup><col><col style="width:30%"><col style="width:92px"></colgroup>
        <thead><tr><th>Nama file</th><th>Tipe</th><th class="num">Ukuran</th></tr></thead>
        <tbody>${d.attachments.map(a => `
          <tr><td class="wrap">${esc(a.name)}${a.inline ? ' <span class="kind-badge kind-inline">INLINE</span>' : ''}</td>
              <td class="wrap dmuted">${esc(a.type)}</td>
              <td class="num">${fmtBytes(a.size)}</td></tr>`).join('')}
        </tbody></table></div>`;
  }

  // Pengiriman (envelope)
  const kindBadge = (k) => k === 'to' ? '<span class="kind-badge kind-to">TO</span>'
    : k === 'cc' ? '<span class="kind-badge kind-cc">CC</span>'
    : k === 'bcc' ? '<span class="kind-badge kind-bcc" title="Tidak tercantum di header To/Cc: BCC, alias, atau anggota distribution list">BCC/ALIAS</span>'
    : '<span class="dmuted">—</span>';
  const rec = d.recipients || [];
  const cnt = (s) => rec.filter(r => r.status === s).length;
  const statusSummary = ['delivered', 'sent', 'deferred', 'bounced'].filter(s => cnt(s))
    .map(s => `${statusBadge(s)} <b>${cnt(s)}</b>`).join('<span class="sep"></span>');
  const deliveryHtml = rec.length ? `
      <div class="dsummary">${rec.length} penerima${statusSummary ? '<span class="sep"></span>' + statusSummary : ''}</div>
      <div class="mini-wrap"><table class="mini-table">
        <colgroup><col><col style="width:86px"><col style="width:104px"><col style="width:146px"></colgroup>
        <thead><tr><th>Penerima</th><th>Jenis</th><th>Status</th><th>Waktu</th></tr></thead>
        <tbody>${rec.map(r => `<tr>
          <td class="wrap">${esc(r.recipient)}${r.reason ? `<div class="row-reason">${esc(r.reason)}</div>` : ''}</td>
          <td>${kindBadge(r.kind)}</td><td>${statusBadge(r.status)}</td>
          <td class="dmuted nowrap">${esc(r.timestamp)}</td></tr>`).join('')}
        </tbody></table></div>`
    : `<div class="dnote">Tidak ada data pengiriman.</div>`;

  box.innerHTML =
    dSection('Penerima (header)', recipientsHtml) +
    dSection(`Lampiran`, attHtml) +
    dSection('Pengiriman (envelope)', deliveryHtml);
}

// ── ZMT Doctor ─────────────────────────────────────────────
async function loadDoctorReport() {
  const summary = document.getElementById('doctor-summary');
  const checks = document.getElementById('doctor-checks');
  if (!summary || !checks) return;
  try {
    const res = await fetch('/api/doctor/latest', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const payload = await res.json();
    const report = payload.report;
    if (!report) {
      summary.textContent = 'Belum ada laporan. Jalankan sudo zmt-doctor di VPS.';
      checks.innerHTML = '<div style="font-size:10px;color:var(--muted);">Laporan akan muncul otomatis setelah script mengirim hasil pemeriksaan.</div>';
      return;
    }
    const overall = String(report.overall || 'UNKNOWN').toUpperCase();
    const color = overall === 'PASS' ? 'var(--green)' : overall === 'CRITICAL' ? 'var(--red)' : 'var(--amber)';
    summary.innerHTML = '<span style="color:' + color + ';font-weight:bold;">' + esc(overall) + '</span> · ' +
      esc(report.hostname || 'VPS') + ' · Pemeriksaan: ' + esc(report.checked_at || '—');
    checks.innerHTML = (report.checks || []).map(item => {
      const status = String(item.status || 'UNKNOWN').toUpperCase();
      const itemColor = status === 'PASS' ? 'var(--green)' : status === 'FAIL' ? 'var(--red)' : 'var(--amber)';
      return '<div style="padding:9px;background:#041508;border:1px solid var(--border);border-radius:3px;min-width:0;">' +
        '<div style="display:flex;justify-content:space-between;gap:8px;font-size:10px;">' +
        '<strong style="color:#c8f5d4;">' + esc(item.name || 'Check') + '</strong>' +
        '<span style="color:' + itemColor + ';font-weight:bold;">' + esc(status) + '</span></div>' +
        '<div style="font-size:10px;color:var(--muted);margin-top:5px;overflow-wrap:anywhere;">' + esc(item.detail || '') + '</div></div>';
    }).join('') || '<div style="font-size:10px;color:var(--muted);">Tidak ada item pemeriksaan.</div>';
  } catch (err) {
    summary.textContent = 'Gagal mengambil laporan: ' + err.message;
  }
}

// ── Batas ukuran pesan & lampiran ──────────────────────────
let _zmtLimits = null;

function limitBar(used, limit) {
  if (!used || !limit) return '';
  const pct = Math.min(100, (used / limit) * 100);
  const cls = pct >= 90 ? 'lb-red' : pct >= 70 ? 'lb-amber' : 'lb-green';
  return `<div class="limit-bar ${cls}" title="${pct.toFixed(1)}% dari batas">
    <div class="limit-bar-fill" style="width:${pct.toFixed(1)}%"></div></div>
    <div class="limit-bar-caption">${fmtBytes(used)} dari ${fmtBytes(limit)} · ${pct.toFixed(0)}%</div>`;
}

async function loadLimits(force) {
  const summary = document.getElementById('limits-summary');
  const cards = document.getElementById('limits-cards');
  const usageBox = document.getElementById('limits-usage');
  if (!summary || !cards) return;
  if (force) summary.textContent = 'Memperbarui dari Zimbra…';
  let d;
  try {
    const res = await fetch('/api/limits' + (force ? '?refresh=1' : ''), { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    d = await res.json();
  } catch (e) {
    summary.textContent = 'Gagal memuat batas: ' + e.message;
    return;
  }
  const L = d.limits || {}, U = d.usage || {};
  _zmtLimits = L;
  const editBtn = document.getElementById('limits-edit-btn');
  if (editBtn) editBtn.hidden = !d.editable;
  renderLimitsAudit(d.audit || []);
  const errs = (L.errors || []).length;
  summary.innerHTML = `Sumber: Postfix <code>message_size_limit</code> &amp; <code>zmprov</code> · diperbarui ${esc(L.fetched_at || '—')}` +
    ` · <span class="limits-ro">${d.editable ? 'DAPAT DIUBAH · AUDIT' : 'READ-ONLY'}</span>` + (errs ? ` · <span style="color:var(--red)">${errs} error: ${esc(L.errors.join('; '))}</span>` : '') +
    (L.consistent === false ? ` · <span style="color:var(--amber)">zimbraMtaMaxMessageSize ≠ Postfix (belum diterapkan?)</span>` : '');

  const card = (title, value, sub, tone = '') => `
    <div class="limit-card ${tone}">
      <div class="limit-card-title">${title}</div>
      <div class="limit-card-value">${value}</div>
      <div class="limit-card-sub">${sub}</div>
    </div>`;
  const perFile = L.file_upload_max_size_per_file;
  const exts = L.blocked_extensions || [];
  cards.innerHTML =
    card('Maks ukuran pesan (MTA)', fmtBytes(L.effective_message_limit),
         'Total email termasuk lampiran · <code>zimbraMtaMaxMessageSize</code>', 'tone-primary') +
    card('Maks lampiran efektif', '≈ ' + fmtBytes(L.effective_attachment_limit),
         'Setelah encoding base64 (+~37%) dari batas MTA') +
    card('Maks upload web client', fmtBytes(L.file_upload_max_size),
         'Batas lampiran saat kirim via webmail · <code>zimbraFileUploadMaxSize</code>', 'tone-warn') +
    card('Maks per file (COS default)', perFile ? fmtBytes(perFile) : 'Tidak dibatasi terpisah',
         '<code>zimbraFileUploadMaxSizePerFile</code>' + (perFile ? '' : ' = 0')) +
    card('Maks konten tersimpan', fmtBytes(L.mail_content_max_size), '<code>zimbraMailContentMaxSize</code>') +
    card('Blokir lampiran', L.attachments_blocked ? 'Semua diblokir' : (exts.length ? `${exts.length} ekstensi` : 'Tidak ada'),
         exts.length ? esc(exts.slice(0, 12).join(', ')) + (exts.length > 12 ? '…' : '') : '<code>zimbraMtaBlockedExtension</code> kosong',
         L.attachments_blocked ? 'tone-warn' : '');

  if (usageBox) {
    const big = U.largest_attachments;
    usageBox.innerHTML = `
      <div class="limits-usage-title">PEMAKAIAN ${U.days || 30} HARI TERAKHIR</div>
      <div class="limits-usage-grid">
        <div class="lu-item"><div class="lu-label">Lampiran terbesar (per pesan)</div>
          ${big ? limitBar(big.att_total, L.effective_attachment_limit) +
            `<div class="lu-note" title="${esc(big.subject || '')}">${big.att_count} file · ${esc(big.subject || '(tanpa subject)')}</div>` : '<div class="dmuted">—</div>'}</div>
        <div class="lu-item"><div class="lu-label">Pesan terbesar</div>
          ${U.largest_message_size ? limitBar(U.largest_message_size, L.effective_message_limit) : '<div class="dmuted">—</div>'}</div>
        <div class="lu-item lu-stats">
          <div><b>${(U.messages_with_attachments ?? 0).toLocaleString()}</b><span>pesan berlampiran</span></div>
          <div><b class="${U.near_limit_messages ? 'amber' : ''}">${U.near_limit_messages ?? '—'}</b><span>pesan ≥ 80% batas</span></div>
          <div><b class="${U.size_rejections ? 'red' : ''}">${U.size_rejections ?? 0}</b><span>ditolak karena ukuran</span></div>
        </div>
      </div>
      <div class="limits-footnote">${d.editable ? 'Gunakan <b>✎ UBAH BATAS</b> untuk mengubah nilai (wajib password, tercatat di riwayat). Postfix diperbarui otomatis oleh zmconfigd ±1 menit.' : 'Perubahan dari dashboard nonaktif. Ubah lewat Admin Console atau <code>zmprov mcf …</code>.'}</div>`;
  }
}

function renderLimitsAudit(rows) {
  const box = document.getElementById('limits-audit');
  if (!box) return;
  if (!rows.length) { box.innerHTML = ''; return; }
  const name = (a) => ({ zimbraMtaMaxMessageSize: 'Pesan (MTA)', zimbraMailContentMaxSize: 'Konten tersimpan',
    zimbraFileUploadMaxSize: 'Upload webmail', zimbraFileUploadMaxSizePerFile: 'Per file (COS)' }[a] || a);
  box.innerHTML = `<div class="limits-usage-title">RIWAYAT PERUBAHAN</div>
    <div class="audit-list">${rows.map(r => `
      <div class="audit-row ${r.ok ? '' : 'audit-fail'}">
        <span class="audit-ts">${esc(r.ts)}</span>
        <span class="audit-who">${esc(r.username || '?')} · ${esc(r.ip || '')}</span>
        <span class="audit-what">${(r.changes || []).map(c => `${esc(name(c.attr))}: ${fmtBytes(c.old)} → <b>${fmtBytes(c.new)}</b>`).join(' · ') || '—'}</span>
        <span class="audit-res">${r.ok ? 'OK' : 'GAGAL'}</span>
      </div>`).join('')}</div>`;
}

// ── Form ubah batas ────────────────────────────────────────
const lf = {
  el: (id) => document.getElementById(id),
  previewOk: false,
  values() {
    const n = (id) => { const v = lf.el(id).value.trim(); return v === '' ? null : Number(v); };
    return { message_mb: n('lf-message'), upload_mb: n('lf-upload'), per_file_mb: n('lf-perfile') };
  },
  status(msg, tone = '') { const s = lf.el('lf-status'); s.className = 'lf-status ' + tone; s.innerHTML = msg; },
  invalidate() {
    lf.previewOk = false; lf.el('lf-apply').disabled = true; lf.el('lf-preview').hidden = true;
    const v = lf.values();
    const eff = v.message_mb ? Math.max(0, v.message_mb * 1048576 - 16384) / ((4 / 3) * (78 / 76)) : 0;
    lf.el('lf-derived').innerHTML = v.message_mb ? `→ lampiran efektif ≈ <b>${fmtBytes(Math.floor(eff))}</b> (setelah base64)` : '';
    const w = [];
    if (v.upload_mb && eff && v.upload_mb * 1048576 > eff) w.push(`Upload webmail ${v.upload_mb} MB melebihi lampiran efektif ≈${fmtBytes(Math.floor(eff))}; lampiran sebesar itu akan ditolak MTA.`);
    if (v.per_file_mb && v.upload_mb && v.per_file_mb > v.upload_mb) w.push('Maks per file tidak boleh melebihi maks upload.');
    const wb = lf.el('lf-warnings'); wb.hidden = !w.length; wb.innerHTML = w.map(x => `<div>⚠ ${esc(x)}</div>`).join('');
    lf.status('');
  },
  open() {
    const L = _zmtLimits || {};
    const mb = (b) => b === null || b === undefined ? '' : Math.round(b / 1048576);
    lf.el('lf-message').value = mb(L.zimbra_mta_max_message_size);
    lf.el('lf-upload').value = mb(L.file_upload_max_size);
    lf.el('lf-perfile').value = mb(L.file_upload_max_size_per_file || 0);
    lf.el('lf-message-cur').textContent = 'sekarang ' + fmtBytes(L.zimbra_mta_max_message_size);
    lf.el('lf-upload-cur').textContent = 'sekarang ' + fmtBytes(L.file_upload_max_size);
    lf.el('lf-perfile-cur').textContent = 'sekarang ' + (L.file_upload_max_size_per_file ? fmtBytes(L.file_upload_max_size_per_file) : '0 (tidak dibatasi)');
    lf.el('lf-password').value = '';
    lf.invalidate();
    lf.el('limits-modal').classList.add('show');
    setTimeout(() => lf.el('lf-message').focus(), 50);
  },
  close() { lf.el('limits-modal').classList.remove('show'); lf.el('lf-password').value = ''; },
  async send(dryRun) {
    const pwd = lf.el('lf-password').value;
    if (!pwd) { lf.status('Masukkan password dashboard untuk konfirmasi.', 'err'); lf.el('lf-password').focus(); return null; }
    const btns = ['lf-preview-btn', 'lf-apply', 'lf-cancel'].map(lf.el);
    btns.forEach(b => b.disabled = true);
    lf.status(dryRun ? 'Memeriksa…' : '<span class="dspinner"></span> Menerapkan ke Zimbra…');
    try {
      const res = await fetch('/api/limits', {
        method: 'POST', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', 'X-ZMT-Request': 'limits' },
        body: JSON.stringify({ ...lf.values(), password: pwd, dry_run: dryRun }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok && !d.changes) throw new Error(d.error || `HTTP ${res.status}`);
      return d;
    } catch (e) {
      lf.status('✕ ' + esc(e.message), 'err');
      return null;
    } finally {
      lf.el('lf-preview-btn').disabled = false; lf.el('lf-cancel').disabled = false;
      lf.el('lf-apply').disabled = !lf.previewOk;
    }
  },
  renderChanges(d) {
    const box = lf.el('lf-preview');
    const ch = d.changes || [];
    box.hidden = false;
    box.innerHTML = ch.length
      ? `<div class="lf-preview-title">Akan diubah:</div>` + ch.map(c =>
          `<div class="lf-change"><code>${esc(c.attr)}</code> <span class="dmuted">(${esc(c.scope)})</span><span>${fmtBytes(c.old)} → <b>${fmtBytes(c.new)}</b></span></div>`).join('')
      : `<div class="dmuted">Tidak ada perubahan: nilai sama dengan konfigurasi sekarang.</div>`;
    if ((d.warnings || []).length) {
      const wb = lf.el('lf-warnings'); wb.hidden = false;
      wb.innerHTML = d.warnings.map(x => `<div>⚠ ${esc(x)}</div>`).join('');
    }
    return ch.length;
  },
};

function initLimitsForm() {
  const modal = lf.el('limits-modal');
  if (!modal) return;
  lf.el('limits-edit-btn').addEventListener('click', lf.open);
  lf.el('limits-modal-close').addEventListener('click', lf.close);
  lf.el('lf-cancel').addEventListener('click', lf.close);
  modal.addEventListener('click', e => { if (e.target === modal) lf.close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && modal.classList.contains('show')) lf.close(); });
  ['lf-message', 'lf-upload', 'lf-perfile'].forEach(id => lf.el(id).addEventListener('input', lf.invalidate));
  lf.el('lf-preview-btn').addEventListener('click', async () => {
    const d = await lf.send(true);
    if (!d) return;
    const n = lf.renderChanges(d);
    lf.previewOk = n > 0;
    lf.el('lf-apply').disabled = !lf.previewOk;
    lf.status(n ? 'Periksa perubahan di atas, lalu klik <b>Terapkan</b>.' : 'Tidak ada yang perlu diterapkan.', n ? 'ok' : '');
  });
  lf.el('limits-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!lf.previewOk) { lf.status('Klik Pratinjau terlebih dahulu.', 'err'); return; }
    const d = await lf.send(false);
    if (!d) return;
    lf.previewOk = false; lf.el('lf-apply').disabled = true;
    if (d.ok) {
      lf.el('lf-password').value = '';
      lf.status('✓ Diterapkan. ' + esc((d.log || []).slice(-1)[0] || ''), 'ok');
      showToast('Batas ukuran diperbarui');
      loadLimits(false);
      setTimeout(lf.close, 2500);
    } else {
      lf.status('✕ Gagal menerapkan: ' + esc((d.log || []).join(' | ') || d.error || ''), 'err');
      loadLimits(false);
    }
  });
}

// ── Firewall: UFW / Fail2ban ───────────────────────────────
const FW_LABEL = { ufw: 'UFW', fail2ban: 'Fail2ban' };
const FW_RISK = {
  ufw: 'Saat UFW mati, semua port yang sekarang tertutup terbuka ke internet, termasuk memcached 11211, admin console 7071, LMTP 7025 dan POP3/IMAP 110/143.',
  fail2ban: 'Saat Fail2ban mati, semua IP yang sedang diblokir dilepas dan brute-force SMTP/SSH tidak lagi diblokir.',
};
const fw = { data: null, editable: false, svc: null, action: null, minutes: 15, timer: null };

function fmtCountdown(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

async function loadFirewall() {
  const box = document.getElementById('fw-cards');
  if (!box) return;
  try {
    const res = await fetch('/api/firewall', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    renderFirewall(d);
  } catch (e) {
    box.innerHTML = `<div class="dnote">Status firewall tidak terbaca: ${esc(e.message)}</div>`;
  }
}

function renderFirewall(d) {
  fw.data = d.status; fw.editable = !!d.editable;
  const st = d.status || {};
  const card = (svc) => {
    const s = st[svc] || {};
    if (!s.available) return `<div class="fw-card"><div class="fw-name">${FW_LABEL[svc]}</div><div class="dnote">Tidak terpasang / tidak terbaca.</div></div>`;
    const on = !!s.active;
    let detail = '';
    if (svc === 'ufw') detail = on ? `${s.rules} aturan · default deny incoming` : 'Semua port terbuka';
    else detail = on ? (s.jails || []).map(j => `<span class="fw-jail">${esc(j.name)} <b>${j.banned}</b></span>`).join('') || 'Tidak ada jail'
                     : 'Tidak ada IP yang diblokir';
    const countdown = !on && s.reenable_at
      ? `<div class="fw-count" data-at="${s.reenable_at}">Aktif lagi dalam <b>${fmtCountdown(s.reenable_at - Date.now() / 1000)}</b></div>`
      : (!on ? `<div class="fw-count fw-count-none">Tidak ada jadwal aktif otomatis</div>` : '');
    const btn = !fw.editable ? '' : on
      ? `<button class="tb-btn fw-btn fw-btn-off" type="button" data-svc="${svc}" data-action="disable">⏻ DISABLE…</button>`
      : `<button class="tb-btn fw-btn fw-btn-on" type="button" data-svc="${svc}" data-action="enable">⏻ ENABLE</button>`;
    return `<div class="fw-card ${on ? 'on' : 'off'}">
      <div class="fw-row"><div class="fw-name">${FW_LABEL[svc]}</div><span class="fw-state ${on ? 'on' : 'off'}">${on ? 'AKTIF' : 'NONAKTIF'}</span></div>
      <div class="fw-detail">${svc === 'fail2ban' && on ? '<span class="dmuted">diblokir sekarang:</span> ' : ''}${detail}</div>
      ${countdown}
      <div class="fw-actions">${btn}</div>
    </div>`;
  };
  document.getElementById('fw-cards').innerHTML = card('ufw') + card('fail2ban');
  document.getElementById('fw-checked').textContent = fw.editable ? '' : '· hanya baca (login dashboard tidak aktif)';
  document.querySelectorAll('#fw-cards .fw-btn').forEach(b => b.addEventListener('click', () => fwOpen(b.dataset.svc, b.dataset.action)));
  const audit = d.audit || [];
  document.getElementById('fw-audit').innerHTML = audit.length ? `<div class="fw-audit-title">Riwayat</div>
    <div class="mini-wrap"><table class="mini-table"><thead><tr><th>Waktu</th><th>Oleh</th><th>Layanan</th><th>Aksi</th><th>Hasil</th></tr></thead><tbody>
    ${audit.map(a => `<tr><td>${esc(a.ts)}</td><td>${esc(a.username || '')}${a.ip ? ' <span class="dmuted">' + esc(a.ip) + '</span>' : ''}</td>
      <td>${esc(FW_LABEL[a.service] || a.service)}</td><td>${esc(a.action)}${a.minutes ? ' ' + a.minutes + ' mnt' : ''}</td>
      <td class="wrap">${a.ok ? '<span class="fw-ok">OK</span>' : '<span class="fw-fail">GAGAL</span> ' + esc((a.log || []).join(' | '))}</td></tr>`).join('')}
    </tbody></table></div>` : '';
  clearInterval(fw.timer);
  if (document.querySelector('#fw-cards .fw-count[data-at]')) {
    fw.timer = setInterval(() => {
      let expired = false;
      document.querySelectorAll('#fw-cards .fw-count[data-at]').forEach(el => {
        const left = parseInt(el.dataset.at) - Date.now() / 1000;
        el.querySelector('b').textContent = fmtCountdown(left);
        if (left < -5) expired = true;
      });
      if (expired) { clearInterval(fw.timer); setTimeout(loadFirewall, 3000); }
    }, 1000);
  }
}

function fwOpen(svc, action) {
  fw.svc = svc; fw.action = action;
  const off = action === 'disable';
  const el = id => document.getElementById(id);
  el('fw-modal-title').textContent = `${off ? '⏻ DISABLE' : '⏻ ENABLE'} ${FW_LABEL[svc].toUpperCase()}`;
  el('fw-intro').className = off ? 'lf-warnings' : 'lf-intro';
  el('fw-intro').innerHTML = off
    ? `<div>⚠ ${esc(FW_RISK[svc])}</div><div>Hanya untuk troubleshooting singkat.</div>`
    : (svc === 'ufw' ? 'UFW dinyalakan dengan aturan yang tersimpan. Dashboard menolak bila aturan SSH (22) atau port dashboard tidak ada, supaya akses admin tidak terkunci.'
                     : 'Fail2ban dinyalakan kembali dan jail dimuat ulang.');
  el('fw-duration-field').hidden = !off;
  el('fw-confirm-field').hidden = !off;
  el('fw-confirm-word').textContent = svc;
  el('fw-confirm').value = ''; el('fw-password').value = '';
  el('fw-status').textContent = ''; el('fw-status').className = 'lf-status';
  el('fw-apply').disabled = false;
  el('fw-apply').textContent = off ? 'Matikan sementara' : 'Aktifkan';
  el('fw-modal').classList.add('show');
  setTimeout(() => (off ? el('fw-confirm') : el('fw-password')).focus(), 50);
}

function fwClose() {
  document.getElementById('fw-modal').classList.remove('show');
  document.getElementById('fw-password').value = '';
}

function fwStatus(msg, cls = '') {
  const s = document.getElementById('fw-status');
  s.innerHTML = msg; s.className = 'lf-status' + (cls ? ' ' + cls : '');
}

function initFirewall() {
  const modal = document.getElementById('fw-modal');
  if (!modal) return;
  const el = id => document.getElementById(id);
  el('fw-modal-close').addEventListener('click', fwClose);
  el('fw-cancel').addEventListener('click', fwClose);
  modal.addEventListener('click', e => { if (e.target === modal) fwClose(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && modal.classList.contains('show')) fwClose(); });
  document.querySelectorAll('#fw-duration .seg-btn').forEach(b => b.addEventListener('click', () => {
    fw.minutes = parseInt(b.dataset.min);
    document.querySelectorAll('#fw-duration .seg-btn').forEach(x => { x.classList.toggle('active', x === b); x.setAttribute('aria-checked', x === b); });
  }));
  el('fw-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const off = fw.action === 'disable';
    if (off && el('fw-confirm').value.trim() !== fw.svc) { fwStatus(`Ketik <code>${esc(fw.svc)}</code> untuk konfirmasi.`, 'err'); el('fw-confirm').focus(); return; }
    const pwd = el('fw-password').value;
    if (!pwd) { fwStatus('Masukkan password dashboard.', 'err'); el('fw-password').focus(); return; }
    el('fw-apply').disabled = true;
    fwStatus('<span class="dspinner"></span> Menerapkan…');
    try {
      const body = { service: fw.svc, action: fw.action, password: pwd };
      if (off) { body.minutes = fw.minutes; body.confirm = el('fw-confirm').value.trim(); }
      const res = await fetch('/api/firewall', {
        method: 'POST', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', 'X-ZMT-Request': 'firewall' },
        body: JSON.stringify(body),
      });
      const d = await res.json().catch(() => ({}));
      if (d.status) renderFirewall(d);
      if (!res.ok || !d.ok) throw new Error(d.error || (d.log || []).join(' | ') || `HTTP ${res.status}`);
      el('fw-password').value = '';
      fwStatus('✓ ' + esc((d.log || []).join(' · ')), 'ok');
      showToast(`${FW_LABEL[fw.svc]} ${off ? 'dimatikan ' + fw.minutes + ' menit' : 'diaktifkan'}`, off ? 'warning' : 'success');
      setTimeout(fwClose, 1800);
    } catch (err) {
      fwStatus('✕ ' + esc(err.message), 'err');
      el('fw-apply').disabled = false;
    }
  });
}

// ── Views: Mail Tracker / Statistics / Alerts ──────────────
const VIEWS = ['tracker', 'stats', 'alerts'];
let _statsDays = 30;

function showView(v) {
  if (!VIEWS.includes(v)) v = 'tracker';
  document.getElementById('main-content').dataset.view = v;
  document.querySelectorAll('.nav-btn[data-view]').forEach(b => {
    const on = b.dataset.view === v;
    b.classList.toggle('active', on);
    b.setAttribute('aria-current', on ? 'page' : 'false');
  });
  if (location.hash !== '#' + v) history.replaceState(null, '', v === 'tracker' ? location.pathname : '#' + v);
  document.getElementById('main-content').scrollTop = 0;
  if (v === 'stats') loadStats();
  if (v === 'alerts') { loadAlerts(); loadFirewall(); }
  if (v === 'tracker' && _lastChart.length) requestAnimationFrame(() => renderChart(_lastChart));
}

const fmtNum = (n) => (n === null || n === undefined) ? '—' : Number(n).toLocaleString();

function rankList(rows, valueKey = 'messages', extra, fmt = fmtNum) {
  if (!rows || !rows.length) return '<div class="dnote">Tidak ada data.</div>';
  const max = Math.max(...rows.map(r => r[valueKey] || 0), 1);
  return `<ol class="rank">${rows.map(r => `
    <li><span class="rank-name" title="${esc(r.name)}">${esc(r.name || '—')}</span>
        <span class="rank-val">${fmt(r[valueKey])}${extra ? extra(r) : ''}</span>
        <span class="rank-bar" style="width:${(100 * (r[valueKey] || 0) / max).toFixed(1)}%"></span></li>`).join('')}</ol>`;
}

async function loadStats() {
  const sub = document.getElementById('stats-sub');
  sub.textContent = 'Memuat…';
  let d;
  try {
    const res = await fetch('/api/stats?days=' + _statsDays, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    d = await res.json();
  } catch (e) { sub.textContent = 'Gagal memuat statistik: ' + e.message; return; }
  const k = d.kpi || {};
  sub.textContent = `Periode ${d.days} hari (sejak ${d.since}) · tanpa hop internal amavis`;
  const kpi = (label, val, note, tone = '') => `<div class="kpi ${tone}"><div class="kpi-label">${label}</div><div class="kpi-val">${val}</div><div class="kpi-note">${note}</div></div>`;
  document.getElementById('stats-kpi').innerHTML =
    kpi('Pesan', fmtNum(k.messages), `${fmtNum(k.deliveries)} pengiriman ke penerima`) +
    kpi('Terkirim', fmtNum(k.ok), `${fmtNum(k.local_deliveries)} lokal · ${fmtNum(k.external_deliveries)} eksternal`, 'tone-green') +
    kpi('Bounce rate', (k.bounce_rate ?? 0) + '%', `${fmtNum(k.bounced)} bounced`, k.bounce_rate > 2 ? 'tone-red' : '') +
    kpi('Deferred', fmtNum(k.deferred), 'percobaan tertunda', k.deferred ? 'tone-amber' : '') +
    kpi('Spam', fmtNum(k.spam), 'verdict amavis', k.spam ? 'tone-purple' : '') +
    kpi('Volume', fmtBytes(k.bytes), `rata-rata ${fmtBytes(Math.round(k.avg_size || 0))} · maks ${fmtBytes(k.max_size)}`) +
    kpi('Pengirim', fmtNum(k.senders), `${fmtNum(k.recipients)} penerima unik`);

  const t = d.trend || [];
  drawBarChart(document.getElementById('stats-trend'), t.map(x => x.date.slice(5)), [
    { name: 'Delivered', color: '#00ff41', values: t.map(x => x.delivered) },
    { name: 'Deferred', color: '#ffb000', values: t.map(x => x.deferred) },
    { name: 'Bounced', color: '#ff2020', values: t.map(x => x.bounced) },
  ], { mode: 'stack', height: 170, tipLabels: t.map(x => x.date) });
  drawBarChart(document.getElementById('stats-hourly'), d.hourly.map((_, h) => String(h).padStart(2, '0')), [
    { name: 'Pesan', color: '#00d4ff', values: d.hourly },
  ], { height: 170, tipLabels: d.hourly.map((_, h) => `${String(h).padStart(2, '0')}:00–${String(h).padStart(2, '0')}:59`) });

  const fc = d.failure_categories || [];
  document.getElementById('stats-failures').innerHTML = fc.length ? `<table class="mini-table">
      <colgroup><col><col style="width:70px"><col style="width:70px"></colgroup>
      <thead><tr><th>Kategori</th><th class="num">Bounced</th><th class="num">Deferred</th></tr></thead>
      <tbody>${fc.map(c => `<tr title="${esc(c.example)}"><td class="wrap">${esc(c.category)}<div class="row-reason dmuted">${esc(c.example)}</div></td>
        <td class="num ${c.bounced ? 'txt-red' : ''}">${fmtNum(c.bounced)}</td><td class="num ${c.deferred ? 'txt-amber' : ''}">${fmtNum(c.deferred)}</td></tr>`).join('')}</tbody></table>`
    : '<div class="dnote">Tidak ada kegagalan pengiriman. 👍</div>';
  document.getElementById('stats-top-senders').innerHTML = rankList(d.top_senders);
  document.getElementById('stats-top-recipients').innerHTML = rankList(d.top_recipients);
  document.getElementById('stats-top-rdomains').innerHTML = rankList(d.top_recipient_domains, 'messages',
    r => r.bounced ? ` <span class="txt-red" title="bounced">· ${r.bounced}✕</span>` : '');
  document.getElementById('stats-top-sdomains').innerHTML = rankList(d.top_sender_domains);
  const a = d.attachments || {}, types = d.attachment_types || [];
  document.getElementById('stats-attachments').innerHTML = `
    <div class="att-kpis">
      <div><b>${fmtNum(a.messages)}</b><span>pesan berlampiran</span></div>
      <div><b>${fmtNum(a.files)}</b><span>file</span></div>
      <div><b>${fmtBytes(a.bytes)}</b><span>total ukuran</span></div>
      <div><b>${fmtBytes(a.max_bytes)}</b><span>terbesar per pesan</span></div>
    </div>
    ${types.length ? rankList(types.map(x => ({ name: '.' + x.ext + ' · ' + fmtNum(x.files) + ' file', bytes: x.bytes })), 'bytes', null, fmtBytes) : ''}`;
}

const SEV = { critical: ['KRITIS', 'sev-critical'], high: ['TINGGI', 'sev-high'], medium: ['SEDANG', 'sev-medium'], info: ['INFO', 'sev-info'] };

function updateAlertsBadge(alerts) {
  const b = document.getElementById('alerts-badge');
  if (!b) return;
  const n = alerts.filter(a => a.severity === 'critical' || a.severity === 'high').length;
  b.hidden = !n; b.textContent = n;
  b.className = 'nav-badge' + (alerts.some(a => a.severity === 'critical') ? ' crit' : '');
}

async function loadAlerts(badgeOnly = false) {
  let d;
  try {
    const res = await fetch('/api/alerts', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    d = await res.json();
  } catch (e) {
    if (!badgeOnly) document.getElementById('alerts-sub').textContent = 'Gagal memuat alerts: ' + e.message;
    return;
  }
  updateAlertsBadge(d.alerts || []);
  if (badgeOnly) return;
  const al = d.alerts || [];
  document.getElementById('alerts-sub').innerHTML =
    `${al.length ? `<b>${al.length}</b> alert aktif` : 'Tidak ada alert aktif'} · sync terakhir ${esc((d.sync || {}).last_sync || '—')} · dihitung otomatis dari log &amp; database (belum mengirim notifikasi)`;
  document.getElementById('alerts-list').innerHTML = al.length ? al.map(a => {
    const [lbl, cls] = SEV[a.severity] || ['INFO', 'sev-info'];
    return `<div class="alert-card ${cls}"><span class="sev">${lbl}</span>
      <div class="alert-body"><div class="alert-title">${esc(a.title)}</div><div class="alert-detail">${esc(a.detail)}</div></div>
      ${a.count !== null && a.count !== undefined ? `<span class="alert-count">${fmtNum(a.count)}</span>` : ''}</div>`;
  }).join('') : `<div class="alert-card sev-ok"><span class="sev">OK</span><div class="alert-body"><div class="alert-title">Semua normal</div><div class="alert-detail">Tidak ada kondisi yang melewati ambang batas.</div></div></div>`;

  const ev = d.daily_events || [];
  const days = [];
  for (let i = 13; i >= 0; i--) { const dt = new Date(Date.now() - i * 86400000); days.push(dt.toLocaleDateString('sv-SE')); }
  const byDay = Object.fromEntries(ev.map(e => [e.date, e]));
  drawBarChart(document.getElementById('alerts-events'), days.map(x => x.slice(5)), [
    { name: 'Injeksi log', color: '#ff2020', values: days.map(x => (byDay[x] || {}).injection || 0) },
    { name: 'Gagal login SASL', color: '#ffb000', values: days.map(x => (byDay[x] || {}).sasl_fail || 0) },
  ], { mode: 'stack', height: 150, tipLabels: days });

  const ips = d.top_ips || [];
  document.getElementById('alerts-ips').innerHTML = ips.length ? `<table class="mini-table">
      <colgroup><col style="width:150px"><col style="width:90px"><col style="width:90px"><col></colgroup>
      <thead><tr><th>IP</th><th class="num">Injeksi</th><th class="num">Gagal login</th><th>Terakhir</th></tr></thead>
      <tbody>${ips.map(r => `<tr><td class="mono">${esc(r.ip)}</td><td class="num ${r.injection ? 'txt-red' : ''}">${fmtNum(r.injection)}</td>
        <td class="num ${r.sasl_fail ? 'txt-amber' : ''}">${fmtNum(r.sasl_fail)}</td><td class="dmuted nowrap">${esc(r.last)}</td></tr>`).join('')}</tbody></table>
      <div class="dnote">Blokir IP (UFW/fail2ban) belum dilakukan otomatis.</div>` : '<div class="dnote">Tidak ada event dalam 7 hari.</div>';

  const inj = d.recent_injections || [];
  document.getElementById('alerts-injections').innerHTML = inj.length ? `<table class="mini-table">
      <colgroup><col style="width:140px"><col style="width:130px"><col></colgroup>
      <thead><tr><th>Waktu</th><th>IP</th><th>Payload (teks, tidak dieksekusi)</th></tr></thead>
      <tbody>${inj.map(r => `<tr><td class="dmuted nowrap">${esc(r.ts)}</td><td class="mono">${esc(r.ip || '—')}</td>
        <td class="wrap"><code class="payload">${esc(r.detail)}</code></td></tr>`).join('')}</tbody></table>` : '<div class="dnote">Tidak ada.</div>';

  const fl = d.recent_failures || [];
  document.getElementById('alerts-failures').innerHTML = fl.length ? `<table class="mini-table">
      <colgroup><col style="width:140px"><col><col style="width:100px"><col style="width:190px"></colgroup>
      <thead><tr><th>Waktu</th><th>Pengirim → Penerima</th><th>Status</th><th>Kategori</th></tr></thead>
      <tbody>${fl.map(r => `<tr title="${esc(r.reason)}"><td class="dmuted nowrap">${esc(r.timestamp)}</td>
        <td class="wrap">${esc(r.sender || '<>')} → ${esc(r.recipient)}<div class="row-reason">${esc(r.reason)}</div></td>
        <td>${statusBadge(r.status)}</td><td class="wrap">${esc(r.category)}</td></tr>`).join('')}</tbody></table>` : '<div class="dnote">Tidak ada.</div>';
}

function initViews() {
  document.querySelectorAll('.nav-btn[data-view]').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
  document.querySelectorAll('#view-stats .seg-btn').forEach(b => b.addEventListener('click', () => {
    _statsDays = parseInt(b.dataset.days);
    document.querySelectorAll('#view-stats .seg-btn').forEach(x => x.classList.toggle('active', x === b));
    loadStats();
  }));
  document.getElementById('alerts-refresh-btn').addEventListener('click', () => { loadAlerts(); loadFirewall(); });
  initFirewall();
  window.addEventListener('hashchange', () => showView(location.hash.slice(1) || 'tracker'));
  let rt;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => {
    const v = document.getElementById('main-content').dataset.view;
    if (v === 'stats') loadStats(); else if (v === 'alerts') loadAlerts(); else renderChart(_lastChart);
  }, 250); });
  showView(location.hash.slice(1) || 'tracker');
  loadAlerts(true);
  setInterval(() => loadAlerts(document.getElementById('main-content').dataset.view !== 'alerts'), 120000);
}

// ── Init ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  buildColChooser();

  const doctorRefresh = document.getElementById('doctor-refresh-btn');
  if (doctorRefresh) doctorRefresh.addEventListener('click', loadDoctorReport);
  loadDoctorReport();

  const limitsRefresh = document.getElementById('limits-refresh-btn');
  if (limitsRefresh) limitsRefresh.addEventListener('click', () => loadLimits(true));
  loadLimits(false);
  initLimitsForm();
  initViews();

  // Search
  let searchTimer;
  document.getElementById('search-input').addEventListener('input', e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = e.target.value.trim();
      state.page = 1;
      load();
    }, 350);
  });

  // Filters
  document.getElementById('status-filter').addEventListener('change', e => {
    state.statusFilter = e.target.value;
    state.page = 1;
    load();
  });
  document.getElementById('class-filter').addEventListener('change', e => {
    state.mainClassFilter = e.target.value;
    state.page = 1;
    load();
  });

  // Show Results button
  document.getElementById('show-results-btn').addEventListener('click', () => {
    state.page = 1;
    load();
  });

  // Per page
  document.getElementById('per-page-input').addEventListener('change', e => {
    state.perPage = Math.max(10, Math.min(500, parseInt(e.target.value) || 50));
    e.target.value = state.perPage;
    state.page = 1;
    load();
  });

  document.getElementById('per-page-reset').addEventListener('click', () => {
    state.perPage = 50;
    document.getElementById('per-page-input').value = 50;
    state.page = 1;
    load();
  });

  // Upload toggle
  document.getElementById('upload-btn').addEventListener('click', () => {
    document.getElementById('upload-zone').classList.toggle('show');
  });

  document.getElementById('file-input').addEventListener('change', e => {
    uploadFile(e.target.files[0]);
    e.target.value = '';
  });

  // Sync now
  document.getElementById('sync-now-btn').addEventListener('click', e => syncNow(e.currentTarget));

  // Reset filter
  document.getElementById('reset-filter-btn').addEventListener('click', resetFilters);

  // Export
  document.getElementById('export-btn').addEventListener('click', exportCSV);

  // Modal close
  document.getElementById('modal-close').addEventListener('click', () =>
    document.getElementById('detail-modal').classList.remove('show'));
  document.getElementById('detail-modal').addEventListener('click', e => {
    if (e.target === e.currentTarget) e.currentTarget.classList.remove('show');
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') document.getElementById('detail-modal').classList.remove('show');
  });

  // Window resize → re-render chart
  window.addEventListener('resize', () => {
    // debounce
    clearTimeout(window._resizeTimer);
    window._resizeTimer = setTimeout(() => load(), 300);
  });

  // Initial load
  initRadar();
  initVitals();
  load();
});

// ── Radar & Vitals Widgets (ZIMMON Ops Style) ─────────────────────────────
let radarAngle = 0;
const radarBlips = [
  { r: 0.35, a: 0.8, status: 'active' },
  { r: 0.65, a: 1.5, status: 'active' },
  { r: 0.45, a: 2.3, status: 'active' },
  { r: 0.80, a: 3.1, status: 'active' },
  { r: 0.55, a: 4.0, status: 'active' },
  { r: 0.25, a: 4.7, status: 'active' },
  { r: 0.70, a: 5.4, status: 'active' },
  { r: 0.85, a: 5.9, status: 'active' },
  { r: 0.50, a: 0.2, status: 'down' }
];

function initRadar() {
  const canvas = document.getElementById('radar-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const cx = 80, cy = 80, radius = 70;

  function draw() {
    ctx.clearRect(0, 0, 160, 160);

    // Circles
    ctx.strokeStyle = 'rgba(0, 255, 65, 0.25)';
    ctx.lineWidth = 1;
    [0.33, 0.66, 1.0].forEach(p => {
      ctx.beginPath();
      ctx.arc(cx, cy, radius * p, 0, Math.PI * 2);
      ctx.stroke();
    });

    // Crosshairs
    ctx.beginPath();
    ctx.moveTo(cx - radius, cy); ctx.lineTo(cx + radius, cy);
    ctx.moveTo(cx, cy - radius); ctx.lineTo(cx, cy + radius);
    ctx.stroke();

    // Sweep cone
    radarAngle = (radarAngle + 0.04) % (Math.PI * 2);
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
    grad.addColorStop(0, 'rgba(0, 255, 65, 0.05)');
    grad.addColorStop(1, 'rgba(0, 255, 65, 0.35)');

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, radius, radarAngle - 0.45, radarAngle);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.restore();

    // Blips
    radarBlips.forEach(b => {
      const bx = cx + Math.cos(b.a) * (radius * b.r);
      const by = cy + Math.sin(b.a) * (radius * b.r);
      ctx.beginPath();
      ctx.arc(bx, by, 3, 0, Math.PI * 2);
      ctx.fillStyle = b.status === 'down' ? '#ff2020' : '#00ff41';
      ctx.shadowColor = ctx.fillStyle;
      ctx.shadowBlur = 6;
      ctx.fill();
      ctx.shadowBlur = 0;
    });

    requestAnimationFrame(draw);
  }
  draw();
}

const vitalsHistory = [20, 22, 28, 18, 35, 24, 26, 32, 20, 19, 23, 27, 30, 21, 24];
function initVitals() {
  async function update() {
    try {
      const res = await fetch('/api/vitals');
      if (res.ok) {
        const d = await res.json();
        document.getElementById('txt-cpu').textContent = `${d.cpu}%`;
        document.getElementById('bar-cpu').style.width = `${Math.min(100, d.cpu)}%`;
        document.getElementById('vitals-cpu-val').textContent = `CPU ${d.cpu}% · LOAD`;
        document.getElementById('txt-ram').textContent = `${d.ram_used}/${d.ram_total}`;
        document.getElementById('bar-ram').style.width = `${d.ram_pct}%`;
        document.getElementById('txt-disk').textContent = d.disk_free;
        document.getElementById('bar-disk').style.width = `${d.disk_pct}%`;
        document.getElementById('txt-swap').textContent = `${d.swap_used}/${d.swap_total}`;
        document.getElementById('bar-swap').style.width = `${d.swap_pct}%`;
        vitalsHistory.push(d.cpu);
        if (vitalsHistory.length > 25) vitalsHistory.shift();
      }
    } catch (_) {}
    drawSparkline();
  }

  function drawSparkline() {
    const c = document.getElementById('vitals-sparkline');
    if (!c) return;
    const ctx = c.getContext('2d');
    c.width = c.offsetWidth;
    c.height = 50;
    const w = c.width, h = c.height;
    ctx.clearRect(0, 0, w, h);

    ctx.strokeStyle = '#00ff41';
    ctx.lineWidth = 1.5;
    ctx.shadowColor = '#00ff41';
    ctx.shadowBlur = 4;
    ctx.beginPath();
    const step = w / (vitalsHistory.length - 1);
    vitalsHistory.forEach((val, i) => {
      const y = h - (val / 100) * (h - 8) - 4;
      if (i === 0) ctx.moveTo(0, y);
      else ctx.lineTo(i * step, y);
    });
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  update();
  setInterval(update, 5000);
}
