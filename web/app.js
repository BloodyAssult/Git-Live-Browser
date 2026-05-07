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

function log(msg, level='info') {
  const div = document.createElement('div');
  const t = new Date().toLocaleTimeString('fa-IR');
  div.textContent = `[${t}] ${msg}`;
  if (level === 'error') div.style.color = '#ff9bb8';
  if (level === 'ok') div.style.color = '#75ffd9';
  logEl.prepend(div);
  while (logEl.children.length > 60) logEl.lastChild.remove();
}

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws`;
}

function connect() {
  ws = new WebSocket(wsUrl());
  ws.binaryType = 'blob';
  connEl.textContent = 'در حال اتصال WebSocket…';
  ws.onopen = () => { connEl.textContent = 'زنده'; log('WebSocket connected', 'ok'); };
  ws.onclose = () => { connEl.textContent = 'قطع؛ تلاش مجدد…'; setTimeout(connect, 1200); };
  ws.onerror = () => log('WebSocket error', 'error');
  ws.onmessage = async (ev) => {
    if (typeof ev.data === 'string') {
      const data = JSON.parse(ev.data);
      handleMessage(data);
    } else {
      const url = URL.createObjectURL(ev.data);
      frame.src = url;
      emptyState.style.display = 'none';
      if (lastObjectUrl) URL.revokeObjectURL(lastObjectUrl);
      lastObjectUrl = url;
    }
  };
}

function handleMessage(data) {
  if (data.type === 'hello') {
    if (data.url && data.url !== 'about:blank') urlInput.value = data.url;
    log('Browser ready', 'ok');
  }
  if (data.type === 'status') log(data.message, data.level);
  if (data.type === 'url') urlInput.value = data.url;
  if (data.type === 'downloads') renderDownloads(data.items || []);
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
$('qualityBtn').onclick = () => {
  $('qualityRange').value = 92;
  $('delayRange').value = 100;
  $('qualityVal').textContent = 92;
  $('delayVal').textContent = '100ms';
  send('set_quality', {quality: 92, interval_ms: 100});
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
