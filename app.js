const $ = (id) => document.getElementById(id);
const frame = $('frame');
const viewer = $('viewer');
const urlInput = $('urlInput');
const logEl = $('statusLog');
const connEl = $('conn');
const emptyState = $('emptyState');
const downloadsEl = $('downloads');
let ws;
let lastObjectUrl = null;
let pendingBlob = null;
let renderScheduled = false;
let framesThisSecond = 0;
let lastFpsTick = performance.now();
let lastStats = null;
let lastFrameAt = 0;
let lastRestartRequestAt = 0;
let reconnectAttempts = 0;

function log(msg, level='info') {
  const div = document.createElement('div');
  const t = new Date().toLocaleTimeString('fa-IR');
  div.textContent = `[${t}] ${msg}`;
  if (level === 'error') div.style.color = '#ff9bb8';
  if (level === 'ok') div.style.color = '#75ffd9';
  if (level === 'warn') div.style.color = '#ffd479';
  logEl.prepend(div);
  while (logEl.children.length > 80) logEl.lastChild.remove();
}

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws`;
}

function updateConn(extra='') {
  const fpsPart = `FPS ${framesThisSecond}`;
  const mode = lastStats?.mode ? ` · ${lastStats.mode}` : '';
  connEl.textContent = `زنده · ${fpsPart}${mode}${extra}`;
}

setInterval(() => {
  const now = performance.now();
  if (now - lastFpsTick >= 1000) {
    updateConn();
    framesThisSecond = 0;
    lastFpsTick = now;
  }
}, 1000);

// Client-side stall recovery: if the tunnel/WebSocket is alive but no frame
// arrives for a few seconds, ask the server to restart CDP screencast. If that
// does not help, reconnect the WebSocket. This avoids manual page refreshes.
setInterval(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!lastFrameAt) return;
  const age = performance.now() - lastFrameAt;
  if (age > 3500 && performance.now() - lastRestartRequestAt > 5000) {
    lastRestartRequestAt = performance.now();
    log('فریم‌ها مکث کردند؛ ری‌استارت استریم…', 'warn');
    send('restart_stream');
  }
  if (age > 10000) {
    log('اتصال زنده گیر کرد؛ اتصال WebSocket تازه می‌شود…', 'warn');
    try { ws.close(); } catch {}
  }
}, 1500);

function connect() {
  ws = new WebSocket(wsUrl());
  ws.binaryType = 'blob';
  connEl.textContent = 'در حال اتصال WebSocket…';
  ws.onopen = () => { reconnectAttempts = 0; connEl.textContent = 'زنده'; log('WebSocket connected', 'ok'); send('ping'); };
  ws.onclose = () => { reconnectAttempts++; connEl.textContent = 'قطع؛ تلاش مجدد…'; setTimeout(connect, Math.min(3000, 500 + reconnectAttempts * 300)); };
  ws.onerror = () => log('WebSocket error', 'error');
  ws.onmessage = async (ev) => {
    if (typeof ev.data === 'string') {
      const data = JSON.parse(ev.data);
      handleMessage(data);
    } else {
      pendingBlob = ev.data;
      if (!renderScheduled) {
        renderScheduled = true;
        requestAnimationFrame(renderLatestFrame);
      }
    }
  };
}

function renderLatestFrame() {
  renderScheduled = false;
  if (!pendingBlob) return;
  const blob = pendingBlob;
  pendingBlob = null;
  const url = URL.createObjectURL(blob);
  const prev = lastObjectUrl;
  frame.onload = () => { if (prev) URL.revokeObjectURL(prev); };
  frame.src = url;
  lastObjectUrl = url;
  emptyState.style.display = 'none';
  framesThisSecond++;
  lastFrameAt = performance.now();
}


function handleMessage(data) {
  if (data.type === 'hello') {
    if (data.url && data.url !== 'about:blank') urlInput.value = data.url;
    if (data.mode) lastStats = {mode: data.mode};
    log(`Browser ready${data.mode ? ' · ' + data.mode : ''}`, 'ok');
  }
  if (data.type === 'status') log(data.message, data.level);
  if (data.type === 'url') urlInput.value = data.url;
  if (data.type === 'downloads') renderDownloads(data.items || []);
  if (data.type === 'stream_stats') {
    lastStats = data;
    updateConn(` · q${data.quality} · nth${data.nth} · drop${data.dropped} · r${data.restarts || 0}`);
  }
}

function send(action, payload={}) {
  const data = { action, ...payload };
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data));
  else fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)}).catch(e => log(e.message, 'error'));
}

function go() { const url = urlInput.value.trim(); if (url) send('goto', {url}); }
$('goBtn').onclick = go;
urlInput.addEventListener('keydown', e => { if(e.key === 'Enter') go(); });
$('backBtn').onclick = () => send('back');
$('fwdBtn').onclick = () => send('forward');
$('reloadBtn').onclick = () => send('reload');
$('typeBtn').onclick = () => send('type', {text: $('typeInput').value});
$('typeInput').addEventListener('keydown', e => { if(e.key === 'Enter') send('type', {text: e.target.value}); });

document.querySelectorAll('[data-key]').forEach(btn => btn.onclick = () => send('key', {key: btn.dataset.key}));
document.querySelectorAll('[data-wheel]').forEach(btn => btn.onclick = () => send('wheel', {dy: Number(btn.dataset.wheel)}));

frame.addEventListener('click', (e) => {
  const rect = frame.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;
  send('click', {x_norm: Math.max(0, Math.min(1, x)), y_norm: Math.max(0, Math.min(1, y))});
});

viewer.addEventListener('wheel', (e) => {
  e.preventDefault();
  send('wheel', {dy: e.deltaY});
}, {passive:false});

$('fullscreenBtn').onclick = async () => {
  viewer.classList.add('full');
  $('exitFull').classList.remove('hidden');
  try { await viewer.requestFullscreen?.(); } catch {}
};
$('exitFull').onclick = async () => {
  viewer.classList.remove('full');
  $('exitFull').classList.add('hidden');
  try { await document.exitFullscreen?.(); } catch {}
};

document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement) {
    viewer.classList.remove('full');
    $('exitFull').classList.add('hidden');
  }
});

$('qualityRange').oninput = e => { $('qualityVal').textContent = e.target.value; };
$('delayRange').oninput = e => { $('delayVal').textContent = `${e.target.value}ms`; };
$('applyStreamBtn').onclick = () => send('set_quality', {quality: Number($('qualityRange').value), interval_ms: Number($('delayRange').value)});
$('restartStreamBtn').onclick = () => send('restart_stream');
$('qualityBtn').onclick = () => {
  $('qualityRange').value = 92;
  $('delayRange').value = 60;
  $('qualityVal').textContent = 92;
  $('delayVal').textContent = '60ms';
  send('set_quality', {quality: 92, interval_ms: 60});
};
$('uploadDownloadsBtn').onclick = () => send('upload_downloads');

function renderDownloads(items) {
  downloadsEl.className = 'downloads';
  downloadsEl.innerHTML = '';
  if (!items.length) {
    downloadsEl.classList.add('emptyList');
    downloadsEl.textContent = 'هنوز دانلودی ثبت نشده.';
    return;
  }
  for (const item of items) {
    const div = document.createElement('div');
    div.className = 'download';
    const mb = item.size ? (item.size / (1024*1024)).toFixed(2) : '0';
    div.innerHTML = `<b title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</b><span>${mb} MB</span>`;
    if (item.url) {
      const a = document.createElement('a');
      a.href = item.url; a.target = '_blank'; a.textContent = 'لینک اصلی';
      div.appendChild(document.createTextNode(' ')); div.appendChild(a);
    }
    if (item.release_url) {
      const a = document.createElement('a');
      a.href = item.release_url; a.target = '_blank'; a.textContent = 'دانلود از Release';
      div.appendChild(document.createTextNode(' ')); div.appendChild(a);
    }
    downloadsEl.appendChild(div);
  }
}
function escapeHtml(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

connect();
