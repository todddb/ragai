let currentCrawlJobId = null;
let currentIngestJobId = null;
let currentJobLogId = null;
let cachedCrawlerConfig = {};
const crawlState = {
  seeds: [],
  blocked: [],
  allowRules: [],
  allowHttp: false
};
let allowRecommendations = [];
let recommendationsExpanded = false;
const editState = {
  seed: null,
  blocked: null,
  allow: null
};
const logStreams = {
  crawl: null,
  ingest: null,
  jobs: null
};
const allowTypeKeys = ['web', 'pdf', 'docx', 'xlsx', 'pptx'];

function getTypeDefaults() {
  return {
    web: true,
    pdf: false,
    docx: false,
    xlsx: false,
    pptx: false
  };
}

function normalizeTypes(types) {
  const defaults = getTypeDefaults();
  if (!types) {
    return { ...defaults };
  }
  return {
    web: Boolean(types.web ?? defaults.web),
    pdf: Boolean(types.pdf ?? defaults.pdf),
    docx: Boolean(types.docx ?? defaults.docx),
    xlsx: Boolean(types.xlsx ?? defaults.xlsx),
    pptx: Boolean(types.pptx ?? defaults.pptx)
  };
}

function normalizeAllowRule(rule) {
  if (!rule) {
    return {
      pattern: '',
      match: 'prefix',
      types: getTypeDefaults(),
      playwright: false
    };
  }
  if (typeof rule === 'string') {
    return {
      pattern: rule,
      match: 'prefix',
      types: getTypeDefaults(),
      playwright: false
    };
  }
  return {
    pattern: rule.pattern || '',
    match: rule.match || 'prefix',
    types: normalizeTypes(rule.types),
    playwright: Boolean(rule.playwright)
  };
}

function normalizeUrlInput(input) {
  let url = input.trim();
  if (!url) return '';

  // If no scheme, add https://
  if (!url.match(/^https?:\/\//i)) {
    url = 'https://' + url;
  }

  // If "Allow HTTP" is unchecked, convert http:// to https://
  if (!crawlState.allowHttp && url.match(/^http:\/\//i)) {
    url = url.replace(/^http:\/\//i, 'https://');
  }

  // Normalize trailing slash for host-only patterns
  try {
    const parsed = new URL(url);
    if (!parsed.pathname || parsed.pathname === '') {
      url = url + '/';
    }
  } catch (e) {
    // If URL parsing fails, just return what we have
  }

  return url;
}

function normalizeDomainInput(input) {
  let domain = input.trim();
  if (!domain) return '';

  // Strip scheme if present
  domain = domain.replace(/^https?:\/\//i, '');

  // Strip path if present (keep only domain)
  const slashIndex = domain.indexOf('/');
  if (slashIndex !== -1) {
    domain = domain.substring(0, slashIndex);
  }

  return domain;
}

function renderSeedList() {
  const list = document.getElementById('seedList');
  if (!list) return;
  list.innerHTML = '';
  crawlState.seeds.forEach((url, index) => {
    const row = document.createElement('div');
    row.className = 'list-row';
    if (editState.seed === index) {
      const input = document.createElement('input');
      input.className = 'list-input';
      input.value = url;
      const actions = document.createElement('div');
      actions.className = 'list-actions';
      const save = document.createElement('button');
      save.className = 'btn btn-small';
      save.textContent = 'Save';
      const cancel = document.createElement('button');
      cancel.className = 'btn btn-small';
      cancel.textContent = 'Cancel';
      save.addEventListener('click', () => {
        const value = normalizeUrlInput(input.value);
        if (value) {
          crawlState.seeds[index] = value;
        }
        editState.seed = null;
        renderSeedList();
      });
      cancel.addEventListener('click', () => {
        editState.seed = null;
        renderSeedList();
      });
      actions.append(save, cancel);
      row.append(input, actions);
    } else {
      const text = document.createElement('div');
      text.className = 'list-text';
      text.textContent = url;
      const actions = document.createElement('div');
      actions.className = 'list-actions';
      const edit = document.createElement('button');
      edit.className = 'icon-btn';
      edit.setAttribute('aria-label', 'Edit seed URL');
      edit.textContent = '✏️';
      const remove = document.createElement('button');
      remove.className = 'icon-btn';
      remove.setAttribute('aria-label', 'Delete seed URL');
      remove.textContent = '🗑️';
      edit.addEventListener('click', () => {
        editState.seed = index;
        renderSeedList();
      });
      remove.addEventListener('click', () => {
        crawlState.seeds.splice(index, 1);
        renderSeedList();
      });
      actions.append(edit, remove);
      row.append(text, actions);
    }
    list.appendChild(row);
  });
}

function renderBlockedList() {
  const list = document.getElementById('blockedList');
  if (!list) return;
  list.innerHTML = '';
  crawlState.blocked.forEach((domain, index) => {
    const row = document.createElement('div');
    row.className = 'list-row';
    if (editState.blocked === index) {
      const input = document.createElement('input');
      input.className = 'list-input';
      input.value = domain;
      const actions = document.createElement('div');
      actions.className = 'list-actions';
      const save = document.createElement('button');
      save.className = 'btn btn-small';
      save.textContent = 'Save';
      const cancel = document.createElement('button');
      cancel.className = 'btn btn-small';
      cancel.textContent = 'Cancel';
      save.addEventListener('click', () => {
        const value = normalizeDomainInput(input.value);
        if (value) {
          crawlState.blocked[index] = value;
        }
        editState.blocked = null;
        renderBlockedList();
      });
      cancel.addEventListener('click', () => {
        editState.blocked = null;
        renderBlockedList();
      });
      actions.append(save, cancel);
      row.append(input, actions);
    } else {
      const text = document.createElement('div');
      text.className = 'list-text';
      text.textContent = domain;
      const actions = document.createElement('div');
      actions.className = 'list-actions';
      const edit = document.createElement('button');
      edit.className = 'icon-btn';
      edit.setAttribute('aria-label', 'Edit blocked domain');
      edit.textContent = '✏️';
      const remove = document.createElement('button');
      remove.className = 'icon-btn';
      remove.setAttribute('aria-label', 'Delete blocked domain');
      remove.textContent = '🗑️';
      edit.addEventListener('click', () => {
        editState.blocked = index;
        renderBlockedList();
      });
      remove.addEventListener('click', () => {
        crawlState.blocked.splice(index, 1);
        renderBlockedList();
      });
      actions.append(edit, remove);
      row.append(text, actions);
    }
    list.appendChild(row);
  });
}

function renderAllowTable() {
  const table = document.getElementById('allowTable');
  if (!table) return;
  table.innerHTML = '';
  const header = document.createElement('div');
  header.className = 'allowed-row allowed-header';
  header.innerHTML = `
    <div>URL</div>
    <div>Match</div>
    <div>Web</div>
    <div>PDF</div>
    <div>DOCX</div>
    <div>XLSX</div>
    <div>PPTX</div>
    <div></div>
    <div></div>
  `;
  table.appendChild(header);
  crawlState.allowRules.forEach((rule, index) => {
    const row = document.createElement('div');
    row.className = 'allowed-row';
    const urlCell = document.createElement('div');
    if (editState.allow === index) {
      const input = document.createElement('input');
      input.className = 'list-input';
      input.value = rule.pattern;
      urlCell.appendChild(input);
      urlCell.dataset.input = 'true';
    } else {
      urlCell.className = 'allowed-url';
      urlCell.textContent = rule.pattern;
    }

    let matchCell;
    if (editState.allow === index) {
      const select = document.createElement('select');
      select.innerHTML = `
        <option value="prefix">prefix</option>
        <option value="exact">exact</option>
      `;
      select.value = rule.match || 'prefix';
      matchCell = select;
    } else {
      const badge = document.createElement('span');
      badge.className = 'match-badge';
      badge.textContent = rule.match || 'prefix';
      matchCell = badge;
    }

    const checkboxes = allowTypeKeys.map((key) => {
      const wrapper = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = Boolean(rule.types?.[key]);
      checkbox.addEventListener('change', () => {
        rule.types[key] = checkbox.checked;
      });
      wrapper.appendChild(checkbox);
      return wrapper;
    });

    const edit = document.createElement('button');
    edit.className = 'icon-btn';
    edit.setAttribute('aria-label', 'Edit allowed URL');
    edit.textContent = editState.allow === index ? '💾' : '✏️';
    const remove = document.createElement('button');
    remove.className = 'icon-btn';
    remove.setAttribute('aria-label', 'Delete allowed URL');
    remove.textContent = editState.allow === index ? '✖️' : '🗑️';

    edit.addEventListener('click', () => {
      if (editState.allow === index) {
        const input = urlCell.querySelector('input');
        const value = normalizeUrlInput(input?.value || '');
        if (value) {
          rule.pattern = value;
        }
        if (matchCell instanceof HTMLSelectElement) {
          rule.match = matchCell.value;
        }
        editState.allow = null;
      } else {
        editState.allow = index;
      }
      renderAllowTable();
      renderRecommendations();
    });

    remove.addEventListener('click', () => {
      if (editState.allow === index) {
        editState.allow = null;
        renderAllowTable();
      } else {
        crawlState.allowRules.splice(index, 1);
        renderAllowTable();
        renderRecommendations();
      }
    });

    row.append(urlCell, matchCell, ...checkboxes, edit, remove);
    table.appendChild(row);
  });
}

function isUrlAlreadyAllowed(url) {
  return crawlState.allowRules.some((rule) => {
    if (rule.match === 'exact') {
      return rule.pattern === url;
    } else {
      return url.startsWith(rule.pattern);
    }
  });
}

function renderRecommendations() {
  const list = document.getElementById('allowRecommendations');
  const expandBtn = document.getElementById('expandRecommendations');
  if (!list) return;
  list.innerHTML = '';

  const filteredRecommendations = allowRecommendations.filter((rec) => {
    return !isUrlAlreadyAllowed(rec.suggested_url);
  });

  const displayLimit = recommendationsExpanded ? filteredRecommendations.length : 3;
  const displayedRecommendations = filteredRecommendations.slice(0, displayLimit);

  displayedRecommendations.forEach((rec) => {
    const row = document.createElement('div');
    row.className = 'rec-row';
    const meta = document.createElement('div');
    meta.className = 'rec-meta';
    const text = document.createElement('div');
    text.textContent = rec.suggested_url;
    const badge = document.createElement('span');
    badge.className = 'count-badge';
    badge.textContent = `seen ${rec.count}x`;
    meta.append(text, badge);
    const addButton = document.createElement('button');
    addButton.className = 'btn btn-small';
    addButton.textContent = '+ Add';
    addButton.addEventListener('click', () => {
      const types = normalizeTypes(rec.seen_types);
      if (!types.web && !types.pdf && !types.docx && !types.xlsx && !types.pptx) {
        types.web = true;
      }
      crawlState.allowRules.push({
        pattern: rec.suggested_url,
        match: 'prefix',
        types,
        playwright: false
      });
      renderAllowTable();
      renderRecommendations();
    });
    row.append(meta, addButton);
    list.appendChild(row);
  });

  if (expandBtn) {
    if (filteredRecommendations.length > 3) {
      expandBtn.style.display = 'block';
      expandBtn.textContent = recommendationsExpanded ? 'Show Less' : `Show ${filteredRecommendations.length - 3} More`;
    } else {
      expandBtn.style.display = 'none';
    }
  }
}

function renderCrawlUi() {
  renderSeedList();
  renderBlockedList();
  renderAllowTable();
  renderRecommendations();
}

function extractAllowedDomains(rules) {
  const domains = new Set();
  rules.forEach((rule) => {
    const pattern = rule.pattern || '';
    let host = '';
    try {
      host = new URL(pattern).hostname;
    } catch (error) {
      const stripped = pattern.replace(/^https?:\/\//, '');
      host = stripped.split('/')[0];
    }
    if (host) {
      domains.add(host);
    }
  });
  return Array.from(domains);
}

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
  if (!token) {
    error.textContent = 'Please enter a token to continue.';
    return;
  }
  const response = await fetch(`${API_BASE}/api/admin/unlock`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token })
  });
  if (!response.ok) {
    error.textContent = 'Invalid token.';
    return;
  }
  document.getElementById('adminToken').value = '';
  if (window.onAdminUnlocked) {
    window.onAdminUnlocked();
  } else {
    document.getElementById('unlockSection').style.display = 'none';
    document.getElementById('adminSection').style.display = 'block';
    loadConfigs();
    loadJobs();
  }
}

async function loadConfigs() {
  const allow = await fetch(`${API_BASE}/api/admin/config/allow_block`).then((r) => r.json());
  crawlState.seeds = allow.seed_urls || [];
  crawlState.blocked = allow.blocked_domains || [];
  crawlState.allowHttp = Boolean(allow.allow_http);
  if (Array.isArray(allow.allow_rules) && allow.allow_rules.length > 0) {
    crawlState.allowRules = allow.allow_rules.map((rule) => normalizeAllowRule(rule));
  } else {
    const allowedDomains = allow.allowed_domains || [];
    crawlState.allowRules = allowedDomains.map((domain) =>
      normalizeAllowRule({
        pattern: `https://${domain}/`,
        match: 'prefix',
        types: { web: true }
      })
    );
  }
  const allowHttpCheckbox = document.getElementById('allowHttpCheckbox');
  if (allowHttpCheckbox) {
    allowHttpCheckbox.checked = crawlState.allowHttp;
  }
  editState.seed = null;
  editState.blocked = null;
  editState.allow = null;
  renderCrawlUi();
  await loadRecommendations();

  const agents = await fetch(`${API_BASE}/api/admin/config/agents`).then((r) => r.json());
  document.getElementById('intentPrompt').value = agents.agents.intent.system_prompt || '';
  document.getElementById('researchPrompt').value = agents.agents.research.system_prompt || '';
  document.getElementById('synthesisPrompt').value = agents.agents.synthesis.system_prompt || '';
  document.getElementById('validationPrompt').value = agents.agents.validation.system_prompt || '';

  const crawler = await fetch(`${API_BASE}/api/admin/config/crawler`).then((r) => r.json());
  cachedCrawlerConfig = crawler || {};
  const playwright = cachedCrawlerConfig.playwright || {};
  document.getElementById('playwrightEnabled').checked = Boolean(playwright.enabled);
  document.getElementById('playwrightStorageStatePath').value = playwright.storage_state_path || '';
  document.getElementById('playwrightUseDomains').value = (playwright.use_for_domains || []).join('\n');
}

async function loadRecommendations() {
  try {
    const response = await fetch(`${API_BASE}/api/admin/candidates/recommendations`);
    if (!response.ok) {
      allowRecommendations = [];
      renderRecommendations();
      return;
    }
    const data = await response.json();
    allowRecommendations = data.items || [];
    renderRecommendations();
  } catch (error) {
    allowRecommendations = [];
    renderRecommendations();
  }
}

async function saveCrawlConfig() {
  const allowHttpCheckbox = document.getElementById('allowHttpCheckbox');
  if (allowHttpCheckbox) {
    crawlState.allowHttp = allowHttpCheckbox.checked;
  }
  const allowPayload = {
    seed_urls: crawlState.seeds,
    blocked_domains: crawlState.blocked,
    allow_http: crawlState.allowHttp,
    allow_rules: crawlState.allowRules.map((rule) => ({
      pattern: rule.pattern,
      match: rule.match || 'prefix',
      types: normalizeTypes(rule.types),
      playwright: Boolean(rule.playwright)
    })),
    allowed_domains: extractAllowedDomains(crawlState.allowRules)
  };
  const existingPlaywright = cachedCrawlerConfig.playwright || {};
  const playwrightPayload = {
    ...existingPlaywright,
    enabled: document.getElementById('playwrightEnabled').checked,
    storage_state_path: document.getElementById('playwrightStorageStatePath').value.trim(),
    use_for_domains: document.getElementById('playwrightUseDomains').value.split('\n').filter(Boolean)
  };
  if (playwrightPayload.headless === undefined) {
    playwrightPayload.headless = true;
  }
  if (playwrightPayload.navigation_timeout_ms === undefined) {
    playwrightPayload.navigation_timeout_ms = 60000;
  }
  const crawlerPayload = {
    ...cachedCrawlerConfig,
    playwright: playwrightPayload
  };
  const [allowResponse, crawlerResponse] = await Promise.all([
    fetch(`${API_BASE}/api/admin/config/allow_block`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(allowPayload)
    }),
    fetch(`${API_BASE}/api/admin/config/crawler`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(crawlerPayload)
    })
  ]);
  if (allowResponse.ok && crawlerResponse.ok) {
    cachedCrawlerConfig = crawlerPayload;
    setStatus('saveCrawlStatus', 'Saved');
  } else {
    setStatus('saveCrawlStatus', 'Error saving config', 'error');
  }
}

function addSeedFromInput() {
  const input = document.getElementById('seedAddInput');
  if (!input) return;
  const value = normalizeUrlInput(input.value);
  if (!value) return;
  crawlState.seeds.push(value);
  input.value = '';
  renderSeedList();
}

function addBlockedFromInput() {
  const input = document.getElementById('blockedAddInput');
  if (!input) return;
  const value = normalizeDomainInput(input.value);
  if (!value) return;
  crawlState.blocked.push(value);
  input.value = '';
  renderBlockedList();
}

function addAllowFromInput() {
  const input = document.getElementById('allowAddInput');
  const match = document.getElementById('allowAddMatch');
  if (!input || !match) return;
  const value = normalizeUrlInput(input.value);
  if (!value) return;
  const types = {
    web: document.getElementById('allowAddWeb')?.checked ?? true,
    pdf: document.getElementById('allowAddPdf')?.checked ?? false,
    docx: document.getElementById('allowAddDocx')?.checked ?? false,
    xlsx: document.getElementById('allowAddXlsx')?.checked ?? false,
    pptx: document.getElementById('allowAddPptx')?.checked ?? false
  };
  if (!types.web && !types.pdf && !types.docx && !types.xlsx && !types.pptx) {
    types.web = true;
  }
  crawlState.allowRules.push({
    pattern: value,
    match: match.value,
    types,
    playwright: false
  });
  input.value = '';
  renderAllowTable();
  renderRecommendations();
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

    // Check if crawl is complete by looking for the completion message
    if (streamKey === 'crawl' && event.data.includes('Crawl job complete')) {
      setTimeout(() => loadCrawlSummary(jobId), 1000);
    }
  };
  logStreams[streamKey] = eventSource;
}

async function loadCrawlSummary(jobId) {
  const summaryPanel = document.getElementById('crawlSummary');
  const summaryContent = document.getElementById('crawlSummaryContent');
  if (!summaryPanel || !summaryContent) return;

  try {
    const response = await fetch(`${API_BASE}/api/admin/jobs/${jobId}/summary`);
    if (!response.ok) {
      summaryPanel.style.display = 'none';
      return;
    }
    const summary = await response.json();

    const totalSkipped = (summary.skipped_already_processed || 0) +
                        (summary.skipped_depth || 0) +
                        (summary.skipped_not_allowed || 0);

    let html = `
      <div class="info-grid">
        <div>
          <div class="field-label">Total Candidates</div>
          <div class="info-value">${summary.total_candidates || 0}</div>
        </div>
        <div>
          <div class="field-label">Crawled</div>
          <div class="info-value">${summary.crawled || 0}</div>
        </div>
        <div>
          <div class="field-label">Successfully Captured</div>
          <div class="info-value">${summary.captured || 0}</div>
        </div>
        <div>
          <div class="field-label">Errors</div>
          <div class="info-value">${summary.errors || 0}</div>
        </div>
        <div>
          <div class="field-label">Skipped</div>
          <div class="info-value">${totalSkipped}</div>
        </div>
      </div>
    `;

    if (summary.error_details && summary.error_details.length > 0) {
      html += `
        <div style="margin-top: 12px;">
          <div class="field-label">Recent Errors:</div>
          <div style="font-size: 12px; color: #666; max-height: 100px; overflow-y: auto;">
            ${summary.error_details.map(err => `<div>• ${err}</div>`).join('')}
          </div>
        </div>
      `;
    }

    summaryContent.innerHTML = html;
    summaryPanel.style.display = 'block';
  } catch (error) {
    summaryPanel.style.display = 'none';
  }
}

async function triggerJob(type) {
  const response = await fetch(`${API_BASE}/api/admin/${type}`, { method: 'POST' });
  const data = await response.json();
  if (type === 'crawl') {
    currentCrawlJobId = data.job_id;
    const summaryPanel = document.getElementById('crawlSummary');
    if (summaryPanel) {
      summaryPanel.style.display = 'none';
    }
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
        <button class="btn" data-action="view" data-id="${job.job_id}" data-type="${job.job_type}">View Log</button>
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
    if (streamKey === 'crawl') {
      const summaryPanel = document.getElementById('crawlSummary');
      if (summaryPanel) {
        summaryPanel.style.display = 'none';
      }
    }
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
document.getElementById('adminToken').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    unlock();
  }
});

document.querySelectorAll('.tab-button').forEach((btn) => {
  btn.addEventListener('click', () => showTab(btn.dataset.tab));
});

document.getElementById('saveCrawlBtn')?.addEventListener('click', saveCrawlConfig);
document.getElementById('seedAddBtn')?.addEventListener('click', addSeedFromInput);
document.getElementById('blockedAddBtn')?.addEventListener('click', addBlockedFromInput);
document.getElementById('allowAddBtn')?.addEventListener('click', addAllowFromInput);

document.getElementById('expandRecommendations')?.addEventListener('click', () => {
  recommendationsExpanded = !recommendationsExpanded;
  renderRecommendations();
});

document.getElementById('seedAddInput')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    addSeedFromInput();
  }
});

document.getElementById('blockedAddInput')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    addBlockedFromInput();
  }
});

document.getElementById('allowAddInput')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    addAllowFromInput();
  }
});

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

document.getElementById('purgeCandidates')?.addEventListener('click', async () => {
  if (!confirm('Purge all candidates and processed history? This cannot be undone.')) {
    return;
  }
  const statusTarget = 'purgeCandidatesStatus';
  try {
    const response = await fetch(`${API_BASE}/api/admin/candidates/purge`, { method: 'POST' });
    if (response.ok) {
      setStatus(statusTarget, 'Purged successfully');
    } else {
      setStatus(statusTarget, 'Error purging candidates', 'error');
    }
  } catch (error) {
    setStatus(statusTarget, 'Error purging candidates', 'error');
  }
});

document.getElementById('jobTable').addEventListener('click', async (event) => {
  const target = event.target;
  if (!target.dataset || !target.dataset.action) {
    return;
  }
  const jobId = target.dataset.id;
  const jobType = target.dataset.type;
  if (target.dataset.action === 'view') {
    currentJobLogId = jobId;
    streamLog(jobId, 'jobLog', 'jobs');
    setStatus('jobLogStatus', '');
    // Load summary for crawl jobs when viewing from history
    if (jobType === 'crawl') {
      setTimeout(() => loadCrawlSummary(jobId), 500);
    }
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

window.loadAdminData = () => {
  loadConfigs();
  loadJobs();
};

window.resetAdminSession = () => {
  closeStream('crawl');
  closeStream('ingest');
  closeStream('jobs');
  currentCrawlJobId = null;
  currentIngestJobId = null;
  currentJobLogId = null;
  setStatus('saveCrawlStatus', '');
  setStatus('crawlLogStatus', '');
  setStatus('ingestLogStatus', '');
  setStatus('clearVectorsStatus', '');
  setStatus('savePromptsStatus', '');
  setStatus('jobLogStatus', '');
  const crawlLog = document.getElementById('crawlLog');
  const ingestLog = document.getElementById('ingestLog');
  const jobLog = document.getElementById('jobLog');
  const summaryPanel = document.getElementById('crawlSummary');
  if (crawlLog) crawlLog.textContent = '';
  if (ingestLog) ingestLog.textContent = '';
  if (jobLog) jobLog.textContent = '';
  if (summaryPanel) summaryPanel.style.display = 'none';
};
