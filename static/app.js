const state = {
  repos: [],
  activeRepos: [],
  project: null,
  selectedCaptionIdx: 0,
  previewMode: 'edit',
  activePollers: new Map(),
  saveTimer: null,
  playbackSpeed: 1.0,
  isExporting: false,
  userDraggingCrop: false,
  captionStyle: 'pink',
};

const CAPTION_STYLE_MAP = {
  pink: { fg: '#ee1d6c', bg: '#ffffff' },
  yellow: { fg: '#facc15', bg: '#111111' },
  cyan: { fg: '#22d3ee', bg: '#111111' },
  white: { fg: '#ffffff', bg: '#111111' },
  tiktok_white: { fg: '#ffffff', bg: '#000000', outline: true, italic: true },
  tiktok_yellow: { fg: '#ffe600', bg: '#000000', outline: true, italic: true },
};

const video = document.getElementById('mainVideo');
const vCanvas = document.getElementById('verticalCanvas');
const vCtx = vCanvas.getContext('2d');

// ---------------------------------------------------------------------------
// BOOTSTRAP
// ---------------------------------------------------------------------------
async function init() {
  await refreshRepos();
  await loadLatestProject();
  setupVideoListeners();
  setupCropDrag();
  setupFaceDrag();
  setupTimelineScrub();
  setupKeyboardShortcuts();
  setupFileDrop();
  initAgentSwarm();
  setUiMode(localStorage.getItem('clipstudio_ui_mode') || 'simple');
  syncQuickPresetUI();
  {
    const ready = state.repos.filter(r => r.ready).length;
    const miss = state.repos.filter(r => r.status === 'executed_missing').length;
    logTelemetry(
      t('tel.system'),
      `CLIP MASTER — ${t('desk.summary', { ready, total: state.repos.length })}` + (miss ? ` (${miss})` : ''),
      miss ? '#f59e0b' : '#0e9f8e',
    );
  }
  document.addEventListener('langchange', () => { if (state.repos?.length) renderReposDesk(); });
  requestAnimationFrame(renderVerticalLoop);
}

async function refreshRepos() {
  const res = await fetch('/api/repos');
  const data = await res.json();
  state.repos = data.repos || [];
  renderReposDesk();
}

async function refreshProjectsList(selectedId = null) {
  const res = await fetch('/api/projects');
  const data = await res.json();
  const list = data.projects || [];
  const sel = document.getElementById('projectSwitcher');
  if (sel) {
    sel.textContent = '';
    if (!list.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = t('project.new');
      sel.appendChild(opt);
    } else {
      for (const p of list) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `${p.name} (${p.duration}s)`;
        if (p.id === selectedId) opt.selected = true;
        sel.appendChild(opt);
      }
    }
  }
  return list;
}

async function loadLatestProject() {
  const list = await refreshProjectsList();
  if (list.length > 0) {
    const pRes = await fetch(`/api/projects/${list[0].id}`);
    if (pRes.ok) {
      applyProject(await pRes.json());
    }
  }
}

async function switchProject(pid) {
  if (!pid) return;
  const pRes = await fetch(`/api/projects/${pid}`);
  if (pRes.ok) {
    const proj = await pRes.json();
    applyProject(proj);
    logTelemetry('PROJECT', `Switched to ${proj.name} (${proj.info?.duration || 0}s)`, '#6366f1');
  }
}

// ---------------------------------------------------------------------------
// FEATURES DESK
// ---------------------------------------------------------------------------
function renderReposDesk() {
  const grid = document.getElementById('reposGrid');
  const readyCount = state.repos.filter(r => r.ready).length;
  document.getElementById('reposReadySummary').textContent =
    t('desk.summary', { ready: readyCount, total: state.repos.length });

  grid.innerHTML = state.repos
    .map(r => {
      const isActive = state.activeRepos.includes(r.id);
      const cls = ['repo-cell', r.ready ? 'ready' : '', isActive ? 'active' : ''].join(' ');
      let badge = '';
      if (!isActive) {
        if (r.ready) {
          badge = `<span class="repo-check" title="${t('desk.ready')}">&#10003;</span>`;
        }
      }
      return `
        <div class="${cls}" onclick="openRepoModal('${r.id}')">
          <span><span class="repo-num">${r.id}</span>${t('tool.' + r.id)}</span>
          ${badge}
        </div>
      `;
    })
    .join('');
}

function openRepoModal(id) {
  const r = state.repos.find(x => x.id === id);
  if (!r) return;
  const fname = t('tool.' + r.id);
  document.getElementById('modalRepoTitle').textContent = `${r.id} ${fname} — ${r.stage}`;
  const runBtn =
    r.stage_key && r.stage_key !== 'pull'
      ? `<button class="btn-pink" style="margin-top:8px;align-self:flex-start" onclick="closeRepoModal(); runStage('${r.stage_key}')">${t('desk.run', { stage: r.stage })}</button>`
      : '';
  document.getElementById('modalRepoBody').innerHTML = `
    <div><strong>${t('desk.role')}:</strong> ${r.role}</div>
    <div><strong>${t('desk.status')}:</strong> ${r.ready ? t('desk.ready') : t('desk.missing')}</div>
    ${runBtn}
  `;
  document.getElementById('repoModal').classList.remove('hidden');
}

function closeRepoModal() {
  document.getElementById('repoModal').classList.add('hidden');
}

// ---------------------------------------------------------------------------
// PROJECT APPLICATION & UI RENDERING
// ---------------------------------------------------------------------------
function applyProject(proj) {
  state.project = proj;
  refreshProjectsList(proj.id);
  document.getElementById('programSourceName').textContent = proj.name || 'SOURCE_01';
  document.getElementById('emptyMonitor').classList.add('hidden');

  const srcUrl = `/media/projects/${proj.id}/${proj.video_file}`;
  if (!video.src.endsWith(srcUrl)) {
    video.src = srcUrl;
    video.load();
    video.onloadedmetadata = () => {
      // Skip black intro/ESRB frame automatically so monitor shows real action immediately
      const hlStart = proj.highlight?.start || 0;
      const targetT = hlStart > 1.5 ? hlStart : Math.min(5.2, (video.duration || 10) * 0.08);
      if (video.currentTime < 0.2 && targetT > 0) {
        video.currentTime = targetT;
      }
      updateStreamMetaTag();
      updateTimeDisplay();
    };
  }
  updateStreamMetaTag();

  // Font size & layout
  const fs = proj.font_size || 40;
  document.getElementById('fontSizeSlider').value = fs;
  document.getElementById('fontSizeVal').textContent = `${fs} PX`;
  document.getElementById('btnLayoutToggle').textContent = proj.layout_mode || 'FACECAM + GAME + CAPS';
  document.getElementById('outputLayoutLabel').textContent = proj.layout_mode || 'FACECAM + GAME + CAPS';
  setCaptionStyle(proj.caption_style || state.captionStyle || 'pink', false);
  applyTrackedToUI(proj);
  syncQuickPresetUI();

  // Overlays
  updateCropBoxUI();
  updateFaceBoxUI();
  renderMoments();
  renderFilmstrip();
  renderCaptionsTrack();
  renderFaceCurves();
  renderWaveform();
  renderPublishPanel();
  updateTimeDisplay();

  // Export link if exists
  if (proj.exports && proj.exports.length > 0) {
    const latest = proj.exports[0];
    const wrap = document.getElementById('exportDownloadWrap');
    const link = document.getElementById('exportDownloadLink');
    link.href = `/media/outputs/${latest.filename}`;
    link.textContent = `DOWNLOAD ${latest.filename} (${latest.size_mb} MB) ↓`;
    wrap.classList.remove('hidden');
    setSegMeter(10);
  }
}

function updateCropBoxUI() {
  if (!state.project) return;
  const cx = state.project.crop_x ?? 0.5;
  const cropW = 31.6; // 9:16 width inside 16:9
  const leftPct = Math.max(0, Math.min(100 - cropW, cx * 100 - cropW / 2));
  const box = document.getElementById('cropBox');
  box.style.left = `${leftPct}%`;
  box.style.width = `${cropW}%`;
  const pctInt = Math.round(cx * 100);
  document.getElementById('cropBadge').textContent = `9:16 / CROP ${pctInt}`;
  document.getElementById('faceTrackX').textContent = `${(cx * 100).toFixed(1)}%`;
}

function updateFaceBoxUI() {
  if (!state.project || !state.project.face_box) return;
  const fb = state.project.face_box;
  const el = document.getElementById('faceBox');
  el.style.left = `${fb.x * 100}%`;
  el.style.top = `${fb.y * 100}%`;
  el.style.width = `${fb.w * 100}%`;
  el.style.height = `${fb.h * 100}%`;
  const confTxt = fb.conf != null ? Number(fb.conf).toFixed(2) : (fb.detected ? 'DETECTED' : 'MANUAL');
  document.getElementById('faceBadge').textContent = `FACE ${confTxt}`;
}

function renderMoments() {
  const grid = document.getElementById('momentsGrid');
  const cuts = state.project?.cuts || [];
  document.getElementById('cutsCountLabel').textContent = `${cuts.length} CUTS`;
  const moments = state.project?.moments || [];
  grid.textContent = '';
  if (!moments.length) {
    const ph = document.createElement('div');
    ph.className = 'moment-placeholder';
    ph.textContent = 'Run 02 / FIND to detect viral moments';
    grid.appendChild(ph);
    return;
  }
  moments.forEach((m, i) => {
    const card = document.createElement('div');
    card.className = `moment-card ${i === 0 ? 'active' : ''}`;
    card.addEventListener('click', () => selectMoment(i));
    const wrap = document.createElement('div');
    wrap.className = 'moment-thumb-wrap';
    const img = document.createElement('img');
    img.src = `/media/projects/${encodeURIComponent(state.project.id)}/${m.thumb}`;
    img.alt = String(m.label || '');
    wrap.appendChild(img);
    const lbl = document.createElement('div');
    lbl.className = 'moment-label';
    lbl.textContent = `${m.label} · ${m.score}`;
    card.appendChild(wrap);
    card.appendChild(lbl);
    grid.appendChild(card);
  });
}

function selectMoment(idx) {
  const m = state.project?.moments?.[idx];
  if (!m) return;
  document.querySelectorAll('.moment-card').forEach((el, i) => el.classList.toggle('active', i === idx));
  video.currentTime = m.start;
  state.project.highlight = { start: m.start, end: m.end, score: m.score };
  updateHighlightUI();
  scheduleSave();
}

function renderFilmstrip() {
  const track = document.getElementById('filmstripTrack');
  const strip = state.project?.filmstrip || [];
  const dur = state.project?.info?.duration || 1;
  const cuts = state.project?.cuts || [];
  const imgs = strip
    .map(f => `<img src="/media/projects/${state.project.id}/filmstrip/${f}" alt="" draggable="false" />`)
    .join('');
  const cutTicks = cuts
    .slice(0, 60)
    .map(c => `<div class="cut-tick" style="left:${Math.min(99.5, (c.start / dur) * 100)}%"></div>`)
    .join('');
  track.innerHTML = `
    ${imgs}
    ${cutTicks}
    <div class="highlight-box" id="highlightBox" title="Glisse la zone ou ses bords pour régler le clip">
      <div class="hl-handle left" data-edge="left"></div>
      <div class="hl-handle right" data-edge="right"></div>
      <span class="highlight-badge" id="highlightBadge">HIGHLIGHT --</span>
    </div>
  `;
  setupHighlightDrag();
  updateHighlightUI();
}

function setupHighlightDrag() {
  const box = document.getElementById('highlightBox');
  const track = document.getElementById('filmstripTrack');
  if (!box || !track) return;
  let dragMode = null; // 'move' | 'left' | 'right'
  let startX = 0;
  let origStart = 0;
  let origEnd = 0;

  box.addEventListener('mousedown', e => {
    if (!state.project) return;
    e.stopPropagation();
    e.preventDefault();
    dragMode = e.target.dataset.edge || 'move';
    startX = e.clientX;
    origStart = state.project.highlight?.start || 0;
    origEnd = state.project.highlight?.end || state.project.info?.duration || 15;
  });

  window.addEventListener('mousemove', e => {
    if (!dragMode || !state.project) return;
    const dur = state.project.info?.duration || 1;
    const rect = track.getBoundingClientRect();
    const dt = ((e.clientX - startX) / rect.width) * dur;
    let s = origStart;
    let en = origEnd;
    if (dragMode === 'move') {
      const len = en - s;
      s = Math.max(0, Math.min(dur - len, origStart + dt));
      en = s + len;
    } else if (dragMode === 'left') {
      s = Math.max(0, Math.min(en - 1.5, origStart + dt));
    } else if (dragMode === 'right') {
      en = Math.min(dur, Math.max(s + 1.5, origEnd + dt));
    }
    state.project.highlight = {
      ...state.project.highlight,
      start: Number(s.toFixed(2)),
      end: Number(en.toFixed(2)),
    };
    updateHighlightUI();
  });

  window.addEventListener('mouseup', () => {
    if (dragMode) {
      dragMode = null;
      scheduleSave();
    }
  });
}

function updateHighlightUI() {
  if (!state.project) return;
  const dur = state.project.info?.duration || 1;
  const hl = state.project.highlight || { start: 0, end: dur, score: null };
  const box = document.getElementById('highlightBox');
  const badge = document.getElementById('highlightBadge');
  if (!box) return;
  const left = Math.max(0, (hl.start / dur) * 100);
  const width = Math.max(6, ((hl.end - hl.start) / dur) * 100);
  box.style.left = `${left}%`;
  box.style.width = `${Math.min(100 - left, width)}%`;
  const scoreTxt = hl.score != null ? Number(hl.score).toFixed(2) : '--';
  badge.textContent = `HIGHLIGHT ${scoreTxt} (${hl.start.toFixed(1)}s–${hl.end.toFixed(1)}s)`;
  document.getElementById('seqDurationLabel').textContent = `${hl.start.toFixed(1)}s — ${hl.end.toFixed(1)}s / ${Math.round(dur)}s`;
  const simpleTrim = document.getElementById('simpleTrimLabel');
  if (simpleTrim) {
    const clipLen = Math.max(0, hl.end - hl.start);
    simpleTrim.textContent = `Extrait : ${hl.start.toFixed(1)}s → ${hl.end.toFixed(1)}s (${clipLen.toFixed(1)}s)`;
  }
}

function renderCaptionsTrack() {
  const track = document.getElementById('captionsTrack');
  const caps = state.project?.captions || [];
  const dur = state.project?.info?.duration || 1;
  track.textContent = '';
  if (!caps.length) {
    const def = document.createElement('div');
    def.className = 'cap-block';
    def.style.left = '1%';
    def.style.width = '22%';
    def.textContent = 'NEW CAPTION';
    track.appendChild(def);
    return;
  }
  const allowedColors = new Set(['slate', 'indigo', 'teal', 'orange', 'pink']);
  caps.forEach((c, idx) => {
    const left = Math.max(0, (c.start / dur) * 100);
    const width = Math.max(4, ((c.end - c.start) / dur) * 100);
    const blk = document.createElement('div');
    const col = allowedColors.has(c.color) ? c.color : '';
    blk.className = `cap-block ${col} ${idx === state.selectedCaptionIdx ? 'active' : ''}`.trim();
    blk.style.left = `${left}%`;
    blk.style.width = `${Math.min(100 - left, width)}%`;
    blk.title = `${c.text} (${c.start}s - ${c.end}s)`;
    blk.textContent = String(c.text || '');
    blk.addEventListener('click', ev => selectCaption(idx, ev));
    track.appendChild(blk);
  });

  syncInspectorCaption();
}

function selectCaption(idx, ev) {
  if (ev) ev.stopPropagation();
  const c = state.project?.captions?.[idx];
  if (!c) return;
  state.selectedCaptionIdx = idx;
  video.currentTime = c.start + 0.02;
  renderCaptionsTrack();
}

function syncInspectorCaption() {
  const caps = state.project?.captions || [];
  const c = caps[state.selectedCaptionIdx];
  const txt = c ? c.text : 'NEW CAPTION';
  document.getElementById('captionInput').value = txt;
  document.getElementById('liveCaptionStrip').textContent = txt;
  document.getElementById('wordsTiming').textContent = c ? `${(c.end - c.start).toFixed(2)}s` : '0.00s';
}

function onCaptionEdit(val) {
  const upper = val.toUpperCase();
  document.getElementById('liveCaptionStrip').textContent = upper || ' ';
  if (state.project?.captions?.[state.selectedCaptionIdx]) {
    state.project.captions[state.selectedCaptionIdx].text = upper;
    renderCaptionsTrack();
    scheduleSave();
  }
}

function addCaptionAtPlayhead() {
  if (!state.project) return;
  const t = Number((video.currentTime || 0).toFixed(2));
  const dur = state.project.info?.duration || 20;
  const colors = ['slate', 'indigo', 'teal', 'orange', 'pink'];
  const caps = state.project.captions || [];
  const newCap = {
    id: `cap_${Date.now()}`,
    text: 'NEW CAPTION',
    start: t,
    end: Number(Math.min(dur, t + 2.0).toFixed(2)),
    color: colors[caps.length % colors.length],
  };
  caps.push(newCap);
  caps.sort((a, b) => a.start - b.start);
  state.project.captions = caps;
  state.selectedCaptionIdx = caps.findIndex(c => c.id === newCap.id);
  renderCaptionsTrack();
  scheduleSave();
  logTelemetry(t('tel.captions'), `Added caption at ${t}s`, '#f59e0b');
}

function shiftSelectedCaption(delta) {
  const c = state.project?.captions?.[state.selectedCaptionIdx];
  if (!c) return;
  const dur = state.project.info?.duration || 60;
  c.start = Number(Math.max(0, Math.min(dur - 0.3, c.start + delta)).toFixed(2));
  c.end = Number(Math.max(c.start + 0.3, Math.min(dur, c.end + delta)).toFixed(2));
  renderCaptionsTrack();
  scheduleSave();
  logTelemetry(t('tel.animated'), `Shifted caption to ${c.start}s–${c.end}s`, '#6366f1');
}

function deleteSelectedCaption() {
  const caps = state.project?.captions;
  if (!caps || !caps.length) return;
  caps.splice(state.selectedCaptionIdx, 1);
  state.selectedCaptionIdx = Math.max(0, state.selectedCaptionIdx - 1);
  renderCaptionsTrack();
  scheduleSave();
  logTelemetry(t('tel.captions'), 'Deleted selected caption block', '#ee1d6c');
}

function downloadSrt() {
  if (!state.project) return;
  window.open(`/api/projects/${state.project.id}/srt`, '_blank');
  logTelemetry(t('tel.captions'), `Exported ${state.project.id}_captions.srt`, '#0e9f8e');
}

function downloadRemotionJson() {
  window.open('/api/remotion-json', '_blank');
  logTelemetry(t('tel.animated'), 'Exported captions JSON', '#6366f1');
}

function setTrimPoint(edge) {
  if (!state.project) return;
  const t = Number((video.currentTime || 0).toFixed(2));
  if (edge === 'in') {
    state.project.highlight.start = Math.min(t, (state.project.highlight.end || 99) - 1);
    logTelemetry('TRIM', `Highlight IN = ${state.project.highlight.start}s`, '#ee1d6c');
    showToast(t('msg.inSet', { v: state.project.highlight.start }));
  } else {
    state.project.highlight.end = Math.max(t, (state.project.highlight.start || 0) + 1);
    logTelemetry('TRIM', `Highlight OUT = ${state.project.highlight.end}s`, '#ee1d6c');
    showToast(t('msg.outSet', { v: state.project.highlight.end }));
  }
  updateHighlightUI();
  scheduleSave();
}

function logTelemetry(agent, msg, color = '#ee1d6c') {
  const box = document.getElementById('telemetryLog');
  if (!box) return;
  const now = new Date();
  const ts = now.toTimeString().slice(0, 8);
  const row = document.createElement('div');
  row.className = 'tel-row';
  const sTime = document.createElement('span');
  sTime.className = 'tel-time';
  sTime.textContent = ts;
  const sAg = document.createElement('span');
  sAg.className = 'tel-agent';
  sAg.style.color = color;
  sAg.textContent = `[${agent}]`;
  const sMsg = document.createElement('span');
  sMsg.className = 'tel-msg';
  sMsg.textContent = String(msg || '');
  row.append(sTime, sAg, sMsg);
  box.prepend(row);
  while (box.children.length > 35) box.removeChild(box.lastChild);
}

function setupKeyboardShortcuts() {
  window.addEventListener('keydown', e => {
    const tag = (e.target?.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.code === 'Space') {
      e.preventDefault();
      togglePlay();
    } else if (e.code === 'ArrowLeft') {
      e.preventDefault();
      seekRelative(-1);
    } else if (e.code === 'ArrowRight') {
      e.preventDefault();
      seekRelative(1);
    } else if (e.key.toLowerCase() === 'i' && state.project) {
      const t = Number((video.currentTime || 0).toFixed(2));
      state.project.highlight.start = Math.min(t, (state.project.highlight.end || 99) - 1);
      updateHighlightUI();
      scheduleSave();
      logTelemetry(t('tel.moments'), `Set Highlight IN = ${state.project.highlight.start}s`, '#ee1d6c');
    } else if (e.key.toLowerCase() === 'o' && state.project) {
      const t = Number((video.currentTime || 0).toFixed(2));
      state.project.highlight.end = Math.max(t, (state.project.highlight.start || 0) + 1);
      updateHighlightUI();
      scheduleSave();
      logTelemetry(t('tel.moments'), `Set Highlight OUT = ${state.project.highlight.end}s`, '#ee1d6c');
    } else if (e.key.toLowerCase() === 'e' && state.project) {
      runStage('ship');
    }
  });
}

function onFontSizeChange(val) {
  const px = parseInt(val, 10);
  document.getElementById('fontSizeVal').textContent = `${px} PX`;
  document.getElementById('liveCaptionStrip').style.fontSize = `${Math.max(24, Math.min(54, px))}px`;
  if (state.project) {
    state.project.font_size = px;
    scheduleSave();
  }
}

function toggleLayoutMode() {
  if (!state.project) return;
  const modes = ['FACECAM + GAME + CAPS', 'CENTER CROP', 'TIKTOK STORY / FACELESS'];
  const cur = state.project.layout_mode || 'FACECAM + GAME + CAPS';
  const idx = modes.indexOf(cur);
  const next = modes[(idx + 1) % modes.length];
  state.project.layout_mode = next;
  document.getElementById('btnLayoutToggle').textContent = next;
  document.getElementById('outputLayoutLabel').textContent = next;
  if (next === 'TIKTOK STORY / FACELESS') {
    setCaptionStyle('tiktok_white');
  }
  updateStreamMetaTag();
  syncQuickPresetUI();
  scheduleSave();
}

function cyclePlaybackSpeed() {
  const speeds = [
    { rate: 1.0, label: '1.0x ORIGINAL SPEED' },
    { rate: 0.5, label: '0.5x SLOW-MO (RIFE)' },
    { rate: 1.5, label: '1.5x FAST PREVIEW' },
  ];
  const idx = speeds.findIndex(s => Math.abs(s.rate - state.playbackSpeed) < 0.05);
  const next = speeds[(idx + 1) % speeds.length];
  state.playbackSpeed = next.rate;
  video.playbackRate = next.rate;
  const btn = document.getElementById('btnSpeedToggle');
  if (btn) btn.textContent = next.label;
}

function updateStreamMetaTag() {
  const el = document.getElementById('streamMetaTag');
  if (!el || !state.project) return;
  const info = state.project.info || {};
  const mode = (state.project.layout_mode || 'FACECAM').includes('FACECAM') ? 'FACECAM + GAME' : 'CENTER 9:16';
  el.textContent = `${info.width || 1920}x${info.height || 1080} · ${Math.round(info.fps || 30)}FPS · ${mode}`;
}

function getInterpolatedTrackX(t) {
  const pts = state.project?.face_track;
  if (!pts || pts.length < 2) return state.project?.crop_x ?? 0.5;
  if (t <= pts[0].t) return pts[0].x;
  if (t >= pts[pts.length - 1].t) return pts[pts.length - 1].x;
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    if (t >= a.t && t <= b.t) {
      const f = (t - a.t) / Math.max(0.001, b.t - a.t);
      return a.x + (b.x - a.x) * f;
    }
  }
  return state.project?.crop_x ?? 0.5;
}

function renderFaceCurves() {
  const pts = state.project?.face_track || [];
  const spark = document.getElementById('sparklineSvg');
  const tlSvg = document.getElementById('faceTimelineSvg');

  const data = pts.length ? pts : [{ t: 0, x: 0.5 }, { t: 1, x: 0.52 }, { t: 2, x: 0.49 }, { t: 3, x: 0.53 }];
  const w = 180, h = 44;
  const coords = data.map((p, i) => {
    const px = (i / Math.max(1, data.length - 1)) * (w - 14) + 7;
    const py = h - 6 - ((p.x - 0.2) / 0.6) * (h - 14);
    return [px, Math.max(6, Math.min(h - 6, py))];
  });
  const poly = coords.map(c => c.join(',')).join(' ');
  const dots = coords.map(c => `<circle cx="${c[0]}" cy="${c[1]}" r="2.6" fill="#6366f1" />`).join('');
  spark.innerHTML = `
    <polyline fill="none" stroke="#6366f1" stroke-width="2" points="${poly}" />
    ${dots}
    <line id="sparkPlayhead" x1="7" y1="2" x2="7" y2="${h - 2}" stroke="#ee1d6c" stroke-width="1.8" />
  `;

  // Timeline green line
  const tlCoords = data.map((p, i) => {
    const px = (i / Math.max(1, data.length - 1)) * 1000;
    const py = 22 - ((p.x - 0.2) / 0.6) * 18;
    return `${px},${Math.max(3, Math.min(23, py))}`;
  });
  tlSvg.innerHTML = `<polyline fill="none" stroke="#0e9f8e" stroke-width="2" points="${tlCoords.join(' ')}" />`;
}

function renderWaveform() {
  const el = document.getElementById('audioTrack');
  const wf = state.project?.waveform || Array.from({ length: 100 }, (_, i) => 0.15 + 0.7 * Math.abs(Math.sin(i * 0.3)));
  el.innerHTML = wf
    .map(v => `<div class="wave-bar" style="height:${Math.round(Math.max(12, v * 100))}%"></div>`)
    .join('');
}

// ---------------------------------------------------------------------------
// VIDEO TRANSPORT & LIVE 9:16 CANVAS COMPOSITOR
// ---------------------------------------------------------------------------
function setupVideoListeners() {
  video.addEventListener('timeupdate', () => {
    updateTimeDisplay();
    updateActiveCaptionForTime(video.currentTime);
  });
  video.addEventListener('play', () => {
    document.getElementById('btnPlayPause').innerHTML = '&#10074;&#10074;';
  });
  video.addEventListener('pause', () => {
    document.getElementById('btnPlayPause').innerHTML = '&#9654;';
  });
}

function togglePlay() {
  if (!video.src) return;
  if (video.paused) video.play();
  else video.pause();
}

function seekRelative(delta) {
  if (!video.duration) return;
  video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + delta));
}

function formatTimecode(sec) {
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  const f = Math.floor((sec - s) * 30);
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}:${String(f).padStart(2, '0')}`;
}

function updateTimeDisplay() {
  const cur = video.currentTime || 0;
  const dur = video.duration || state.project?.info?.duration || 0;
  document.getElementById('timecodeBig').textContent = formatTimecode(cur);
  document.getElementById('phaseTime').textContent = `${cur.toFixed(2).padStart(5, '0')} / ${dur.toFixed(2).padStart(5, '0')}`;

  const ratio = dur > 0 ? Math.min(1, cur / dur) : 0;
  document.getElementById('playProgressFill').style.width = `${ratio * 100}%`;

  // Move playhead
  const shell = document.getElementById('timelineShell');
  const trackEl = document.getElementById('filmstripTrack');
  if (trackEl) {
    const leftOffset = 84 + ratio * trackEl.clientWidth;
    document.getElementById('playhead').style.left = `${leftOffset}px`;
  }

  // Color audio bars
  const bars = document.querySelectorAll('.wave-bar');
  const playedCount = Math.floor(ratio * bars.length);
  bars.forEach((b, i) => b.classList.toggle('played', i <= playedCount));

  // Move sparkline playhead dynamically
  const sp = document.getElementById('sparkPlayhead');
  if (sp) {
    const sx = 7 + ratio * 166;
    sp.setAttribute('x1', sx.toFixed(1));
    sp.setAttribute('x2', sx.toFixed(1));
  }

  // Live face-track crop interpolation during playback
  if (state.project && !state.userDraggingCrop && state.project.face_track?.length > 1) {
    const liveX = getInterpolatedTrackX(cur);
    state.project.crop_x = liveX;
    updateCropBoxUI();
  }

  // Live VU meter on segmented bars during playback (when not exporting)
  if (!state.isExporting && state.project?.waveform?.length && !video.paused) {
    const wf = state.project.waveform;
    const wIdx = Math.min(wf.length - 1, Math.floor(ratio * wf.length));
    const level = wf[wIdx] || 0.3;
    setSegMeter(Math.max(2, Math.min(10, Math.round(level * 10))));
  }
}

function updateActiveCaptionForTime(t) {
  const caps = state.project?.captions || [];
  if (!caps.length) return;
  const idx = caps.findIndex(c => t >= c.start && t <= c.end);
  if (idx !== -1 && idx !== state.selectedCaptionIdx) {
    state.selectedCaptionIdx = idx;
    const c = caps[idx];
    document.getElementById('liveCaptionStrip').textContent = c.text;
    document.getElementById('captionInput').value = c.text;
    document.getElementById('wordsTiming').textContent = `${(c.end - c.start).toFixed(2)}s`;
    document.querySelectorAll('.cap-block').forEach((el, i) => el.classList.toggle('active', i === idx));
  }
}

function renderVerticalLoop() {
  if (video.readyState >= 2 && state.project) {
    const vw = video.videoWidth || 1920;
    const vh = video.videoHeight || 1080;
    const cw = vCanvas.width;
    const ch = vCanvas.height;
    const cx = state.project.crop_x ?? 0.5;
    const split = (state.project.layout_mode || 'FACECAM').includes('FACECAM');

    vCtx.fillStyle = '#000';
    vCtx.fillRect(0, 0, cw, ch);

    if (split) {
      const fb = state.project.face_box || { x: 0.02, y: 0.16, w: 0.2, h: 0.26 };
      const topH = Math.round(ch * 0.375);
      vCtx.drawImage(video, fb.x * vw, fb.y * vh, fb.w * vw, fb.h * vh, 0, 0, cw, topH);

      const gw = Math.min(vw, vh * (cw / (ch - topH)));
      const gx = Math.max(0, Math.min(vw - gw, cx * vw - gw / 2));
      vCtx.drawImage(video, gx, 0, gw, vh, 0, topH, cw, ch - topH);
    } else {
      const gw = Math.min(vw, vh * (9 / 16));
      const gx = Math.max(0, Math.min(vw - gw, cx * vw - gw / 2));
      vCtx.drawImage(video, gx, 0, gw, vh, 0, 0, cw, ch);
    }

    // Burn live caption preview on 9:16 canvas
    const capText = document.getElementById('liveCaptionStrip').textContent || '';
    if (capText.trim()) {
      const styleCfg = CAPTION_STYLE_MAP[state.captionStyle] || CAPTION_STYLE_MAP.pink;
      vCtx.save();
      const yPos = split ? Math.round(ch * 0.39) : Math.round(ch * 0.78);
      if (styleCfg.outline) {
        vCtx.font = 'italic 900 17px Anton, Impact, "Arial Black", sans-serif';
        vCtx.textAlign = 'center';
        vCtx.lineWidth = 4;
        vCtx.strokeStyle = '#000000';
        vCtx.strokeText(capText.slice(0, 24), cw / 2, yPos + 1);
        vCtx.fillStyle = styleCfg.fg;
        vCtx.fillText(capText.slice(0, 24), cw / 2, yPos + 1);
      } else {
        vCtx.font = 'bold 15px Anton, Impact, sans-serif';
        vCtx.textAlign = 'center';
        vCtx.fillStyle = styleCfg.bg;
        const wTxt = Math.min(cw - 14, vCtx.measureText(capText).width + 14);
        vCtx.fillRect((cw - wTxt) / 2, yPos - 14, wTxt, 20);
        vCtx.fillStyle = styleCfg.fg;
        vCtx.fillText(capText.slice(0, 22), cw / 2, yPos + 1);
      }
      vCtx.restore();
    }

    if (document.getElementById('chkTracked')?.checked) drawTrackedPreview(vw, vh, cw, ch, cx);

    // Live animated pink retention bar at bottom of 9:16 preview
    if (document.getElementById('chkRetention')?.checked && video.duration) {
      const pct = Math.max(0, Math.min(1, (video.currentTime || 0) / video.duration));
      vCtx.fillStyle = '#ee1d6c';
      vCtx.fillRect(0, ch - 4, Math.round(cw * pct), 4);
    }
  }
  requestAnimationFrame(renderVerticalLoop);
}

// ---------------------------------------------------------------------------
// DRAGGABLE OVERLAYS & TIMELINE SCRUB
// ---------------------------------------------------------------------------
function setupCropDrag() {
  const cropBox = document.getElementById('cropBox');
  const wrap = document.getElementById('monitorWrap');
  let dragging = false;

  cropBox.addEventListener('mousedown', e => {
    dragging = true;
    state.userDraggingCrop = true;
    e.preventDefault();
  });
  window.addEventListener('mousemove', e => {
    if (!dragging || !state.project) return;
    const rect = wrap.getBoundingClientRect();
    const xNorm = (e.clientX - rect.left) / rect.width;
    state.project.crop_x = Math.max(0.16, Math.min(0.84, xNorm));
    updateCropBoxUI();
  });
  window.addEventListener('mouseup', () => {
    if (dragging) {
      dragging = false;
      state.userDraggingCrop = false;
      scheduleSave();
    }
  });
}

function setupFaceDrag() {
  const faceBox = document.getElementById('faceBox');
  const wrap = document.getElementById('monitorWrap');
  let dragging = false;
  let offsetX = 0, offsetY = 0;

  faceBox.addEventListener('mousedown', e => {
    dragging = true;
    const rect = faceBox.getBoundingClientRect();
    offsetX = e.clientX - rect.left;
    offsetY = e.clientY - rect.top;
    e.preventDefault();
  });
  window.addEventListener('mousemove', e => {
    if (!dragging || !state.project) return;
    const rect = wrap.getBoundingClientRect();
    const fb = state.project.face_box;
    fb.x = Math.max(0, Math.min(1 - fb.w, (e.clientX - rect.left - offsetX) / rect.width));
    fb.y = Math.max(0, Math.min(1 - fb.h, (e.clientY - rect.top - offsetY) / rect.height));
    updateFaceBoxUI();
  });
  window.addEventListener('mouseup', () => {
    if (dragging) {
      dragging = false;
      scheduleSave();
    }
  });
}

function setupTimelineScrub() {
  const shell = document.getElementById('timelineShell');
  const scrub = e => {
    const track = document.getElementById('filmstripTrack');
    if (!track || !video.duration) return;
    const rect = track.getBoundingClientRect();
    if (e.clientX < rect.left) return;
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    video.currentTime = ratio * video.duration;
  };
  let down = false;
  shell.addEventListener('mousedown', e => {
    down = true;
    scrub(e);
  });
  window.addEventListener('mousemove', e => {
    if (down) scrub(e);
  });
  window.addEventListener('mouseup', () => {
    down = false;
  });
}

function setPreviewMode(mode) {
  state.previewMode = mode;
  document.getElementById('btnModeEdit').classList.toggle('active', mode === 'edit');
  document.getElementById('btnModePreview').classList.toggle('active', mode === 'preview');
  document.getElementById('overlayLayer').style.display = mode === 'preview' ? 'none' : 'block';
}

function scheduleSave() {
  if (!state.project) return;
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    await fetch(`/api/projects/${state.project.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        captions: state.project.captions,
        crop_x: state.project.crop_x,
        font_size: state.project.font_size,
        layout_mode: state.project.layout_mode,
        face_box: state.project.face_box,
        highlight: state.project.highlight,
      }),
    });
  }, 400);
}

// ---------------------------------------------------------------------------
// PIPELINE STAGE ACTIONS & JOB POLLING
// ---------------------------------------------------------------------------
let _lastTelMsg = '';
function setPhaseUI(stage, msg, activeRepos = [], percent = 0) {
  document.getElementById('phaseTag').textContent = stage;
  const simple = document.body.classList.contains('ui-simple');
  let shown = String(msg || '');
  if (simple) {
    shown = shown.replace(/^\d{2} \/ [A-Z]+\s*[—–-]?\s*/i, '').trim();
    if (shown.length > 48) shown = shown.slice(0, 45).trim() + '…';
  }
  document.getElementById('phaseMsg').textContent = shown || 'Prêt';
  const pbar = document.getElementById('phaseProgressBar');
  const pct = Math.max(0, Math.min(100, Number(percent) || 0));
  if (pbar) {
    pbar.style.width = `${pct}%`;
    pbar.classList.toggle('is-busy', pct > 0 && pct < 100);
  }
  const pTime = document.getElementById('phaseTime');
  if (pTime && pct > 0 && pct < 100) {
    pTime.textContent = `${Math.round(pct)} %`;
  }
  if (msg && msg !== _lastTelMsg) {
    _lastTelMsg = msg;
    logTelemetry(stage, msg, '#ee1d6c');
  }
  state.activeRepos = activeRepos;
  renderReposDesk();
  if (window.agentSwarm) {
    window.agentSwarm.focusStage(stage);
  }
  if (stage.includes('SHIP')) {
    state.isExporting = percent < 100;
    const btn = document.getElementById('btnExport');
    btn.classList.add('exporting');
    btn.textContent = percent < 100 ? `EXPORT ${percent}%` : 'EXPORTER LE SHORT';
    setSegMeter(Math.round(percent / 10));
    if (percent >= 100) btn.classList.remove('exporting');
  } else {
    state.isExporting = false;
  }
}

function setSegMeter(litCount) {
  document.querySelectorAll('#segMeter span').forEach((s, i) => s.classList.toggle('lit', i < litCount));
}

async function extractJobId(res, label = 'Job') {
  let body = {};
  try {
    body = await res.json();
  } catch {
    body = {};
  }
  if (!res.ok || !body.job_id) {
    const detail =
      typeof body.detail === 'string'
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map(d => d.msg || JSON.stringify(d)).join('; ')
          : `HTTP ${res.status}`;
    logTelemetry('API', `${label} refusé (${res.status}): ${detail}`, '#ee1d6c');
    alert(t('msg.refused', { label, status: res.status, detail }));
    return null;
  }
  return body.job_id;
}

function stopJobPoller(jobId) {
  const t = state.activePollers.get(jobId);
  if (t) {
    clearInterval(t);
    state.activePollers.delete(jobId);
  }
}

async function pollJob(jobId, onDone) {
  if (!jobId) return;
  stopJobPoller(jobId);
  const timer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (res.status === 404 || res.status >= 400) {
        stopJobPoller(jobId);
        logTelemetry('JOB', `Suivi arrêté pour ${jobId} (HTTP ${res.status})`, '#ee1d6c');
        return;
      }
      const job = await res.json();
      setPhaseUI(job.stage, job.message, job.active_repos || [], job.percent || 0);

      if (job.status === 'done') {
        stopJobPoller(jobId);
        if (state.activePollers.size === 0) {
          state.activeRepos = [];
          renderReposDesk();
        }
        if (window.agentSwarm) window.agentSwarm.resumePatrol();
        if (onDone) onDone(job.result);
      } else if (job.status === 'error') {
        stopJobPoller(jobId);
        if (state.activePollers.size === 0) {
          state.activeRepos = [];
          renderReposDesk();
        }
        if (window.agentSwarm) window.agentSwarm.resumePatrol();
        const err = String(job.error || job.message || '');
        const qcFail = /QC échoué|REJECTED_/i.test(err);
        if (qcFail) {
          logTelemetry('QC', err, '#ee1d6c');
          showToast(err, 'warn');
          const pid = state.project && state.project.id;
          if (pid) {
            fetch(`/api/projects/${encodeURIComponent(pid)}`)
              .then(r => (r.ok ? r.json() : null))
              .then(p => { if (p) applyProject(p); })
              .catch(() => {});
          }
        } else {
          alert(t('msg.pipelineError', { err: job.error }));
        }
      }
    } catch {
      // keep polling on transient network blip
    }
  }, 600);
  state.activePollers.set(jobId, timer);
}

async function pullFromUrl() {
  const url = document.getElementById('urlInput').value.trim();
  if (!url) return alert(t('msg.needUrl'));
  const engine = document.getElementById('pullEngine').value;
  setPhaseUI('01 / PULL', t('msg.pullStart'), ['01', '02']);
  const res = await fetch('/api/pull', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, source_type: engine }),
  });
  const jobId = await extractJobId(res, '01 / PULL');
  if (jobId) pollJob(jobId, proj => applyProject(proj));
}

async function uploadLocalFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', file.name.replace(/\.[^.]+$/, '').toUpperCase());
  setPhaseUI('01 / PULL', `Importing ${file.name}...`, ['01']);
  const res = await fetch('/api/upload', { method: 'POST', body: fd });
  const jobId = await extractJobId(res, 'UPLOAD');
  if (jobId) pollJob(jobId, proj => applyProject(proj));
}

function pollJobPromise(jobId) {
  return new Promise((resolve, reject) => {
    if (!jobId) return reject(new Error('Missing job_id'));
    stopJobPoller(jobId);
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (res.status === 404 || res.status >= 400) {
          stopJobPoller(jobId);
          return reject(new Error(`Job ${jobId} introuvable (HTTP ${res.status})`));
        }
        const job = await res.json();
        setPhaseUI(job.stage, job.message, job.active_repos || [], job.percent || 0);
        if (job.status === 'done') {
          stopJobPoller(jobId);
          if (state.activePollers.size === 0) {
            state.activeRepos = [];
            renderReposDesk();
          }
          resolve(job.result);
        } else if (job.status === 'error') {
          stopJobPoller(jobId);
          if (state.activePollers.size === 0) {
            state.activeRepos = [];
            renderReposDesk();
          }
          reject(new Error(job.error || 'Pipeline error'));
        }
      } catch (e) {
        stopJobPoller(jobId);
        reject(e);
      }
    }, 550);
    state.activePollers.set(jobId, timer);
  });
}

async function runStage(stage) {
  if (!state.project) return alert(t('msg.needSource'));
  const pid = state.project.id;
  let endpoint = `/api/stage/${stage}`;
  let payload = { project_id: pid };

  if (stage === 'find') {
    payload.strip_silence = document.getElementById('chkSilence').checked;
  } else if (stage === 'caption') {
    payload.model_size = document.getElementById('whisperModel').value;
    payload.language = document.getElementById('whisperLang')?.value || 'auto';
  } else if (stage === 'ship') {
    const hl = state.project.highlight || {};
    payload = {
      project_id: pid,
      layout_mode: state.project.layout_mode || 'FACECAM + GAME + CAPS',
      crop_x: state.project.crop_x ?? 0.5,
      font_size: state.project.font_size || 40,
      caption_style: state.captionStyle || 'pink',
      use_demucs: document.getElementById('chkDemucs').checked,
      lossless_trim: document.getElementById('chkLossless').checked,
      playback_speed: state.playbackSpeed || 1.0,
      upscale_sharpen: document.getElementById('chkUpscale')?.checked || false,
      retention_bar: document.getElementById('chkRetention')?.checked ?? true,
      normalize_audio: document.getElementById('chkRetention')?.checked ?? true,
      start_sec: hl.start,
      end_sec: hl.end,
      tracked: getTrackedOptions(),
      export_preset: selectedExportPreset(),
    };
  }

  const res = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const jobId = await extractJobId(res, 'BATCH 3 SHORTS');
  if (!jobId) return;
  pollJob(jobId, result => {
    const proj = result.project || result;
    applyProject(proj);
  });
}

async function runFullPipeline() {
  if (!state.project) return alert(t('msg.needSource'));
  const res = await fetch('/api/stage/full', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: state.project.id,
      strip_silence: document.getElementById('chkSilence').checked,
      model_size: document.getElementById('whisperModel').value,
      language: document.getElementById('whisperLang')?.value || 'auto',
      layout_mode: state.project.layout_mode || 'FACECAM + GAME + CAPS',
      tracked: getTrackedOptions(),
      export_preset: selectedExportPreset(),
      translate_to: selectedTranslateTo(),
    }),
  });
  const jobId = await extractJobId(res, 'BATCH 3 SHORTS');
  if (!jobId) return;
  pollJob(jobId, result => {
    const proj = result.project || result;
    applyProject(proj);
  });
}

// ===========================================================================
// REAL BACKEND-DRIVEN AI AGENTS OVERLAY (GROQ LLM + CV + FFMPEG)
// Zero fake patrol timers or Math.random() movement: cursors only appear and
// move when real backend agents emit events via GET /api/agents/live.
// ===========================================================================
class RealAgentOverlayController {
  constructor() {
    this.stageEl = document.getElementById('agentStage');
    this.badgesEl = document.getElementById('agentsBadges');
    this.counterEl = document.getElementById('agentsCounter');
    this.visible = true;
    this.lastSeq = 0;
    this.cursors = new Map();
    this.llmInfo = 'GROQ';
    this.pollTimer = setInterval(() => this._pollBackend(), 650);
    this._pollBackend();
  }

  _cursorSvg(hex) {
    return `
      <svg viewBox="0 0 24 24" fill="none">
        <path d="M4 3L20 11.2L12.6 13.4L9.8 20.8L4 3Z" fill="${hex}" stroke="#ffffff" stroke-width="1.8" stroke-linejoin="round"/>
      </svg>
    `;
  }

  _ensureCursor(ev) {
    let entry = this.cursors.get(ev.agent_id);
    if (!entry) {
      const el = document.createElement('div');
      el.className = `agent-cursor ${ev.color || 'pink'}`;
      el.dataset.agentId = ev.agent_id;
      el.innerHTML = `
        ${this._cursorSvg(ev.hex || '#ee1d6c')}
        <div class="agent-pill">
          <span class="agent-name-txt"></span>
          <span class="agent-action-sub"></span>
        </div>
      `;
      this.stageEl?.appendChild(el);
      entry = { el };
      this.cursors.set(ev.agent_id, entry);
    }
    entry.el.className = `agent-cursor ${ev.color || 'pink'}`;
    const nameEl = entry.el.querySelector('.agent-name-txt');
    const subEl = entry.el.querySelector('.agent-action-sub');
    if (nameEl) nameEl.textContent = ev.agent_name;
    if (subEl) subEl.textContent = ev.task;
    return entry;
  }

  _positionCursor(ev, ripple = false) {
    const entry = this._ensureCursor(ev);
    const targetEl = document.querySelector(ev.target || '#monitorWrap');
    if (!targetEl || !this.visible) {
      entry.el.classList.remove('visible');
      return;
    }
    const rect = targetEl.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) {
      entry.el.classList.remove('visible');
      return;
    }
    const docLeft = rect.left + window.scrollX;
    const docTop = rect.top + window.scrollY;
    const maxW = Math.max(document.documentElement.clientWidth, window.innerWidth) - 260;
    const x = Math.max(16, Math.min(maxW, docLeft + rect.width * (ev.x_norm ?? 0.5)));
    const y = Math.max(16, docTop + rect.height * (ev.y_norm ?? 0.5));
    entry.el.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(y)}px, 0)`;
    entry.el.classList.add('visible');

    if (ripple) {
      const r = document.createElement('div');
      r.className = 'agent-ripple';
      r.style.left = `${x}px`;
      r.style.top = `${y}px`;
      r.style.borderColor = ev.hex || '#ee1d6c';
      document.body.appendChild(r);
      setTimeout(() => r.remove(), 680);
    }
  }

  _hideAllExcept(ids) {
    const keep = new Set(ids);
    for (const [id, entry] of this.cursors.entries()) {
      if (!keep.has(id)) entry.el.classList.remove('visible');
    }
  }

  async _pollBackend() {
    try {
      const res = await fetch(`/api/agents/live?since=${this.lastSeq}`);
      if (!res.ok) return;
      const data = await res.json();
      this.llmInfo = data.llm_ready ? `${(data.llm_provider || 'groq').toUpperCase()} · ${data.llm_model}` : 'LOCAL CPU-CV';

      const newEvents = data.events || [];
      for (const ev of newEvents) {
        this.lastSeq = Math.max(this.lastSeq, ev.seq || 0);
        const detail = ev.thought ? `${ev.task} — ${ev.thought}` : ev.task;
        logTelemetry(ev.agent_name, detail, ev.hex || '#ee1d6c');
      }

      const active = data.active_agents || [];
      if (this.counterEl) {
        this.counterEl.textContent =
          active.length > 0
            ? `REAL AGENTS ACTIVE (${active.length}) · ${this.llmInfo}`
            : `REAL AGENTS IDLE · ${this.llmInfo}`;
      }

      if (!this.visible || active.length === 0) {
        this._hideAllExcept([]);
        if (this.badgesEl) {
          this.badgesEl.innerHTML = this.visible
            ? `<span class="muted" style="font-size:10px">${t('agents.wait')}</span>`
            : `<span class="muted" style="font-size:10px">Curseurs masqués — clique REAL AGENTS pour afficher</span>`;
        }
        return;
      }

      this._hideAllExcept(active.map(a => a.agent_id));
      active.forEach(ev => this._positionCursor(ev, newEvents.some(n => n.seq === ev.seq)));

      if (this.badgesEl) {
        this.badgesEl.textContent = '';
        for (const ag of active) {
          const chip = document.createElement('div');
          chip.className = 'agent-chip working';
          chip.title = String(ag.thought || '');
          const dot = document.createElement('span');
          dot.className = 'chip-dot';
          dot.style.background = ag.hex || '#ee1d6c';
          const nm = document.createElement('span');
          nm.textContent = String(ag.agent_name || '');
          const tk = document.createElement('span');
          tk.className = 'chip-task';
          tk.textContent = ` · ${ag.task || ''}`;
          chip.append(dot, nm, tk);
          this.badgesEl.appendChild(chip);
        }
      }
    } catch {
      // ignore transient network errors
    }
  }

  setVisible(flag) {
    this.visible = flag;
    document.getElementById('btnAgentsToggle')?.classList.toggle('active', flag);
    if (!flag) this._hideAllExcept([]);
    this._pollBackend();
  }

  // Called by job polling — backend already emits real events, just trigger immediate poll
  focusStage() {
    this._pollBackend();
  }

  resumePatrol() {
    this._pollBackend();
  }

  async runAutonomousCrew() {
    const demoBtn = document.getElementById('btnSwarmDemo');
    if (demoBtn) demoBtn.textContent = 'AI CREW RUNNING...';
    this.visible = true;
    document.getElementById('btnAgentsToggle')?.classList.add('active');

    try {
      if (!state.project) {
        alert(t('msg.needSource'));
        return;
      }

      const res = await fetch('/api/agents/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: state.project.id, export_mp4: true }),
      });
      const jobId = await extractJobId(res, 'AI CREW');
      if (!jobId) return;
      const result = await pollJobPromise(jobId);
      const finalProj = result.project || result;
      applyProject(finalProj);
      if (result.export?.filename) {
        watchShort(result.export.filename);
      }
    } catch (err) {
      alert(`AI Crew error: ${err.message}`);
    } finally {
      if (demoBtn) demoBtn.textContent = 'RUN AI CREW';
      this._pollBackend();
    }
  }
}

function initAgentSwarm() {
  window.agentSwarm = new RealAgentOverlayController();
}

function toggleAgentsVisible() {
  if (!window.agentSwarm) return;
  window.agentSwarm.setVisible(!window.agentSwarm.visible);
}

function triggerSwarmChoreography() {
  if (!window.agentSwarm) return;
  window.agentSwarm.runAutonomousCrew();
}

function setCaptionStyle(style, log = true) {
  state.captionStyle = style;
  const cfg = CAPTION_STYLE_MAP[style] || CAPTION_STYLE_MAP.pink;
  const strip = document.getElementById('liveCaptionStrip');
  if (strip) {
    strip.style.color = cfg.fg;
    strip.style.background = style === 'pink' ? 'var(--surface)' : '#171717';
  }
  document.querySelectorAll('.swatch-btn').forEach(b => b.classList.toggle('active', b.dataset.style === style));
  if (state.project) state.project.caption_style = style;
  if (log) logTelemetry(t('tel.animated'), `Caption style set to ${style.toUpperCase()}`, cfg.fg === '#ffffff' ? '#171717' : cfg.fg);
}

async function renderPublishPanel() {
  if (!state.project) return;
  const exportsList = document.getElementById('exportsList');
  const exps = state.project.exports || [];
  if (exportsList) {
    exportsList.textContent = '';
    if (!exps.length) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.style.fontSize = '11px';
      empty.textContent = 'No 9:16 exports yet.';
      exportsList.appendChild(empty);
    } else {
      for (const ex of exps.slice(0, 8)) {
        const fname = String(ex.filename || '');
        const safeUrl = `/media/outputs/${encodeURIComponent(fname)}`;
        const row = document.createElement('div');
        row.className = 'export-row';

        const infoSpan = document.createElement('span');
        const strong = document.createElement('strong');
        strong.textContent = fname;
        const sizeSpan = document.createElement('span');
        sizeSpan.className = 'muted';
        sizeSpan.textContent = ` (${ex.size_mb} MB)`;
        infoSpan.append(strong, sizeSpan);

        const acts = document.createElement('div');
        acts.className = 'export-row-actions';

        const watchBtn = document.createElement('button');
        watchBtn.className = 'cap-mini-btn';
        watchBtn.textContent = '▶ WATCH 9:16';
        watchBtn.addEventListener('click', () => watchShort(fname));

        const dlLink = document.createElement('a');
        dlLink.href = safeUrl;
        dlLink.target = '_blank';
        dlLink.download = fname;
        dlLink.textContent = 'MP4 ↓';

        const delBtn = document.createElement('button');
        delBtn.className = 'cap-mini-btn danger';
        delBtn.title = 'Supprimer cet export';
        delBtn.textContent = '🗑';
        delBtn.addEventListener('click', () => deleteExport(fname));

        if (ex.tracked) {
          const tag = document.createElement('span');
          const rejected = !!(ex.tracked.rejected || ex.tracked.qc_pass === false || String(fname).startsWith('REJECTED_'));
          const qc = (ex.tracked.report || {}).qc || {};
          tag.className = 'tracked-tag' + (rejected ? ' rejected' : '');
          tag.textContent = rejected ? 'REJETÉ' : `SUIVI ${qc.pass ? 'QC OK' : 'QC ?'}`;
          infoSpan.appendChild(tag);
        }
        acts.append(watchBtn, dlLink, delBtn);
        row.append(infoSpan, acts);
        exportsList.appendChild(row);
      }
    }
  }
  try {
    const res = await fetch(`/api/projects/${state.project.id}/viral-kit`);
    if (res.ok) {
      const kit = await res.json();
      state.project.viral_kit = kit;
      document.getElementById('viralTitle').textContent = kit.title;
      document.getElementById('viralTags').textContent = kit.hashtags;
    }
  } catch {}
}

function copyViralKit() {
  const kit = state.project?.viral_kit;
  const text = kit
    ? `${kit.title}\n\n${kit.description}`
    : `${document.getElementById('viralTitle').textContent}\n\n${document.getElementById('viralTags').textContent}`;
  navigator.clipboard?.writeText(text);
  logTelemetry('VIRAL KIT', 'Titre et hashtags copiés dans le presse-papiers', '#0e9f8e');
  showToast(t('msg.titleCopied'), 'ok');
}

async function runBatchShip() {
  if (!state.project) return alert(t('msg.needSource'));
  logTelemetry(t('tel.moments'), 'Launching BATCH 3 SHORTS (9:16 H.264)...', '#0e9f8e');
  const res = await fetch('/api/stage/batch-ship', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: state.project.id,
      layout_mode: state.project.layout_mode || 'FACECAM + GAME + CAPS',
      crop_x: state.project.crop_x ?? 0.5,
      font_size: state.project.font_size || 40,
      caption_style: state.captionStyle || 'pink',
      playback_speed: state.playbackSpeed || 1.0,
      upscale_sharpen: document.getElementById('chkUpscale')?.checked || false,
      tracked: getTrackedOptions(),
      export_preset: selectedExportPreset(),
    }),
  });
  const jobId = await extractJobId(res, 'BATCH 3 SHORTS');
  if (!jobId) return;
  pollJob(jobId, result => {
    const proj = result.project || result;
    applyProject(proj);
  });
}

function watchShort(filename) {
  const modal = document.getElementById('shortPreviewModal');
  const vid = document.getElementById('shortModalVideo');
  const title = document.getElementById('shortModalTitle');
  const dl = document.getElementById('shortModalDownload');
  const url = `/media/outputs/${filename}`;
  title.textContent = `${filename} · 9:16 H.264`;
  dl.href = url;
  vid.src = url;
  modal.classList.remove('hidden');
  vid.play().catch(() => {});
  logTelemetry(t('tel.export'), `Previewing 9:16 export ${filename}`, '#0e9f8e');
}

function closeShortModal() {
  const modal = document.getElementById('shortPreviewModal');
  const vid = document.getElementById('shortModalVideo');
  vid.pause();
  vid.src = '';
  modal.classList.add('hidden');
}

async function openOutputsFolder() {
  const res = await fetch('/api/open-outputs', { method: 'POST' });
  const data = await res.json();
  logTelemetry('SYSTEM', `Opened outputs folder: ${data.path}`, '#0e9f8e');
}

async function deleteCurrentProject() {
  if (!state.project) return;
  if (!confirm(`Supprimer le projet ${state.project.name} ?`)) return;
  const pid = state.project.id;
  await fetch(`/api/projects/${pid}`, { method: 'DELETE' });
  logTelemetry('SYSTEM', `Deleted project ${pid}`, '#ee1d6c');
  state.project = null;
  await loadLatestProject();
}

async function deleteExport(filename) {
  if (!confirm(`Supprimer ${filename} ?`)) return;
  const pid = state.project?.id || '';
  await fetch(`/api/outputs/${encodeURIComponent(filename)}?project_id=${encodeURIComponent(pid)}`, { method: 'DELETE' });
  logTelemetry('SYSTEM', `Deleted export ${filename}`, '#ee1d6c');
  if (state.project) {
    state.project.exports = (state.project.exports || []).filter(e => e.filename !== filename);
    renderPublishPanel();
  }
}

async function runRemotionRender() {
  if (!state.project) return alert(t('msg.needSource'));
  logTelemetry(t('tel.animated'), 'Starting animated-captions render...', '#6366f1');
  const res = await fetch('/api/stage/remotion', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: state.project.id }),
  });
  const jobId = await extractJobId(res, '18 REMOTION');
  if (!jobId) return;
  pollJob(jobId, result => {
    const proj = result.project || result;
    applyProject(proj);
    if (result.export?.filename) watchShort(result.export.filename);
  });
}

init();


// ===========================================================================
// TRACKED mode: followed 9:16 crop · facecam inset on top · optional caption profanity filter · QC
// ===========================================================================
function getTrackedOptions() {
  const on = document.getElementById('chkTracked')?.checked || false;
  if (!on) return { enabled: false };
  return {
    enabled: true,
    strict: document.getElementById('chkTrackedStrict').checked,
    facecam_inset: document.getElementById('chkFacecamInset').checked,
    tracked_crop: document.getElementById('chkTrackCrop').checked,
    censor_captions: document.getElementById('chkCensor').checked,
    censor_mode: document.getElementById('censorMode').value,
  };
}

function syncTrackedBar() {
  document.getElementById('trackedBar').classList.toggle('active', document.getElementById('chkTracked').checked);
}

function applyTrackedToUI(proj) {
  const o = (proj.tracked || {}).options || {};
  document.getElementById('chkTracked').checked = !!o.enabled;
  if (o.strict !== undefined) document.getElementById('chkTrackedStrict').checked = !!o.strict;
  if (o.facecam_inset !== undefined) document.getElementById('chkFacecamInset').checked = !!o.facecam_inset;
  if (o.tracked_crop !== undefined) document.getElementById('chkTrackCrop').checked = !!o.tracked_crop;
  if (o.censor_captions !== undefined) document.getElementById('chkCensor').checked = !!o.censor_captions;
  if (o.censor_mode) document.getElementById('censorMode').value = o.censor_mode;
  syncTrackedBar();
}

async function onTrackedToggle() {
  syncTrackedBar();
  syncQuickPresetUI();
  if (!state.project) return;
  const opts = getTrackedOptions();
  state.project.tracked = { ...(state.project.tracked || {}), options: opts };
  await fetch(`/api/projects/${state.project.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tracked_options: opts }),
  });
  logTelemetry('TRACKED', opts.enabled ? t('msg.trackedOn') : t('msg.trackedOff'), '#1a7a00');
}

function drawTrackedPreview(vw, vh, cw, ch, cx) {
  // Approximate preview: full-height game crop + facecam inset on top + caption
  const gw = Math.min(vw, vh * (9 / 16));
  const gx = Math.max(0, Math.min(vw - gw, cx * vw - gw / 2));
  vCtx.drawImage(video, gx, 0, gw, vh, 0, 0, cw, ch);
  const fb = state.project.face_box || {};
  if (document.getElementById('chkFacecamInset').checked && fb.w) {
    const iw = Math.round(cw * 0.46);
    const ih = Math.round(iw * (fb.h * vh) / (fb.w * vw));
    const ox = Math.round((cw - iw) / 2);
    const oy = Math.round(ch * 0.06);
    vCtx.fillStyle = '#fff';
    vCtx.fillRect(ox - 2, oy - 2, iw + 4, ih + 4);
    vCtx.drawImage(video, fb.x * vw, fb.y * vh, fb.w * vw, fb.h * vh, ox, oy, iw, ih);
  }
  const capText = document.getElementById('liveCaptionStrip').textContent || '';
  if (capText.trim()) {
    vCtx.fillStyle = '#FFE600';
    vCtx.font = 'bold 16px Anton, Impact, sans-serif';
    vCtx.textAlign = 'center';
    vCtx.fillText(capText.toUpperCase().slice(0, 18), cw / 2, Math.round(ch * 0.66));
  }
}


// ===========================================================================
// SIMPLE / PRO UI MODE & 1-CLICK QUICK PRESETS
// ===========================================================================
function setUiMode(mode) {
  const isPro = mode === 'pro';
  document.body.classList.toggle('ui-simple', !isPro);
  document.getElementById('btnUiSimple')?.classList.toggle('active', !isPro);
  document.getElementById('btnUiPro')?.classList.toggle('active', isPro);
  document.getElementById('btnAvance')?.classList.toggle('active', isPro);
  try {
    localStorage.setItem('clipstudio_ui_mode', isPro ? 'pro' : 'simple');
  } catch (_) {}
  const stage = document.getElementById('agentStage');
  if (stage) stage.style.display = isPro ? '' : 'none';
}

function toggleAvance() {
  setUiMode(document.body.classList.contains('ui-simple') ? 'pro' : 'simple');
}

function syncQuickPresetUI() {
  const trackedOn = !!document.getElementById('chkTracked')?.checked;
  const layout = (state.project && state.project.layout_mode) || 'FACECAM + GAME + CAPS';
  const activeId = trackedOn
    ? 'presetTracked'
    : layout === 'TIKTOK STORY / FACELESS'
      ? 'presetTiktok'
      : 'presetFacecam';
  for (const id of ['presetTiktok', 'presetFacecam', 'presetTracked']) {
    document.getElementById(id)?.classList.toggle('active', id === activeId);
  }
}

function selectQuickPreset(preset) {
  const chkT = document.getElementById('chkTracked');
  if (preset === 'tracked') {
    if (chkT) chkT.checked = true;
    onTrackedToggle();
    syncQuickPresetUI();
    return;
  }
  if (chkT && chkT.checked) {
    chkT.checked = false;
    onTrackedToggle();
  }
  const targetLayout = preset === 'tiktok' ? 'TIKTOK STORY / FACELESS' : 'FACECAM + GAME + CAPS';
  if (state.project) {
    state.project.layout_mode = targetLayout;
    scheduleSave();
  }
  const btn = document.getElementById('btnLayoutToggle');
  if (btn) btn.textContent = targetLayout;
  const lbl = document.getElementById('outputLayoutLabel');
  if (lbl) lbl.textContent = targetLayout;
  if (preset === 'tiktok') {
    setCaptionStyle('tiktok_white');
  } else {
    setCaptionStyle('pink');
  }
  updateStreamMetaTag();
  syncQuickPresetUI();
}

function showToast(msg, tone = '') {
  const host = document.getElementById('toastHost');
  if (!host) return;
  const el = document.createElement('div');
  el.className = `toast-item ${tone}`;
  el.textContent = String(msg || '');
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 220);
  }, 2400);
}

function setupFileDrop() {
  const targets = [document.querySelector('.ingest-bar'), document.getElementById('monitorWrap')].filter(Boolean);
  for (const el of targets) {
    el.addEventListener('dragover', e => {
      e.preventDefault();
      el.classList.add('drop-active');
    });
    el.addEventListener('dragleave', () => el.classList.remove('drop-active'));
    el.addEventListener('drop', e => {
      e.preventDefault();
      el.classList.remove('drop-active');
      const file = e.dataTransfer?.files?.[0];
      if (file) {
        showToast(t('msg.importing', { name: file.name }));
        uploadLocalFile({ target: { files: [file] } });
      }
    });
  }
}
