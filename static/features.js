// Export presets, AI-mode badge and batch queue UI.
const featureState = { presets: [], queueTimer: null, mode: null };

async function loadPresets() {
  try {
    const d = await (await fetch('/api/presets')).json();
    featureState.presets = d.presets || [];
  } catch (e) { return; }
  const sel = document.getElementById('exportPreset');
  if (!sel) return;
  for (const p of featureState.presets) {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = p.label;
    sel.appendChild(o);
  }
  const saved = localStorage.getItem('clipmaster_preset');
  if (saved && featureState.presets.some(p => p.id === saved)) sel.value = saved;
  onExportPresetChange();
}

function currentPreset() {
  const id = document.getElementById('exportPreset')?.value || '';
  return featureState.presets.find(p => p.id === id) || null;
}

function onExportPresetChange() {
  const p = currentPreset();
  localStorage.setItem('clipmaster_preset', p ? p.id : '');
  const hint = document.getElementById('exportHint');
  if (hint) hint.textContent = p ? t('export.hint', { max: p.max_duration, bottom: Math.round(p.safe_zone.bottom * 100) }) : '';
  // Only offer the caption styles that work on this platform
  document.querySelectorAll('.swatch-btn').forEach(b => {
    b.style.display = !p || p.caption_styles.includes(b.dataset.style) ? '' : 'none';
  });
  if (p && typeof setCaptionStyle === 'function' && !p.caption_styles.includes(state.captionStyle)) {
    setCaptionStyle(p.default_caption_style, false);
  }
}

function selectedExportPreset() { return document.getElementById('exportPreset')?.value || null; }
function selectedTranslateTo() { return document.getElementById('translateTo')?.value || null; }

async function refreshMode() {
  const badge = document.getElementById('llmBadge');
  if (!badge) return;
  try { featureState.mode = await (await fetch('/api/mode')).json(); } catch (e) { return; }
  renderMode();
}

function renderMode() {
  const badge = document.getElementById('llmBadge');
  const m = featureState.mode;
  if (!badge || !m) return;
  let label = t('mode.' + m.mode);
  if (m.mode === 'local' && !m.ready) label = t('mode.localNotReady');
  const dev = m.device || {};
  const devLabel = dev.device === 'cuda' ? t('mode.gpu') : t('mode.cpu');
  badge.textContent = label + (m.model && m.mode !== 'off' ? ' · ' + m.model : '') + ' · ' + devLabel;
  badge.className = 'llm-badge ' + (m.ready ? 'ok' : (m.mode === 'off' ? 'off' : 'warn'));
  badge.title = t('mode.tip') + (m.hint ? '\n' + m.hint : '') + (dev.reason ? '\n' + dev.reason : '');
  const cloud = document.getElementById('cloudNotice');
  if (cloud) {
    const on = !!m.sends_text_to_cloud;
    cloud.hidden = !on;
    cloud.classList.toggle('hidden', !on);
    cloud.textContent = on ? t('mode.cloudNoticeShort') : '';
    cloud.title = on ? t('mode.cloudNotice') : '';
  }
  const warn = document.getElementById('diskWarn');
  if (warn) {
    const disk = m.disk || {};
    if (disk.ok === false) {
      warn.hidden = false;
      warn.classList.remove('hidden');
      warn.textContent = t('cache.lowDiskShort');
      warn.title = t('cache.lowDisk', { free: disk.free_mb, min: disk.min_free_mb });
    } else {
      warn.hidden = true;
      warn.classList.add('hidden');
      warn.textContent = '';
    }
  }
}

async function clearCacheAndExports() {
  if (!confirm(t('cache.confirm'))) return;
  const res = await fetch('/api/cache/clean?all=true', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(t('msg.refused', { label: t('cache.clear'), status: res.status, detail: data.detail || '' }), 'error');
    return;
  }
  const n = (data.deleted || []).length;
  showToast(n ? t('cache.freed', { size: data.freed_human || '0 B', n }) : t('cache.nothing'), n ? 'ok' : '');
  if (typeof logTelemetry === 'function') {
    logTelemetry(t('tel.system'), `Cache clean: ${data.freed_human || '0 B'}`, '#0e9f8e');
  }
  if (typeof refreshProjectsList === 'function') {
    const list = await refreshProjectsList();
    const still = state.project && list.some(p => p.id === state.project.id);
    if (!still) {
      state.project = null;
      if (typeof loadLatestProject === 'function') await loadLatestProject();
    } else if (state.project && typeof applyProject === 'function') {
      const pRes = await fetch('/api/projects/' + encodeURIComponent(state.project.id));
      if (pRes.ok) applyProject(await pRes.json());
    }
  }
  refreshMode();
}

// ---- Batch queue -----------------------------------------------------------
function queueOptions() {
  return {
    export_preset: selectedExportPreset(),
    translate_to: selectedTranslateTo(),
    model_size: document.getElementById('whisperModel')?.value || 'tiny',
    language: document.getElementById('whisperLang')?.value || 'auto',
    tracked: typeof getTrackedOptions === 'function' ? getTrackedOptions() : null,
  };
}

async function addQueueUrls() {
  const box = document.getElementById('queueUrls');
  const urls = box.value.split(/\s*\n\s*/).map(s => s.trim()).filter(Boolean);
  if (!urls.length) return showToast(t('msg.needUrl'), 'warn');
  const res = await fetch('/api/queue', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls, options: queueOptions() }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) return showToast(typeof body.detail === 'string' ? body.detail : t('msg.needUrl'), 'warn');
  box.value = '';
  showToast(t('msg.queued', { n: body.added.length }), 'ok');
  refreshQueue();
}

async function addQueueFiles(ev) {
  const files = Array.from(ev.target.files || []);
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f);
    fd.append('options', JSON.stringify(queueOptions()));
    const res = await fetch('/api/queue/upload', { method: 'POST', body: fd });
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      showToast(typeof b.detail === 'string' ? b.detail : 'Upload', 'warn');
    }
  }
  ev.target.value = '';
  if (files.length) showToast(t('msg.queued', { n: files.length }), 'ok');
  refreshQueue();
}

async function queueAction(id, action) {
  const opts = action === 'remove' ? { method: 'DELETE' } : { method: 'POST' };
  const url = action === 'remove' ? `/api/queue/${id}` : `/api/queue/${id}/${action}`;
  const res = await fetch(url, opts);
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    showToast(typeof b.detail === 'string' ? b.detail : 'Error', 'warn');
  }
  refreshQueue();
}

async function clearQueueFinished() {
  await fetch('/api/queue-clear', { method: 'POST' });
  refreshQueue();
}

function renderQueue(items) {
  const host = document.getElementById('queueList');
  if (!host) return;
  host.textContent = '';
  if (!items.length) {
    const d = document.createElement('div');
    d.className = 'muted';
    d.textContent = t('queue.empty');
    host.appendChild(d);
    return;
  }
  for (const it of items) {
    const row = document.createElement('div');
    row.className = `queue-item st-${it.status}`;
    const head = document.createElement('div');
    head.className = 'queue-item-head';
    const name = document.createElement('span');
    name.className = 'queue-name';
    name.textContent = it.label;
    name.title = it.source;
    const st = document.createElement('span');
    st.className = 'queue-status';
    st.textContent = t('st.' + it.status);
    head.append(name, st);
    const bar = document.createElement('div');
    bar.className = 'queue-bar';
    const fill = document.createElement('div');
    fill.style.width = `${it.status === 'done' ? 100 : it.percent || 0}%`;
    bar.appendChild(fill);
    const msg = document.createElement('div');
    msg.className = 'queue-msg muted';
    msg.textContent = it.error || it.message || '';
    const actions = document.createElement('div');
    actions.className = 'queue-actions';
    const mk = (label, fn) => {
      const b = document.createElement('button');
      b.className = 'cap-mini-btn';
      b.textContent = label;
      b.onclick = fn;
      actions.appendChild(b);
    };
    if (it.status === 'queued' || it.status === 'running') mk(t('queue.cancel'), () => queueAction(it.id, 'cancel'));
    if (it.status === 'error' || it.status === 'cancelled') mk(t('queue.retry'), () => queueAction(it.id, 'retry'));
    if (it.status !== 'running') mk(t('queue.remove'), () => queueAction(it.id, 'remove'));
    if (it.status === 'done' && it.output) {
      const a = document.createElement('a');
      a.className = 'cap-mini-btn';
      a.textContent = t('queue.open');
      a.href = `/media/outputs/${encodeURIComponent(it.output)}`;
      a.target = '_blank';
      actions.appendChild(a);
    }
    row.append(head, bar, msg, actions);
    host.appendChild(row);
  }
}

let _lastQueue = [];
async function refreshQueue() {
  try {
    const d = await (await fetch('/api/queue')).json();
    _lastQueue = d.items || [];
    renderQueue(_lastQueue);
  } catch (e) { /* server restarting */ }
}

async function initFeatures() {
  await window.i18nReady;
  loadPresets();
  refreshMode();
  refreshQueue();
  featureState.queueTimer = setInterval(refreshQueue, 3000);
  setInterval(refreshMode, 30000);
  document.addEventListener('langchange', () => {
    renderMode(); renderQueue(_lastQueue); onExportPresetChange();
  });
}
initFeatures();
