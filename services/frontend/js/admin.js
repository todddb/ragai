let authToken = null;
let currentJobId = null;

function showTab(name) {
  document.querySelectorAll('.tab-content').forEach((el) => {
    el.style.display = el.id === name ? 'block' : 'none';
  });
  document.querySelectorAll('.tab-button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === name);
  });
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
  await fetch(`${API_BASE}/api/admin/config/allow_block`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
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
  await fetch(`${API_BASE}/api/admin/config/agents`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

function streamLog(jobId, targetId) {
  const logArea = document.getElementById(targetId);
  logArea.textContent = '';
  const eventSource = new EventSource(`${API_BASE}/api/admin/jobs/${jobId}/log`);
  eventSource.onmessage = (event) => {
    logArea.textContent += `${event.data}\n`;
    logArea.scrollTop = logArea.scrollHeight;
  };
  return eventSource;
}

async function triggerJob(type) {
  const response = await fetch(`${API_BASE}/api/admin/${type}`, { method: 'POST' });
  const data = await response.json();
  currentJobId = data.job_id;
  if (type === 'crawl') {
    streamLog(currentJobId, 'crawlLog');
  } else {
    streamLog(currentJobId, 'ingestLog');
  }
}

async function loadJobs() {
  const jobs = await fetch(`${API_BASE}/api/admin/jobs`).then((r) => r.json());
  const table = document.getElementById('jobTable');
  table.innerHTML = '<tr><th>Job ID</th><th>Type</th><th>Status</th><th>Started</th><th>Ended</th></tr>';
  jobs.forEach((job) => {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${job.job_id}</td>
      <td>${job.job_type}</td>
      <td>${job.status}</td>
      <td>${job.started_at}</td>
      <td>${job.ended_at || '-'}</td>
    `;
    table.appendChild(row);
  });
}

document.getElementById('unlockButton').addEventListener('click', unlock);

document.querySelectorAll('.tab-button').forEach((btn) => {
  btn.addEventListener('click', () => showTab(btn.dataset.tab));
});

document.getElementById('saveCrawlConfig').addEventListener('click', saveCrawlConfig);

document.getElementById('triggerCrawl').addEventListener('click', () => triggerJob('crawl'));

document.getElementById('triggerIngest').addEventListener('click', () => triggerJob('ingest'));

document.getElementById('savePrompts').addEventListener('click', savePrompts);
