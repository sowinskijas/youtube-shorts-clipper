const form = document.getElementById('clip-form');
const submitBtn = document.getElementById('submit-btn');
const btnLabel = document.getElementById('btn-label');
const progressCard = document.getElementById('progress-card');
const progressFill = document.getElementById('progress-fill');
const progressStatus = document.getElementById('progress-status');
const progressStepBadge = document.getElementById('progress-step-badge');
const progressDetail = document.getElementById('progress-detail');
const errorCard = document.getElementById('error-card');
const errorMsg = document.getElementById('error-msg');
const resultsCard = document.getElementById('results-card');
const resultsSummary = document.getElementById('results-summary');
const clipsGrid = document.getElementById('clips-grid');
const startOverBtn = document.getElementById('start-over-btn');
const dlAllBtn = document.getElementById('dl-all-btn');

let lastClips = [];

const STEP_LABELS = {
  download: 'Downloading',
  analyze: 'Analyzing',
  audio: 'Audio',
  detect: 'Detecting',
  clip: 'Clipping',
};

let evtSource = null;

function setLoading(on) {
  submitBtn.disabled = on;
  btnLabel.textContent = on ? 'Working…' : 'Generate Clips';
}

function showError(msg) {
  errorMsg.textContent = msg;
  errorCard.classList.remove('hidden');
  progressCard.classList.add('hidden');
  setLoading(false);
}

function showProgress(pct, step, status, detail) {
  progressCard.classList.remove('hidden');
  errorCard.classList.add('hidden');
  resultsCard.classList.add('hidden');

  progressFill.style.width = `${Math.min(100, pct)}%`;
  if (step) {
    progressStepBadge.textContent = STEP_LABELS[step] || step;
    progressStepBadge.className = 'badge active';
  }
  if (status) progressStatus.textContent = status;
  if (detail !== undefined) progressDetail.textContent = detail;
}

function formatTime(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

function showResults(clips) {
  lastClips = clips;
  progressCard.classList.add('hidden');
  resultsCard.classList.remove('hidden');
  errorCard.classList.add('hidden');

  resultsSummary.textContent = `${clips.length} clip${clips.length !== 1 ? 's' : ''}`;
  clipsGrid.innerHTML = '';

  clips.forEach(clip => {
    const item = document.createElement('div');
    item.className = 'clip-item';

    const videoUrl = `/clips/${clip.path}`;
    const ts = formatTime(clip.timestamp);

    item.innerHTML = `
      <video src="${videoUrl}" controls playsinline muted loop preload="metadata"></video>
      <div class="clip-info">
        <span class="clip-label">Clip ${clip.index}</span>
        <span class="clip-time">@ ${ts}</span>
        <a class="clip-dl" href="${videoUrl}" download="clip_${clip.index}.mp4">Download</a>
      </div>`;

    clipsGrid.appendChild(item);
  });

  setLoading(false);
}

function cleanup() {
  if (evtSource) { evtSource.close(); evtSource = null; }
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  cleanup();

  errorCard.classList.add('hidden');
  resultsCard.classList.add('hidden');
  progressCard.classList.add('hidden');
  clipsGrid.innerHTML = '';

  const payload = {
    url: document.getElementById('url').value.trim(),
    max_clips: parseInt(document.getElementById('max-clips').value, 10),
    clip_duration: parseInt(document.getElementById('clip-duration').value, 10),
    start_time: document.getElementById('start-time').value.trim(),
    end_time: document.getElementById('end-time').value.trim(),
  };

  setLoading(true);
  showProgress(2, 'download', 'Starting…', '');

  let jobId;
  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) { showError(data.error); return; }
    jobId = data.job_id;
  } catch (err) {
    showError('Could not reach the server. Is the app running?');
    return;
  }

  // Track progress via SSE
  let clipsTotal = 1;
  evtSource = new EventSource(`/progress/${jobId}`);

  evtSource.onmessage = e => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }

    if (msg.type === 'ping') return;

    if (msg.type === 'error') {
      cleanup();
      showError(msg.message);
      return;
    }

    if (msg.type === 'download_progress') {
      const pct = Math.min(msg.percent * 0.4, 40); // download = 0-40% of bar
      showProgress(pct, 'download', `Downloading… ${msg.percent.toFixed(0)}%`, msg.message);
      return;
    }

    if (msg.type === 'status') {
      const stepPcts = { download: 5, analyze: 42, audio: 50, detect: 58, clip: 65 };
      const basePct = stepPcts[msg.step] ?? 50;
      showProgress(basePct, msg.step, msg.message, '');
      return;
    }

    if (msg.type === 'info') {
      progressDetail.textContent = msg.message || '';
      return;
    }

    if (msg.type === 'progress') {
      clipsTotal = msg.total || 1;
      const pct = 65 + (msg.current / clipsTotal) * 32;
      showProgress(pct, 'clip', `Creating clip ${msg.current} of ${msg.total}…`, '');
      return;
    }

    if (msg.type === 'done') {
      cleanup();
      showProgress(100, 'clip', 'Done!', '');
      setTimeout(() => showResults(msg.clips), 400);
    }
  };

  evtSource.onerror = () => {
    cleanup();
    showError('Lost connection to the server. The job may still be running — refresh to check.');
  };
});

startOverBtn.addEventListener('click', () => {
  cleanup();
  lastClips = [];
  clipsGrid.innerHTML = '';
  resultsCard.classList.add('hidden');
  errorCard.classList.add('hidden');
  progressCard.classList.add('hidden');
  progressFill.style.width = '0%';
  setLoading(false);
  form.reset();
  document.getElementById('url').focus();
});

dlAllBtn.addEventListener('click', () => {
  lastClips.forEach((clip, i) => {
    const a = document.createElement('a');
    a.href = `/clips/${clip.path}`;
    a.download = `clip_${clip.index}.mp4`;
    document.body.appendChild(a);
    setTimeout(() => { a.click(); document.body.removeChild(a); }, i * 300);
  });
});
