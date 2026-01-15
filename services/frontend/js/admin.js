let authToken = null;
let currentCrawlJobId = null;
let currentIngestJobId = null;
let currentJobLogId = null;
const logStreams = {
  crawl: null,
  ingest: null,
  jobs: null
};

function showTab(name) {
  document.querySelectorAll('.tab-content').forEach((el) => {
    el.style.display = el.id === name ? 'block' : 'none';
  });
  document.querySelectorAll('.tab-button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === name);
  });
}

function setStatus(targetId, message, type = 'success') {
  const el = document.getElementById(targetId);
  if (!el) return;
  el.textContent = message;
  el.classList.remove('success', 'error');
  if (message) {
    el.classList.add(type === 'error' ? 'error' : 'success');
  }
}

function getFilenameFromHeader(response, fallbackName) {
  const disposition = response.headers.get('content-disposition');
  if (!disposition) return fallbackName;
  const utfMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utfMatch) {
    return decodeURIComponent(utfMatch[1]);
  }
  const match = disposition.match(/filename="?([^"]+)"?/i);
  return match ? match[1] : fallbackName;
}

async function downloadResponse(response, fallbackName) {
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = getFilenameFromHeader(response, fallbackName);
  link.click();
  URL.revokeObjectURL(url);
}

function closeStream(key) {
  if (logStreams[key]) {
    logStreams[key].close();
    logStreams[key] = null;
  }
}

async function unlock() {
  const token = document.getElementById('adminToken').value.trim();
  const error = document.getElementById('unlockError');
  error.textContent = '';
  const response = await fetch(`${API_BASE}/api/admin/unlock`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token })
  });
  if (!response.ok) {
    error.textContent = 'Invalid token.';
    return;
  }
  authToken = token;
  document.getElementById('unlockSection').style.display = 'none';
  document.getElementById('adminSection').style.display = 'block';
  loadConfigs();
  loadJobs();
}

async function loadConfigs() {
  const allow = await fetch(`${API_BASE}/api/admin/config/allow_block`).then((r) => r.json());
  document.getElementById('seedUrls').value = (allow.seed_urls || []).join('\n');
  document.getElementById('allowedDomains').value = (allow.allowed_domains || []).join('\n');
  document.getElementById('blockedDomains').value = (allow.blocked_domains || []).join('\n');

  const agents = await fetch(`${API_BASE}/api/admin/config/agents`).then((r) => r.json());
  document.getElementById('intentPrompt').value = agents.agents.intent.system_prompt || '';
  document.getElementById('researchPrompt').value = agents.agents.research.system_prompt || '';
  document.getElementById('synthesisPrompt').value = agents.agents.synthesis.system_prompt || '';
  document.getElementById('validationPrompt').value = agents.agents.validation.system_prompt || '';
}

async function saveCrawlConfig() {
  const payload = {
    seed_urls: document.getElementById('seedUrls').value.split('\n').filter(Boolean),
    allowed_domains: document.getElementById('allowedDomains').value.split('\n').filter(Boolean),
    blocked_domains: document.getElementById('blockedDomains').value.split('\n').filter(Boolean)
  };
  const response = await fetch(`${API_BASE}/api/admin/config/allow_block`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (response.ok) {
    setStatus('saveCrawlStatus', 'Saved');
  } else {
    setStatus('saveCrawlStatus', 'Error saving config', 'error');
  }
}

async function savePrompts() {
  const payload = {
    agents: {
      intent: { system_prompt: document.getElementById('intentPrompt').value },
      research: { system_prompt: document.getElementById('researchPrompt').value },
      synthesis: { system_prompt: document.getElementById('synthesisPrompt').value },
      validation: { system_prompt: document.getElementById('validationPrompt').value }
    }
  };
  const response = await fetch(`${API_BASE}/api/admin/config/agents`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (response.ok) {
    setStatus('savePromptsStatus', 'Saved');
  } else {
    setStatus('savePromptsStatus', 'Error saving prompts', 'error');
  }
}

function streamLog(jobId, targetId, streamKey) {
  const logArea = document.getElementById(targetId);
  logArea.textContent = '';
  closeStream(streamKey);
  const eventSource = new EventSource(`${API_BASE}/api/admin/jobs/${jobId}/log`);
  eventSource.onmessage = (event) => {
    logArea.textContent += `${event.data}\n`;
    logArea.scrollTop = logArea.scrollHeight;
  };
  logStreams[streamKey] = eventSource;
}

async function triggerJob(type) {
  const response = await fetch(`${API_BASE}/api/admin/${type}`, { method: 'POST' });
  const data = await response.json();
  if (type === 'crawl') {
    currentCrawlJobId = data.job_id;
    streamLog(currentCrawlJobId, 'crawlLog', 'crawl');
  } else {
    currentIngestJobId = data.job_id;
    streamLog(currentIngestJobId, 'ingestLog', 'ingest');
  }
  loadJobs();
}

async function loadJobs() {
  const jobs = await fetch(`${API_BASE}/api/admin/jobs`).then((r) => r.json());
  const table = document.getElementById('jobTable');
  table.innerHTML = `
    <tr>
      <th>Job ID</th>
      <th>Type</th>
      <th>Status</th>
      <th>Started At</th>
      <th>Ended At</th>
      <th>Actions</th>
    </tr>
  `;
  jobs.forEach((job) => {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${job.job_id}</td>
      <td>${job.job_type}</td>
      <td>${job.status}</td>
      <td>${job.started_at}</td>
      <td>${job.ended_at || '-'}</td>
      <td class="actions">
        <button class="btn" data-action="view" data-id="${job.job_id}">View Log</button>
        <button class="btn" data-action="export" data-id="${job.job_id}">Export</button>
        <button class="btn" data-action="delete" data-id="${job.job_id}">Delete</button>
      </td>
    `;
    table.appendChild(row);
  });
}

async function exportJobLog(jobId, fallbackName) {
  const response = await fetch(`${API_BASE}/api/admin/jobs/${jobId}/log/export`);
  if (!response.ok) return;
  await downloadResponse(response, fallbackName);
}

async function deleteJob(jobId, statusTarget) {
  const response = await fetch(`${API_BASE}/api/admin/jobs/${jobId}`, { method: 'DELETE' });
  if (!response.ok) {
    setStatus(statusTarget, 'Error deleting log', 'error');
    return false;
  }
  return true;
}

async function exportCurrentLog(jobId, statusTarget) {
  if (!jobId) {
    setStatus(statusTarget, 'No job selected', 'error');
    return;
  }
  await exportJobLog(jobId, `job_${jobId}.log`);
  setStatus(statusTarget, 'Exported');
}

async function deleteCurrentLog(jobId, statusTarget, streamKey, logTargetId) {
  if (!jobId) {
    setStatus(statusTarget, 'No job selected', 'error');
    return;
  }
  const deleted = await deleteJob(jobId, statusTarget);
  if (deleted) {
    closeStream(streamKey);
    document.getElementById(logTargetId).textContent = '';
    setStatus(statusTarget, 'Deleted');
  }
  loadJobs();
}

async function clearVectors() {
  if (!confirm('Clear all vectors and ingest metadata? This cannot be undone.')) {
    return;
  }
  const response = await fetch(`${API_BASE}/api/admin/clear_vectors`, { method: 'POST' });
  if (response.ok) {
    setStatus('clearVectorsStatus', 'Cleared');
  } else {
    setStatus('clearVectorsStatus', 'Error clearing vectors', 'error');
  }
}

document.getElementById('unlockButton').addEventListener('click', unlock);

document.querySelectorAll('.tab-button').forEach((btn) => {
  btn.addEventListener('click', () => showTab(btn.dataset.tab));
});

document.getElementById('saveCrawlConfig').addEventListener('click', saveCrawlConfig);

document.getElementById('triggerCrawl').addEventListener('click', () => triggerJob('crawl'));

document.getElementById('triggerIngest').addEventListener('click', () => triggerJob('ingest'));

document.getElementById('savePrompts').addEventListener('click', savePrompts);

document.getElementById('exportCrawlLog').addEventListener('click', () => {
  exportCurrentLog(currentCrawlJobId, 'crawlLogStatus');
});

document.getElementById('deleteCrawlLog').addEventListener('click', () => {
  deleteCurrentLog(currentCrawlJobId, 'crawlLogStatus', 'crawl', 'crawlLog');
  currentCrawlJobId = null;
});

document.getElementById('exportIngestLog').addEventListener('click', () => {
  exportCurrentLog(currentIngestJobId, 'ingestLogStatus');
});

document.getElementById('deleteIngestLog').addEventListener('click', () => {
  deleteCurrentLog(currentIngestJobId, 'ingestLogStatus', 'ingest', 'ingestLog');
  currentIngestJobId = null;
});

document.getElementById('clearVectors').addEventListener('click', clearVectors);

document.getElementById('exportJobLog').addEventListener('click', () => {
  exportCurrentLog(currentJobLogId, 'jobLogStatus');
});

document.getElementById('deleteJobLog').addEventListener('click', () => {
  deleteCurrentLog(currentJobLogId, 'jobLogStatus', 'jobs', 'jobLog');
  currentJobLogId = null;
});

document.getElementById('jobTable').addEventListener('click', async (event) => {
  const target = event.target;
  if (!target.dataset || !target.dataset.action) {
    return;
  }
  const jobId = target.dataset.id;
  if (target.dataset.action === 'view') {
    currentJobLogId = jobId;
    streamLog(jobId, 'jobLog', 'jobs');
    setStatus('jobLogStatus', '');
  }
  if (target.dataset.action === 'export') {
    await exportJobLog(jobId, `job_${jobId}.log`);
  }
  if (target.dataset.action === 'delete') {
    const deleted = await deleteJob(jobId, 'jobLogStatus');
    if (deleted) {
      if (currentJobLogId === jobId) {
        currentJobLogId = null;
        closeStream('jobs');
        document.getElementById('jobLog').textContent = '';
      }
      setStatus('jobLogStatus', 'Deleted');
      loadJobs();
    }
  }
});
