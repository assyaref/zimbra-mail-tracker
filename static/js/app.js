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

// ── Chart (canvas bar chart) ───────────────────────────────
let chartInstance = null;
function renderChart(chartData) {
  const canvas = document.getElementById('chart-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  // Set canvas dimensions
  canvas.width  = canvas.offsetWidth;
  canvas.height = 90;

  const labels    = chartData.map(d => d.date.slice(5));
  const delivered = chartData.map(d => d.delivered);
  const bounced   = chartData.map(d => d.bounced);
  const spam      = chartData.map(d => d.spam);

  const maxVal = Math.max(...delivered, ...bounced, ...spam, 1);
  const n = labels.length;
  const W = canvas.width;
  const H = canvas.height;
  const padL = 10, padR = 10, padT = 10, padB = 20;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const barGroupW = plotW / n;
  const barW = Math.floor(barGroupW * 0.25);

  ctx.clearRect(0, 0, W, H);

  // Grid lines
  ctx.strokeStyle = 'rgba(0,61,16,0.5)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH / 4) * i;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
  }

  // Bars
  chartData.forEach((d, i) => {
    const x = padL + i * barGroupW + barGroupW * 0.1;

    const drawBar = (val, color, offset) => {
      if (!val) return;
      const h = Math.max(2, (val / maxVal) * plotH);
      const y = padT + plotH - h;
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 6;
      ctx.fillRect(x + offset, y, barW, h);
      ctx.shadowBlur = 0;
    };

    drawBar(d.delivered, '#00ff41', 0);
    drawBar(d.bounced,   '#ff2020', barW + 2);
    drawBar(d.spam,      '#cc44ff', (barW + 2) * 2);

    // Label
    ctx.fillStyle = '#2a5c38';
    ctx.font = '9px Share Tech Mono, monospace';
    ctx.textAlign = 'center';
    ctx.fillText(labels[i], x + barGroupW * 0.4, H - 4);
  });
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
    showToast(`✔ Loaded ${data.count.toLocaleString()} records from ${data.filename}`);
    state.page = 1;
    await load();
  } catch (err) {
    showToast('Upload failed: ' + err.message, 'error');
    setLoading(false);
  }
}

// ── Clear data ─────────────────────────────────────────────
async function clearData() {
  await fetch('/api/clear', { method: 'POST' });
  state.page = 1;
  showToast('Data cleared — showing demo data', 'warning');
  load();
}

// ── Export CSV ─────────────────────────────────────────────
function exportCSV() {
  window.location = '/api/export/csv';
  showToast('Exporting CSV…');
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
        ${dField('Ukuran pesan', sizeTxt)}
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

// ── Init ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  buildColChooser();

  const doctorRefresh = document.getElementById('doctor-refresh-btn');
  if (doctorRefresh) doctorRefresh.addEventListener('click', loadDoctorReport);
  loadDoctorReport();

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

  // Load dir
  document.getElementById('load-dir-btn').addEventListener('click', async () => {
    const path = prompt('Enter log directory path:\n(e.g. /var/log)', '/var/log');
    if (!path) return;
    setLoading(true, 'LOADING LOG DIRECTORY...');
    try {
      const res = await fetch('/api/load-dir', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ path }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      showToast(`Loaded ${data.count.toLocaleString()} records from ${data.path}`);
      state.page = 1;
      await load();
    } catch (err) {
      showToast(err.message, 'error');
      setLoading(false);
    }
  });

  // Clear
  document.getElementById('clear-btn').addEventListener('click', clearData);

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
