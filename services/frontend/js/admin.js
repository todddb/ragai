let currentCrawlJobId = null;
let currentIngestJobId = null;
let currentJobLogId = null;
let cachedCrawlerConfig = {};
let cachedIngestConfig = {};
let ingestWorkerPoller = null;
let authProfiles = [];
let authStatusCache = { results: {}, updatedAt: null };
let allowedUrlAuthStatus = { byRuleId: {}, byPattern: {}, updatedAt: null, playwrightAvailable: true };
const crawlState = {
  seeds: [],
  blocked: [],
  allowRules: []
};
let allowRecommendations = [];
let recommendationsExpanded = false;
const editState = {
  seed: null,
  blocked: null,
  allow: null,
  authProfile: null
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
      id: null,
      pattern: '',
      match: 'prefix',
      types: getTypeDefaults(),
      allow_http: false,
      auth_profile: null
    };
  }
  if (typeof rule === 'string') {
    return {
      id: null,
      pattern: rule,
      match: 'prefix',
      types: getTypeDefaults(),
      allow_http: false,
      auth_profile: null
    };
  }
  return {
    id: rule.id || null,
    pattern: rule.pattern || '',
    match: rule.match || 'prefix',
    types: normalizeTypes(rule.types),
    allow_http: Boolean(rule.allow_http),
    auth_profile: rule.auth_profile || rule.authProfile || null
  };
}

function normalizeSeed(seed) {
  if (!seed) {
    return {
      url: '',
      allow_http: false
    };
  }
  if (typeof seed === 'string') {
    return {
      url: seed,
      allow_http: false
    };
  }
  return {
    url: seed.url || '',
    allow_http: Boolean(seed.allow_http)
  };
}

function normalizeUrlRow(input, allowHttp = false) {
  let url = input.trim();
  if (!url) return '';

  // Reject non-http(s) schemes
  const schemeMatch = url.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):\/\//);
  if (schemeMatch && !schemeMatch[1].match(/^https?$/i)) {
    throw new Error(`Invalid scheme "${schemeMatch[1]}". Only http:// and https:// are allowed.`);
  }

  // If no scheme, add based on allow_http flag
  if (!url.match(/^https?:\/\//i)) {
    url = (allowHttp ? 'http://' : 'https://') + url;
  }

  // If scheme is http:// and allow_http is false, convert to https://
  if (!allowHttp && url.match(/^http:\/\//i)) {
    url = url.replace(/^http:\/\//i, 'https://');
  }

  // Strip fragment
  const hashIndex = url.indexOf('#');
  if (hashIndex !== -1) {
    url = url.substring(0, hashIndex);
  }

  // Normalize trailing slash for host-only patterns
  try {
    const parsed = new URL(url);
    if (!parsed.pathname || parsed.pathname === '' || parsed.pathname === '/') {
      // Ensure trailing slash for root paths
      if (!url.endsWith('/')) {
        url = url + '/';
      }
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
  const table = document.getElementById('seedList');
  if (!table) return;
  table.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'seed-row seed-header';
  header.innerHTML = `
    <div>URL</div>
    <div>Allow HTTP</div>
    <div></div>
    <div></div>
  `;
  table.appendChild(header);

  // Sort seeds alphabetically by URL
  const sortedSeeds = [...crawlState.seeds].sort((a, b) => {
    const urlA = (normalizeSeed(a).url || '').toLowerCase();
    const urlB = (normalizeSeed(b).url || '').toLowerCase();
    return urlA.localeCompare(urlB);
  });

  sortedSeeds.forEach((seedObj, displayIndex) => {
    // Find the original index for edit state tracking
    const index = crawlState.seeds.findIndex(s => normalizeSeed(s).url === normalizeSeed(seedObj).url);
    const seed = normalizeSeed(seedObj);
    const row = document.createElement('div');
    row.className = 'seed-row';

    const urlCell = document.createElement('div');
    if (editState.seed === index) {
      const input = document.createElement('input');
      input.className = 'list-input';
      input.value = seed.url;
      urlCell.appendChild(input);
      urlCell.dataset.input = 'true';
    } else {
      urlCell.className = 'seed-url';
      urlCell.textContent = seed.url;
    }

    const allowHttpCell = document.createElement('label');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = Boolean(seed.allow_http);
    checkbox.addEventListener('change', () => {
      seed.allow_http = checkbox.checked;
      crawlState.seeds[index] = seed;
      // Re-normalize URL when toggling allow_http
      try {
        const normalized = normalizeUrlRow(seed.url, seed.allow_http);
        seed.url = normalized;
        crawlState.seeds[index] = seed;
        renderSeedList();
      } catch (e) {
        // Keep the current value if normalization fails
      }
    });
    allowHttpCell.appendChild(checkbox);

    const edit = document.createElement('button');
    edit.className = 'icon-btn';
    edit.setAttribute('aria-label', 'Edit seed URL');
    edit.textContent = editState.seed === index ? '💾' : '✏️';

    const remove = document.createElement('button');
    remove.className = 'icon-btn';
    remove.setAttribute('aria-label', 'Delete seed URL');
    remove.textContent = editState.seed === index ? '✖️' : '🗑️';

    edit.addEventListener('click', () => {
      if (editState.seed === index) {
        const input = urlCell.querySelector('input');
        try {
          const value = normalizeUrlRow(input?.value || '', seed.allow_http);
          if (value) {
            seed.url = value;
            crawlState.seeds[index] = seed;
          }
        } catch (e) {
          alert(e.message);
          return;
        }
        editState.seed = null;
      } else {
        editState.seed = index;
      }
      renderSeedList();
    });

    remove.addEventListener('click', () => {
      if (editState.seed === index) {
        editState.seed = null;
        renderSeedList();
      } else {
        crawlState.seeds.splice(index, 1);
        renderSeedList();
      }
    });

    row.append(urlCell, allowHttpCell, edit, remove);
    table.appendChild(row);
  });
}

function renderBlockedList() {
  const list = document.getElementById('blockedList');
  if (!list) return;
  list.innerHTML = '';

  // Sort blocked domains alphabetically
  const sortedBlocked = [...crawlState.blocked].sort((a, b) => {
    return a.toLowerCase().localeCompare(b.toLowerCase());
  });

  sortedBlocked.forEach((domain, displayIndex) => {
    // Find the original index for edit state tracking
    const index = crawlState.blocked.indexOf(domain);
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

// Per-row save/delete functions for allowed URLs
async function saveAllowedUrlRow(rule, statusElement) {
  if (!statusElement) return;

  // Show saving status
  statusElement.textContent = '⏳';
  statusElement.title = 'Saving...';
  statusElement.className = 'row-status saving';

  try {
    const payload = {
      pattern: rule.pattern,
      match: rule.match || 'prefix',
      types: rule.types || {},
      allow_http: rule.allow_http || false,
      auth_profile: rule.auth_profile || null
    };

    let response;
    if (rule.id) {
      // Update existing rule
      response = await fetch(`${API_BASE}/api/admin/allowed-urls/${rule.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } else {
      // Create new rule
      response = await fetch(`${API_BASE}/api/admin/allowed-urls`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    }

    if (response.ok) {
      const savedRule = await response.json();
      // Update the rule with the ID from the server
      rule.id = savedRule.id;

      // Show success
      statusElement.textContent = '✓';
      statusElement.title = 'Saved';
      statusElement.className = 'row-status saved';

      // Clear success status after 2 seconds
      setTimeout(() => {
        statusElement.textContent = '';
        statusElement.title = '';
        statusElement.className = 'row-status';
      }, 2000);

      return true;
    } else {
      const error = await response.text();
      statusElement.textContent = '✗';
      statusElement.title = `Error: ${error}`;
      statusElement.className = 'row-status error';
      return false;
    }
  } catch (e) {
    statusElement.textContent = '✗';
    statusElement.title = `Error: ${e.message}`;
    statusElement.className = 'row-status error';
    return false;
  }
}

async function deleteAllowedUrlRow(rule, statusElement) {
  if (!rule.id) {
    // If no ID, just remove from local state
    return true;
  }

  if (statusElement) {
    statusElement.textContent = '⏳';
    statusElement.title = 'Deleting...';
    statusElement.className = 'row-status saving';
  }

  try {
    const response = await fetch(`${API_BASE}/api/admin/allowed-urls/${rule.id}`, {
      method: 'DELETE'
    });

    if (response.ok) {
      return true;
    } else {
      const error = await response.text();
      if (statusElement) {
        statusElement.textContent = '✗';
        statusElement.title = `Error: ${error}`;
        statusElement.className = 'row-status error';
      }
      return false;
    }
  } catch (e) {
    if (statusElement) {
      statusElement.textContent = '✗';
      statusElement.title = `Error: ${e.message}`;
      statusElement.className = 'row-status error';
    }
    return false;
  }
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
    <div>Auth Status</div>
    <div>Auth Profile</div>
    <div>Web</div>
    <div>PDF</div>
    <div>DOCX</div>
    <div>XLSX</div>
    <div>PPTX</div>
    <div>Allow HTTP</div>
    <div></div>
    <div></div>
    <div></div>
  `;
  table.appendChild(header);

  // Sort allow rules alphabetically by pattern
  const sortedAllowRules = [...crawlState.allowRules].sort((a, b) => {
    return (a.pattern || '').toLowerCase().localeCompare((b.pattern || '').toLowerCase());
  });

  sortedAllowRules.forEach((rule, displayIndex) => {
    // Find the original index for edit state tracking
    const index = crawlState.allowRules.findIndex(r => r.id === rule.id || r.pattern === rule.pattern);
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

    const authCell = document.createElement('div');
    const authStatus = getAllowedUrlStatusIcon(rule);
    const statusBadge = document.createElement('span');
    statusBadge.className = `auth-status-icon ${authStatus.className}`;
    statusBadge.textContent = authStatus.icon;
    statusBadge.title = authStatus.tooltip;
    statusBadge.style.cursor = 'help';
    authCell.appendChild(statusBadge);

    let authProfileCell;
    if (editState.allow === index) {
      const select = document.createElement('select');
      const noneOption = document.createElement('option');
      noneOption.value = '';
      noneOption.textContent = 'None';
      select.appendChild(noneOption);
      authProfiles.forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        select.appendChild(option);
      });
      if (rule.auth_profile && !authProfiles.includes(rule.auth_profile)) {
        const legacyOption = document.createElement('option');
        legacyOption.value = rule.auth_profile;
        legacyOption.textContent = `Legacy: ${rule.auth_profile}`;
        select.appendChild(legacyOption);
      }
      select.value = rule.auth_profile || '';
      select.addEventListener('change', () => {
        rule.auth_profile = select.value || null;
        setAllowedUrlStatus(rule, { ui_status: rule.auth_profile ? 'unknown' : 'unknown' });
        renderAllowTable();
      });
      authProfileCell = select;
    } else {
      const label = document.createElement('span');
      label.className = 'auth-profile-label';
      if (rule.auth_profile === 'default') {
        label.textContent = 'Legacy (hidden)';
      } else {
        label.textContent = rule.auth_profile || 'None';
      }
      authProfileCell = label;
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

    const allowHttpCell = document.createElement('label');
    const allowHttpCheckbox = document.createElement('input');
    allowHttpCheckbox.type = 'checkbox';
    allowHttpCheckbox.checked = Boolean(rule.allow_http);
    allowHttpCheckbox.addEventListener('change', () => {
      rule.allow_http = allowHttpCheckbox.checked;
      // Re-normalize URL when toggling allow_http
      try {
        const normalized = normalizeUrlRow(rule.pattern, rule.allow_http);
        rule.pattern = normalized;
        renderAllowTable();
        renderRecommendations();
      } catch (e) {
        // Keep the current value if normalization fails
      }
    });
    allowHttpCell.appendChild(allowHttpCheckbox);

    const statusCell = document.createElement('div');
    statusCell.className = 'row-status';

    const edit = document.createElement('button');
    edit.className = 'icon-btn';
    edit.setAttribute('aria-label', 'Edit allowed URL');
    edit.textContent = editState.allow === index ? '💾' : '✏️';
    const remove = document.createElement('button');
    remove.className = 'icon-btn';
    remove.setAttribute('aria-label', 'Delete allowed URL');
    remove.textContent = editState.allow === index ? '✖️' : '🗑️';

    edit.addEventListener('click', async () => {
      if (editState.allow === index) {
        const input = urlCell.querySelector('input');
        try {
          const value = normalizeUrlRow(input?.value || '', rule.allow_http);
          if (value) {
            rule.pattern = value;
          }
        } catch (e) {
          alert(e.message);
          return;
        }
        if (matchCell instanceof HTMLSelectElement) {
          rule.match = matchCell.value;
        }

        // Save the row immediately
        const success = await saveAllowedUrlRow(rule, statusCell);
        if (success) {
          editState.allow = null;
          await loadConfigs();
        }
      } else {
        editState.allow = index;
        renderAllowTable();
        renderRecommendations();
      }
    });

    remove.addEventListener('click', async () => {
      if (editState.allow === index) {
        editState.allow = null;
        renderAllowTable();
      } else {
        // Delete the row from backend first
        const success = await deleteAllowedUrlRow(rule, statusCell);
        if (success) {
          await loadConfigs();
        }
      }
    });

    row.append(
      urlCell,
      matchCell,
      authCell,
      authProfileCell,
      ...checkboxes,
      allowHttpCell,
      statusCell,
      edit,
      remove
    );
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
      try {
        const normalized = normalizeUrlRow(rec.suggested_url, false);
        crawlState.allowRules.push({
          pattern: normalized,
          match: 'prefix',
          types,
          allow_http: false,
          auth_profile: null
        });
        renderAllowTable();
        renderRecommendations();
      } catch (e) {
        alert(`Failed to add recommendation: ${e.message}`);
      }
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

function getRuleHost(pattern) {
  let host = '';
  try {
    host = new URL(pattern).hostname;
  } catch (error) {
    const stripped = pattern.replace(/^https?:\/\//, '');
    host = stripped.split('/')[0];
  }
  return host;
}

function getAllowedUrlStatus(rule) {
  if (!rule) return null;
  if (rule.id && allowedUrlAuthStatus.byRuleId?.[rule.id]) {
    return allowedUrlAuthStatus.byRuleId[rule.id];
  }
  if (allowedUrlAuthStatus.byPattern?.[rule.pattern]) {
    return allowedUrlAuthStatus.byPattern[rule.pattern];
  }
  return null;
}

function setAllowedUrlStatus(rule, status) {
  if (!rule) return;
  if (rule.id) {
    allowedUrlAuthStatus.byRuleId = allowedUrlAuthStatus.byRuleId || {};
    allowedUrlAuthStatus.byRuleId[rule.id] = status;
  } else if (rule.pattern) {
    allowedUrlAuthStatus.byPattern = allowedUrlAuthStatus.byPattern || {};
    allowedUrlAuthStatus.byPattern[rule.pattern] = status;
  }
}

function getAllowedUrlStatusIcon(rule) {
  const status = getAllowedUrlStatus(rule);
  const authProfile = rule.auth_profile;
  const playwrightAvailable = allowedUrlAuthStatus.playwrightAvailable !== false;

  if (authProfile && !playwrightAvailable) {
    return {
      icon: '⚠️',
      className: 'cannot-test',
      tooltip: 'Playwright is unavailable in this environment. Auth checks cannot run.'
    };
  }

  const uiStatus = status?.ui_status;
  if (uiStatus === 'valid') {
    return {
      icon: '✅',
      className: 'valid',
      tooltip: 'Auth profile configured and validated.'
    };
  }
  if (uiStatus === 'invalid') {
    return {
      icon: '❌',
      className: 'invalid',
      tooltip: 'Auth profile configured but test failed. Refresh the storage state.'
    };
  }
  if (uiStatus === 'needs_profile') {
    return {
      icon: '🔒',
      className: 'needs',
      tooltip: 'Authentication appears required. Assign an auth profile to use Playwright.'
    };
  }
  if (uiStatus === 'cannot_test') {
    return {
      icon: '⚠️',
      className: 'cannot-test',
      tooltip: 'Playwright is unavailable in this environment. Auth checks cannot run.'
    };
  }
  if (authProfile) {
    return {
      icon: '⏳',
      className: 'unknown',
      tooltip: 'Auth status not checked yet. Run Test Auth to verify.'
    };
  }
  return {
    icon: '—',
    className: 'unknown',
    tooltip: 'No auth profile assigned.'
  };
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

function getAuthStatus(profileName) {
  return authStatusCache?.results?.[profileName] || null;
}

function getAuthStatusBadge(status) {
  if (allowedUrlAuthStatus.playwrightAvailable === false) {
    return { label: '⚠️ Cannot test', className: 'warning' };
  }
  if (!status) {
    return { label: '⏳ Not checked yet', className: 'warning' };
  }
  if (status.ok) {
    return { label: '✅ Valid', className: 'success' };
  }
  return { label: '❌ Invalid', className: 'error' };
}

async function loadAuthStatus() {
  try {
    const response = await fetch(`${API_BASE}/api/crawl/auth-status`);
    if (!response.ok) {
      authStatusCache = { results: {}, updatedAt: null };
      return;
    }
    const payload = await response.json();
    authStatusCache = { results: payload.results || {}, updatedAt: new Date().toISOString() };
  } catch (error) {
    authStatusCache = { results: {}, updatedAt: null };
  }
}

async function loadAllowedUrlAuthStatus() {
  try {
    const response = await fetch(`${API_BASE}/api/admin/allowed-urls/auth-status`);
    if (!response.ok) {
      allowedUrlAuthStatus = { byRuleId: {}, byPattern: {}, updatedAt: null, playwrightAvailable: true };
      return;
    }
    const payload = await response.json();
    const byRuleId = {};
    const byPattern = {};
    (payload.rules || []).forEach((entry) => {
      if (entry.rule_id) {
        byRuleId[entry.rule_id] = entry;
      }
      if (entry.pattern) {
        byPattern[entry.pattern] = entry;
      }
    });
    allowedUrlAuthStatus = {
      byRuleId,
      byPattern,
      updatedAt: new Date().toISOString(),
      playwrightAvailable: payload.playwright_available !== false
    };
    const warning = document.getElementById('playwrightWarning');
    if (warning) {
      warning.style.display = allowedUrlAuthStatus.playwrightAvailable ? 'none' : 'block';
    }
  } catch (error) {
    allowedUrlAuthStatus = { byRuleId: {}, byPattern: {}, updatedAt: null, playwrightAvailable: true };
  }
}

async function testAuthProfile(profileName, statusEl, buttonEl) {
  if (!profileName) return;
  if (buttonEl) {
    buttonEl.disabled = true;
    buttonEl.textContent = 'Testing...';
  }
  if (statusEl) {
    statusEl.textContent = 'Running auth check...';
    statusEl.style.color = 'var(--text-secondary)';
  }
  try {
    const response = await fetch(`${API_BASE}/api/crawl/test-auth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_name: profileName })
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const payload = await response.json();
    authStatusCache = {
      results: { ...(authStatusCache.results || {}), ...(payload.results || {}) },
      updatedAt: new Date().toISOString()
    };
    await loadAllowedUrlAuthStatus();
    renderAuthProfilesList();
    renderAllowTable();
  } catch (error) {
    if (statusEl) {
      statusEl.textContent = `Auth check failed: ${error.message}`;
      statusEl.style.color = 'var(--status-error)';
    }
  } finally {
    if (buttonEl) {
      buttonEl.disabled = false;
      buttonEl.textContent = 'Test Auth';
    }
  }
}

function renderAuthProfilesList() {
  const list = document.getElementById('authProfilesList');
  if (!list) return;

  const playwright = cachedCrawlerConfig.playwright || {};
  const profiles = playwright.auth_profiles || {};
  const profileNames = Object.keys(profiles).filter((name) => name !== 'default');

  if (profileNames.length === 0) {
    list.innerHTML = '<div style="padding: 12px; text-align: center; color: var(--text-secondary); font-size: 0.85rem;">No auth profiles configured. Click "+ Add Profile" to create one.</div>';
    return;
  }

  // Sort profile names alphabetically
  const sortedProfileNames = [...profileNames].sort((a, b) => {
    return a.toLowerCase().localeCompare(b.toLowerCase());
  });

  list.innerHTML = '';
  sortedProfileNames.forEach((name) => {
    const profile = profiles[name];
    const row = document.createElement('div');
    row.className = 'list-row';
    row.style.alignItems = 'flex-start';
    row.style.padding = '10px';

    const details = document.createElement('div');
    details.style.flex = '1';
    details.style.minWidth = '0';

    const nameEl = document.createElement('div');
    nameEl.style.fontWeight = '600';
    nameEl.style.marginBottom = '4px';
    nameEl.textContent = name;

    const pathEl = document.createElement('div');
    pathEl.style.fontSize = '0.75rem';
    pathEl.style.color = 'var(--text-secondary)';
    pathEl.style.wordBreak = 'break-all';
    pathEl.textContent = `Path: ${profile.storage_state_path || 'Not set'}`;

    const testUrlEl = document.createElement('div');
    testUrlEl.style.fontSize = '0.75rem';
    testUrlEl.style.color = 'var(--text-tertiary)';
    if (profile.test_url) {
      testUrlEl.textContent = `Test URL: ${profile.test_url}`;
    } else if (profile.start_url) {
      testUrlEl.textContent = `Test URL: auto (start URL ${profile.start_url})`;
    } else {
      testUrlEl.textContent = 'Test URL: auto (first protected Allowed URL)';
    }

    const statusRow = document.createElement('div');
    statusRow.style.display = 'flex';
    statusRow.style.flexDirection = 'column';
    statusRow.style.gap = '4px';
    statusRow.style.marginTop = '8px';

    const status = getAuthStatus(name);
    const badgeInfo = getAuthStatusBadge(status);
    const badge = document.createElement('span');
    badge.className = `status-pill ${badgeInfo.className}`;
    badge.textContent = badgeInfo.label;
    statusRow.appendChild(badge);

    const statusMessage = document.createElement('div');
    statusMessage.style.fontSize = '0.75rem';
    statusMessage.style.color = 'var(--text-secondary)';
    statusRow.appendChild(statusMessage);

    // Show last checked timestamp if available
    if (status && status.checked_at) {
      const checkedAt = document.createElement('div');
      checkedAt.style.fontSize = '0.75rem';
      checkedAt.style.color = 'var(--text-secondary)';
      const timestamp = new Date(status.checked_at);
      const timeStr = timestamp.toLocaleString();
      checkedAt.textContent = `Last checked: ${timeStr}`;
      statusRow.appendChild(checkedAt);
    }

    if (status && !status.ok) {
      const reason = document.createElement('div');
      reason.style.fontSize = '0.75rem';
      reason.style.color = 'var(--status-error)';
      reason.textContent = status.error_reason || 'Auth validation failed';
      statusRow.appendChild(reason);

      if (status.final_url) {
        const finalUrl = document.createElement('div');
        finalUrl.style.fontSize = '0.75rem';
        finalUrl.style.color = 'var(--text-secondary)';
        finalUrl.textContent = `Final URL: ${status.final_url}`;
        statusRow.appendChild(finalUrl);
      }
    }

    details.append(nameEl, pathEl, testUrlEl, statusRow);

    const actions = document.createElement('div');
    actions.className = 'list-actions';

    const editBtn = document.createElement('button');
    editBtn.className = 'icon-btn';
    editBtn.setAttribute('aria-label', 'Edit auth profile');
    editBtn.textContent = '✏️';
    editBtn.addEventListener('click', () => startEditAuthProfile(name));

    const testBtn = document.createElement('button');
    testBtn.className = 'btn btn-small';
    testBtn.textContent = 'Test Auth';
    testBtn.addEventListener('click', () => testAuthProfile(name, statusMessage, testBtn));

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'icon-btn';
    deleteBtn.setAttribute('aria-label', 'Delete auth profile');
    deleteBtn.textContent = '🗑️';
    deleteBtn.addEventListener('click', () => deleteAuthProfile(name));

    actions.append(testBtn, editBtn, deleteBtn);
    row.append(details, actions);
    list.appendChild(row);
  });

}

function showAuthProfileEditor(profileName = null) {
  const editor = document.getElementById('authProfileEditor');
  const title = document.getElementById('profileEditorTitle');
  const nameInput = document.getElementById('profileName');
  const pathInput = document.getElementById('profileStoragePath');
  const testUrlInput = document.getElementById('profileTestUrl');
  const status = document.getElementById('profileEditorStatus');

  if (!editor) return;

  editor.style.display = 'block';
  status.textContent = '';

  if (profileName) {
    title.textContent = `Edit Profile: ${profileName}`;
    const playwright = cachedCrawlerConfig.playwright || {};
    const profile = (playwright.auth_profiles || {})[profileName] || {};
    nameInput.value = profileName;
    nameInput.disabled = true;
    pathInput.value = profile.storage_state_path || '';
    testUrlInput.value = profile.test_url || '';
    editState.authProfile = profileName;
  } else {
    title.textContent = 'Add Auth Profile';
    nameInput.value = '';
    nameInput.disabled = false;
    pathInput.value = '';
    testUrlInput.value = '';
    editState.authProfile = null;

    // Auto-update path suggestion when name changes
    nameInput.addEventListener('input', function updatePathSuggestion() {
      const name = nameInput.value.trim();
      if (name && !pathInput.value) {
        pathInput.placeholder = `secrets/playwright/${name}-storageState.json`;
      }
    });
  }

  editor.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideAuthProfileEditor() {
  const editor = document.getElementById('authProfileEditor');
  const status = document.getElementById('profileEditorStatus');
  if (editor) {
    editor.style.display = 'none';
  }
  if (status) {
    status.textContent = '';
  }
  editState.authProfile = null;
}

function startEditAuthProfile(profileName) {
  showAuthProfileEditor(profileName);
}

async function saveAuthProfile() {
  const nameInput = document.getElementById('profileName');
  const pathInput = document.getElementById('profileStoragePath');
  const testUrlInput = document.getElementById('profileTestUrl');
  const status = document.getElementById('profileEditorStatus');

  const name = nameInput.value.trim();
  const path = pathInput.value.trim();
  const testUrl = testUrlInput.value.trim();

  if (!name) {
    setStatus('profileEditorStatus', 'Profile name is required', 'error');
    return;
  }

  if (name === 'default') {
    setStatus('profileEditorStatus', 'Profile name "default" is reserved. Choose a different name.', 'error');
    return;
  }

  if (!path) {
    setStatus('profileEditorStatus', 'Storage state path is required', 'error');
    return;
  }

  // Validate unique name (only when creating new profile)
  if (!editState.authProfile) {
    const playwright = cachedCrawlerConfig.playwright || {};
    const existingProfiles = playwright.auth_profiles || {};
    if (existingProfiles[name]) {
      setStatus('profileEditorStatus', 'Profile name already exists', 'error');
      return;
    }
  }

  // Update cachedCrawlerConfig
  if (!cachedCrawlerConfig.playwright) {
    cachedCrawlerConfig.playwright = {};
  }
  if (!cachedCrawlerConfig.playwright.auth_profiles) {
    cachedCrawlerConfig.playwright.auth_profiles = {};
  }

  cachedCrawlerConfig.playwright.auth_profiles[name] = {
    ...(cachedCrawlerConfig.playwright.auth_profiles[name] || {}),
    storage_state_path: path,
    test_url: testUrl || null
  };

  // Update authProfiles array
  authProfiles = Object.keys(cachedCrawlerConfig.playwright.auth_profiles).filter((name) => name !== 'default');

  // Save to backend immediately
  setStatus('profileEditorStatus', 'Saving...', 'info');
  try {
    const response = await fetch(`${API_BASE}/api/admin/playwright-settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        auth_profiles: cachedCrawlerConfig.playwright.auth_profiles
      })
    });

    if (response.ok) {
      hideAuthProfileEditor();
      await loadConfigs();
      setStatus('profileEditorStatus', 'Profile saved', 'success');
    } else {
      const error = await response.text();
      setStatus('profileEditorStatus', `Error saving: ${error}`, 'error');
    }
  } catch (e) {
    setStatus('profileEditorStatus', `Error saving: ${e.message}`, 'error');
  }
}

async function deleteAuthProfile(profileName) {
  if (!confirm(`Delete auth profile "${profileName}"? This cannot be undone.`)) {
    return;
  }

  const playwright = cachedCrawlerConfig.playwright || {};
  const profiles = playwright.auth_profiles || {};

  if (profiles[profileName]) {
    delete profiles[profileName];
    authProfiles = Object.keys(profiles).filter((name) => name !== 'default');

    // Save to backend immediately
    try {
      const response = await fetch(`${API_BASE}/api/admin/playwright-settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          auth_profiles: cachedCrawlerConfig.playwright.auth_profiles
        })
      });

      if (response.ok) {
        await loadConfigs();
      } else {
        const error = await response.text();
        alert(`Error deleting profile: ${error}`);
      }
    } catch (e) {
      alert(`Error deleting profile: ${e.message}`);
    }
  }
}

async function migrateLegacyPlaywrightSettings() {
  const playwright = cachedCrawlerConfig.playwright || {};

  // Check if legacy fields exist
  const hasLegacyPath = Boolean(playwright.storage_state_path);
  const hasLegacyDomains = Array.isArray(playwright.use_for_domains) && playwright.use_for_domains.length > 0;

  if (!hasLegacyPath && !hasLegacyDomains) {
    return;
  }

  // Create default profile from legacy settings
  if (!playwright.auth_profiles) {
    playwright.auth_profiles = {};
  }

  if (!playwright.auth_profiles.legacy_migrated) {
    playwright.auth_profiles.legacy_migrated = {
      storage_state_path: playwright.storage_state_path || '',
      use_for_domains: playwright.use_for_domains || []
    };
  }

  // Remove legacy fields from the config object
  delete playwright.storage_state_path;
  delete playwright.use_for_domains;

  cachedCrawlerConfig.playwright = playwright;
  authProfiles = Object.keys(playwright.auth_profiles).filter((name) => name !== 'default');

  // Hide migration banner
  const banner = document.getElementById('migrationBanner');
  if (banner) {
    banner.style.display = 'none';
  }

  renderAuthProfilesList();
  renderAllowTable();
  setStatus('saveAuthStatus', 'Legacy settings migrated to "legacy_migrated" profile. Click Save to persist.', 'success');
}

function checkForLegacySettings() {
  const playwright = cachedCrawlerConfig.playwright || {};
  const hasAuthProfiles = playwright.auth_profiles && Object.keys(playwright.auth_profiles).length > 0;
  const hasLegacyPath = Boolean(playwright.storage_state_path);
  const hasLegacyDomains = Array.isArray(playwright.use_for_domains) && playwright.use_for_domains.length > 0;

  const banner = document.getElementById('migrationBanner');
  if (banner && (hasLegacyPath || hasLegacyDomains) && !hasAuthProfiles) {
    banner.style.display = 'block';
  } else if (banner) {
    banner.style.display = 'none';
  }
}

function showTab(name) {
  document.querySelectorAll('.tab-content').forEach((el) => {
    el.style.display = el.id === name ? 'block' : 'none';
  });
  document.querySelectorAll('.tab-button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === name);
  });

  // Load data when switching to Data tab
  if (name === 'data') {
    loadPipelineHealth();
    loadDataValidation();
  }
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

  // Normalize and convert seeds to new structure
  const rawSeeds = allow.seed_urls || [];
  crawlState.seeds = rawSeeds.map((seed) => {
    const normalized = normalizeSeed(seed);
    // Normalize the URL based on the allow_http flag
    try {
      normalized.url = normalizeUrlRow(normalized.url, normalized.allow_http);
    } catch (e) {
      // Keep original if normalization fails
    }
    return normalized;
  });

  crawlState.blocked = allow.blocked_domains || [];

  // Normalize and convert allow rules
  if (Array.isArray(allow.allow_rules) && allow.allow_rules.length > 0) {
    crawlState.allowRules = allow.allow_rules.map((rule) => {
      const normalized = normalizeAllowRule(rule);
      // Normalize the pattern based on the allow_http flag
      try {
        normalized.pattern = normalizeUrlRow(normalized.pattern, normalized.allow_http);
      } catch (e) {
        // Keep original if normalization fails
      }
      return normalized;
    });
  } else {
    const allowedDomains = allow.allowed_domains || [];
    crawlState.allowRules = allowedDomains.map((domain) =>
      normalizeAllowRule({
        pattern: `https://${domain}/`,
        match: 'prefix',
        types: { web: true },
        allow_http: false
      })
    );
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
  authProfiles = Object.keys(playwright.auth_profiles || {}).filter((name) => name !== 'default');

  await loadAuthStatus();
  await loadAllowedUrlAuthStatus();
  renderAuthProfilesList();
  checkForLegacySettings();
  renderAllowTable();

  try {
    const ingest = await fetch(`${API_BASE}/api/admin/config/ingest`).then((r) => r.json());
    cachedIngestConfig = ingest || {};
    applyIngestConfig(cachedIngestConfig);
  } catch (error) {
    cachedIngestConfig = {};
  }
}

function applyIngestConfig(config = {}) {
  const ingest = config.ingest || {};
  const embedConcurrency = document.getElementById('ingestEmbedConcurrency');
  const upsertBatchSize = document.getElementById('ingestUpsertBatchSize');
  const chunkBatchSize = document.getElementById('ingestChunkBatchSize');
  const maxInflightChunks = document.getElementById('ingestMaxInflightChunks');

  if (embedConcurrency) embedConcurrency.value = ingest.embed_concurrency ?? 4;
  if (upsertBatchSize) upsertBatchSize.value = ingest.upsert_batch_size ?? 64;
  if (chunkBatchSize) chunkBatchSize.value = ingest.chunk_batch_size ?? 16;
  if (maxInflightChunks) maxInflightChunks.value = ingest.max_inflight_chunks ?? 256;
}

async function saveIngestPerformanceSettings() {
  const statusTarget = 'ingestPerformanceStatus';
  setStatus(statusTarget, 'Saving ingest performance settings...');
  const embedConcurrency = parseInt(document.getElementById('ingestEmbedConcurrency')?.value || '4', 10);
  const upsertBatchSize = parseInt(document.getElementById('ingestUpsertBatchSize')?.value || '64', 10);
  const chunkBatchSize = parseInt(document.getElementById('ingestChunkBatchSize')?.value || '16', 10);
  const maxInflightChunks = parseInt(document.getElementById('ingestMaxInflightChunks')?.value || '256', 10);

  const updated = {
    ...cachedIngestConfig,
    ingest: {
      ...(cachedIngestConfig.ingest || {}),
      embed_concurrency: embedConcurrency,
      upsert_batch_size: upsertBatchSize,
      chunk_batch_size: chunkBatchSize,
      max_inflight_chunks: maxInflightChunks
    }
  };

  try {
    const response = await fetch(`${API_BASE}/api/admin/config/ingest`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updated)
    });
    if (!response.ok) {
      const message = await response.text();
      setStatus(statusTarget, message || 'Error saving ingest config', 'error');
      return;
    }
    cachedIngestConfig = updated;
    setStatus(statusTarget, 'Performance settings saved. Restart worker if needed.', 'success');
  } catch (error) {
    setStatus(statusTarget, `Error: ${error.message}`, 'error');
  }
}

async function loadIngestWorkerStatus() {
  const statusEl = document.getElementById('ingestWorkerState');
  const queueEl = document.getElementById('ingestQueueDepth');
  if (!statusEl || !queueEl) return;
  if (ingestWorkerPoller) {
    clearTimeout(ingestWorkerPoller);
    ingestWorkerPoller = null;
  }
  try {
    const resp = await fetch(`${API_BASE}/api/ingest/worker/status`);
    if (!resp.ok) {
      statusEl.textContent = 'Worker offline — start ingestor-worker service';
      queueEl.textContent = '-';
      return;
    }
    const data = await resp.json();
    const age = data.age_seconds;
    if (age === null) {
      statusEl.textContent = 'Worker offline — start ingestor-worker service';
    } else if (age > 15) {
      statusEl.textContent = `Worker stale (${Math.round(age)}s ago)`;
    } else {
      const pid = data.worker?.pid ? `PID ${data.worker.pid}` : 'Online';
      statusEl.textContent = `Online (${pid})`;
    }
    queueEl.textContent = data.queue_depth ?? '-';
  } catch (error) {
    statusEl.textContent = 'Worker offline — start ingestor-worker service';
    queueEl.textContent = '-';
  }
  ingestWorkerPoller = setTimeout(loadIngestWorkerStatus, 5000);
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
  // Sort seeds alphabetically by URL
  const sortedSeeds = [...crawlState.seeds].sort((a, b) => {
    const urlA = (normalizeSeed(a).url || '').toLowerCase();
    const urlB = (normalizeSeed(b).url || '').toLowerCase();
    return urlA.localeCompare(urlB);
  });

  // Sort blocked domains alphabetically
  const sortedBlocked = [...crawlState.blocked].sort((a, b) => {
    return a.toLowerCase().localeCompare(b.toLowerCase());
  });

  // Sort allow rules alphabetically by pattern
  const sortedAllowRules = [...crawlState.allowRules].sort((a, b) => {
    return (a.pattern || '').toLowerCase().localeCompare((b.pattern || '').toLowerCase());
  });

  const allowPayload = {
    seed_urls: sortedSeeds.map((seed) => {
      const normalized = normalizeSeed(seed);
      return {
        url: normalized.url,
        allow_http: normalized.allow_http
      };
    }),
    blocked_domains: sortedBlocked,
    allow_rules: sortedAllowRules.map((rule) => ({
      id: rule.id,
      pattern: rule.pattern,
      match: rule.match || 'prefix',
      types: normalizeTypes(rule.types),
      allow_http: Boolean(rule.allow_http),
      auth_profile: rule.auth_profile || null
    })),
    allowed_domains: extractAllowedDomains(sortedAllowRules)
  };
  const allowResponse = await fetch(`${API_BASE}/api/admin/config/allow_block`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(allowPayload)
  });
  if (allowResponse.ok) {
    setStatus('saveCrawlStatus', 'Saved');
    await loadConfigs();
  } else {
    setStatus('saveCrawlStatus', 'Error saving config', 'error');
  }
}

async function saveAuthConfig() {
  setStatus('saveAuthStatus', 'Saving...');
  const playwright = cachedCrawlerConfig.playwright || {};
  const crawlerPayload = {
    ...cachedCrawlerConfig,
    playwright: {
      ...playwright,
      auth_profiles: playwright.auth_profiles || {}
    }
  };
  try {
    const response = await fetch(`${API_BASE}/api/admin/config/crawler`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(crawlerPayload)
    });
    if (response.ok) {
      setStatus('saveAuthStatus', 'Saved');
      await loadConfigs();
    } else {
      const error = await response.text();
      setStatus('saveAuthStatus', error || 'Error saving auth config', 'error');
    }
  } catch (error) {
    setStatus('saveAuthStatus', `Error: ${error.message}`, 'error');
  }
}

function addSeedFromInput() {
  const input = document.getElementById('seedAddInput');
  const allowHttpCheckbox = document.getElementById('seedAddAllowHttp');
  if (!input) return;
  const allowHttp = allowHttpCheckbox ? allowHttpCheckbox.checked : false;
  try {
    const value = normalizeUrlRow(input.value, allowHttp);
    if (!value) return;
    crawlState.seeds.push({
      url: value,
      allow_http: allowHttp
    });
    input.value = '';
    if (allowHttpCheckbox) allowHttpCheckbox.checked = false;
    renderSeedList();
  } catch (e) {
    alert(e.message);
  }
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

async function addAllowFromInput() {
  const input = document.getElementById('allowAddInput');
  const match = document.getElementById('allowAddMatch');
  const allowHttpCheckbox = document.getElementById('allowAddAllowHttp');
  const statusElement = document.getElementById('allowAddStatus');
  if (!input || !match) return;
  const allowHttp = allowHttpCheckbox ? allowHttpCheckbox.checked : false;

  if (statusElement) {
    statusElement.textContent = '⏳ Saving...';
    statusElement.className = 'row-status saving';
  }

  try {
    const value = normalizeUrlRow(input.value, allowHttp);
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

    const newRule = {
      pattern: value,
      match: match.value,
      types,
      allow_http: allowHttp,
      auth_profile: null
    };

    // Save to backend immediately
    const response = await fetch(`${API_BASE}/api/admin/allowed-urls`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newRule)
    });

    if (response.ok) {
      await response.json();

      // Clear inputs
      input.value = '';
      if (allowHttpCheckbox) allowHttpCheckbox.checked = false;

      // Show success
      if (statusElement) {
        statusElement.textContent = '✓ Saved';
        statusElement.className = 'row-status saved';
        setTimeout(() => {
          statusElement.textContent = '';
          statusElement.className = 'row-status';
        }, 2000);
      }

      await loadConfigs();
    } else {
      const error = await response.text();
      if (statusElement) {
        statusElement.textContent = `✗ Error: ${error}`;
        statusElement.className = 'row-status error';
      }
    }
  } catch (e) {
    if (statusElement) {
      statusElement.textContent = `✗ Error: ${e.message}`;
      statusElement.className = 'row-status error';
    } else {
      alert(e.message);
    }
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

    // Handle both old and new summary formats
    const skipped = summary.skipped || {};
    const errorsByClass = summary.errors_by_class || {};

    const totalSkipped = (skipped.already_processed || summary.skipped_already_processed || 0) +
                        (skipped.depth_exceeded || summary.skipped_depth || 0) +
                        (skipped.not_allowed || summary.skipped_not_allowed || 0) +
                        (skipped.auth_required || 0) +
                        (skipped.non_html || 0);

    let html = `
      <div class="info-grid">
        <div>
          <div class="field-label">Total Seeds</div>
          <div class="info-value">${summary.total_seeds || 0}</div>
        </div>
        <div>
          <div class="field-label">Candidates Loaded</div>
          <div class="info-value">${summary.total_candidates || 0}</div>
        </div>
        <div>
          <div class="field-label">URLs Crawled</div>
          <div class="info-value">${summary.crawled || 0}</div>
        </div>
        <div>
          <div class="field-label">Successfully Captured</div>
          <div class="info-value">${summary.captured || 0}</div>
        </div>
        <div>
          <div class="field-label">Artifacts Written</div>
          <div class="info-value">${summary.artifacts_written || summary.captured || 0}</div>
        </div>
        <div>
          <div class="field-label">Total Errors</div>
          <div class="info-value">${summary.errors || 0}</div>
        </div>
        <div>
          <div class="field-label">Total Skipped</div>
          <div class="info-value">${totalSkipped}</div>
        </div>
      </div>
    `;

    // Add detailed skipped breakdown
    if (totalSkipped > 0) {
      html += `
        <div style="margin-top: 12px;">
          <div class="field-label">Skipped Breakdown:</div>
          <div style="font-size: 12px; color: #666;">
            ${skipped.already_processed || summary.skipped_already_processed ? `<div>• Already processed: ${skipped.already_processed || summary.skipped_already_processed}</div>` : ''}
            ${skipped.depth_exceeded || summary.skipped_depth ? `<div>• Depth exceeded: ${skipped.depth_exceeded || summary.skipped_depth}</div>` : ''}
            ${skipped.not_allowed || summary.skipped_not_allowed ? `<div>• Not allowed: ${skipped.not_allowed || summary.skipped_not_allowed}</div>` : ''}
            ${skipped.auth_required ? `<div>• Auth required: ${skipped.auth_required}</div>` : ''}
            ${skipped.non_html ? `<div>• Non-HTML: ${skipped.non_html}</div>` : ''}
          </div>
        </div>
      `;
    }

    // Add errors by class
    const totalErrorsByClass = (errorsByClass['4xx'] || 0) + (errorsByClass['5xx'] || 0) +
                                (errorsByClass.network_timeout || 0) + (errorsByClass.other || 0);
    if (totalErrorsByClass > 0) {
      html += `
        <div style="margin-top: 12px;">
          <div class="field-label">Errors by Class:</div>
          <div style="font-size: 12px; color: #666;">
            ${errorsByClass['4xx'] ? `<div>• 4xx: ${errorsByClass['4xx']}</div>` : ''}
            ${errorsByClass['5xx'] ? `<div>• 5xx: ${errorsByClass['5xx']}</div>` : ''}
            ${errorsByClass.network_timeout ? `<div>• Network/Timeout: ${errorsByClass.network_timeout}</div>` : ''}
            ${errorsByClass.other ? `<div>• Other: ${errorsByClass.other}</div>` : ''}
          </div>
        </div>
      `;
    }

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

    // Update pill summary at top of logs
    const pillSummary = document.getElementById('crawlPillSummary');
    if (pillSummary) {
      const pillHtml = `
        <span class="status-pill success">Captured: ${summary.captured || 0}</span>
        <span class="status-pill ${summary.errors > 0 ? 'error' : 'success'}">Errors: ${summary.errors || 0}</span>
        <span class="status-pill">Skipped: ${totalSkipped}</span>
      `;
      pillSummary.innerHTML = pillHtml;
      pillSummary.style.display = 'flex';
    }
    await loadAllowedUrlAuthStatus();
    renderAllowTable();
  } catch (error) {
    summaryPanel.style.display = 'none';
    const pillSummary = document.getElementById('crawlPillSummary');
    if (pillSummary) {
      pillSummary.style.display = 'none';
    }
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
    const pillSummary = document.getElementById('crawlPillSummary');
    if (pillSummary) {
      pillSummary.style.display = 'none';
    }
    streamLog(currentCrawlJobId, 'crawlLog', 'crawl');
  } else {
    currentIngestJobId = data.job_id;
    streamLog(currentIngestJobId, 'ingestQueueLog', 'ingest');
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
      const pillSummary = document.getElementById('crawlPillSummary');
      if (pillSummary) {
        pillSummary.style.display = 'none';
      }
    }
    setStatus(statusTarget, 'Deleted');
  }
  loadJobs();
}

async function clearVectors() {
  const confirmed = prompt(
    'This will delete ALL vectors from Qdrant and reset the ingest metadata database.\n\n' +
    'The following will be deleted:\n' +
    '• All vectors in the Qdrant collection\n' +
    '• Ingest metadata.db (documents and chunks tables)\n\n' +
    'Type DELETE to confirm:'
  );

  if (confirmed !== 'DELETE') {
    return;
  }

  const statusTarget = 'clearVectorsStatus';
  setStatus(statusTarget, 'Reading current vector count...');

  try {
    const response = await fetch(`${API_BASE}/api/admin/reset/qdrant`, { method: 'POST' });
    if (response.ok) {
      const result = await response.json();
      const collection = result.collection || 'unknown';

      setStatus(
        statusTarget,
        `Cleared vectors from '${collection}'.`,
        'success'
      );
    } else {
      setStatus(statusTarget, 'Error clearing vectors', 'error');
    }
  } catch (error) {
    setStatus(statusTarget, `Error: ${error.message}`, 'error');
  }
}

async function resetCrawl() {
  const confirmed = prompt(
    'This will delete ALL crawl state including artifacts, candidates, and logs.\n\n' +
    'The following will be deleted:\n' +
    '• data/artifacts/* (all crawled content)\n' +
    '• data/candidates/* (URL discovery cache)\n' +
    '• data/logs/jobs/* (crawl job logs)\n' +
    '• data/logs/summaries/* (crawl summaries)\n\n' +
    'Type DELETE to confirm:'
  );

  if (confirmed !== 'DELETE') {
    return;
  }

  const statusTarget = 'resetCrawlStatus';
  setStatus(statusTarget, 'Resetting crawl state...');

  try {
    const response = await fetch(`${API_BASE}/api/admin/reset/artifacts`, { method: 'POST' });
    if (response.ok) {
      const result = await response.json();
      const deletedItems = result.deleted || [];
      setStatus(statusTarget, `Reset complete: ${deletedItems.join(', ')}`, 'success');
    } else {
      setStatus(statusTarget, 'Error resetting crawl state', 'error');
    }
  } catch (error) {
    setStatus(statusTarget, `Error: ${error.message}`, 'error');
  }
}

async function resetIngest() {
  const confirmed = prompt(
    'This will delete the ingest metadata database.\n\n' +
    'The following will be deleted:\n' +
    '• data/ingest/metadata.db (documents and chunks tables)\n\n' +
    'Note: This does NOT delete vectors from Qdrant.\n' +
    'Use "Clear Vector DB" to also remove vectors.\n\n' +
    'Type DELETE to confirm:'
  );

  if (confirmed !== 'DELETE') {
    return;
  }

  const statusTarget = 'resetIngestStatus';
  setStatus(statusTarget, 'Resetting ingest state...');

  try {
    const response = await fetch(`${API_BASE}/api/admin/reset_ingest`, { method: 'POST' });
    if (response.ok) {
      const result = await response.json();
      const deletedItems = result.deleted || [];
      setStatus(statusTarget, `Reset complete: ${deletedItems.join(', ')}`, 'success');
    } else {
      setStatus(statusTarget, 'Error resetting ingest state', 'error');
    }
  } catch (error) {
    setStatus(statusTarget, `Error: ${error.message}`, 'error');
  }
}

async function resetAllData() {
  const confirmed = prompt(
    'This will delete ALL data including artifacts, logs, quarantine, Qdrant vectors, and ingest metadata.\n\n' +
    'Type DELETE to confirm:'
  );

  if (confirmed !== 'DELETE') {
    return;
  }

  const statusTarget = 'resetAllStatus';
  setStatus(statusTarget, 'Resetting all data...');

  try {
    const response = await fetch(`${API_BASE}/api/admin/reset/all`, { method: 'POST' });
    if (response.ok) {
      const result = await response.json();
      const deletedItems = result.deleted || [];
      setStatus(statusTarget, `Reset complete: ${deletedItems.join(', ')}`, 'success');
    } else {
      setStatus(statusTarget, 'Error resetting all data', 'error');
    }
  } catch (error) {
    setStatus(statusTarget, `Error: ${error.message}`, 'error');
  }
}

// Pipeline Health functions
async function loadPipelineHealth() {
  const statusTarget = 'pipelineHealthStatus';
  setStatus(statusTarget, 'Loading pipeline health...');
  try {
    const resp = await fetch(`${API_BASE}/api/admin/data/health`);
    if (!resp.ok) {
      setStatus(statusTarget, 'Error loading health data', 'error');
      return;
    }
    const health = await resp.json();
    renderPipelineHealth(health);
    setStatus(statusTarget, '');
  } catch (err) {
    setStatus(statusTarget, `Error: ${err.message}`, 'error');
  }
}

function renderPipelineHealth(health = {}) {
  const grid = document.getElementById('pipelineHealthGrid');
  if (!grid) return;

  const cards = [];

  // Artifacts card
  if (health.artifacts) {
    const a = health.artifacts;
    cards.push(`
      <div class="panel-card" style="padding: 12px;">
        <h5 style="margin: 0 0 8px 0; font-size: 0.9rem; color: var(--text-secondary);">Artifacts</h5>
        <div style="font-size: 1.2rem; font-weight: 600;">${a.count || 0}</div>
        <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 4px;">
          ${a.quarantined ? `Quarantined: ${a.quarantined}` : 'No quarantined'}
        </div>
        ${a.last_captured_at ? `<div style="font-size: 0.75rem; color: var(--text-secondary);">Last: ${new Date(a.last_captured_at).toLocaleString()}</div>` : ''}
      </div>
    `);
  }

  // Crawl card
  if (health.crawl) {
    const lastJob = health.crawl.last_job;
    const statusColor = lastJob?.status === 'done' ? 'var(--success-color)' : lastJob?.status === 'error' ? 'var(--error-color)' : 'var(--text-secondary)';
    cards.push(`
      <div class="panel-card" style="padding: 12px;">
        <h5 style="margin: 0 0 8px 0; font-size: 0.9rem; color: var(--text-secondary);">Crawl</h5>
        ${lastJob ? `
          <div style="font-size: 0.85rem; color: ${statusColor}; font-weight: 600;">${lastJob.status || 'unknown'}</div>
          <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 4px;">Job: ${lastJob.id?.substring(0, 8) || 'N/A'}</div>
          ${lastJob.finished_at ? `<div style="font-size: 0.75rem; color: var(--text-secondary);">Finished: ${new Date(lastJob.finished_at).toLocaleString()}</div>` : ''}
        ` : '<div style="font-size: 0.85rem; color: var(--text-secondary);">No jobs yet</div>'}
      </div>
    `);
  }

  // Ingest card
  if (health.ingest) {
    const worker = health.ingest.worker || {};
    const lastJob = health.ingest.last_job;
    const workerStatus = worker.status || 'unknown';
    const statusColor = workerStatus === 'idle' ? 'var(--success-color)' : workerStatus === 'busy' ? 'var(--warning-color)' : 'var(--text-secondary)';
    cards.push(`
      <div class="panel-card" style="padding: 12px;">
        <h5 style="margin: 0 0 8px 0; font-size: 0.9rem; color: var(--text-secondary);">Ingest</h5>
        <div style="font-size: 0.85rem; color: ${statusColor}; font-weight: 600;">Worker: ${workerStatus}</div>
        ${lastJob ? `
          <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 4px;">Last job: ${lastJob.status || 'unknown'}</div>
          ${lastJob.finished_at ? `<div style="font-size: 0.75rem; color: var(--text-secondary);">Finished: ${new Date(lastJob.finished_at).toLocaleString()}</div>` : ''}
        ` : '<div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 4px;">No jobs yet</div>'}
      </div>
    `);
  }

  // Qdrant card
  if (health.qdrant) {
    const collections = health.qdrant.collections || [];
    const totalPoints = collections.reduce((sum, c) => sum + (c.points || 0), 0);
    cards.push(`
      <div class="panel-card" style="padding: 12px;">
        <h5 style="margin: 0 0 8px 0; font-size: 0.9rem; color: var(--text-secondary);">Qdrant</h5>
        <div style="font-size: 1.2rem; font-weight: 600;">${totalPoints.toLocaleString()}</div>
        <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 4px;">
          ${collections.map(c => `${c.name}: ${c.points || 0}`).join(', ') || 'No collections'}
        </div>
      </div>
    `);
  }

  // System card
  if (health.system) {
    const apiHealth = health.system.api_health || 'unknown';
    const statusColor = apiHealth === 'ok' ? 'var(--success-color)' : 'var(--warning-color)';
    cards.push(`
      <div class="panel-card" style="padding: 12px;">
        <h5 style="margin: 0 0 8px 0; font-size: 0.9rem; color: var(--text-secondary);">System</h5>
        <div style="font-size: 0.85rem; color: ${statusColor}; font-weight: 600;">API: ${apiHealth}</div>
      </div>
    `);
  }

  grid.innerHTML = cards.join('');
}

// Check Data functions
async function checkUrl() {
  const input = document.getElementById('checkUrlInput');
  const url = input?.value.trim();
  if (!url) {
    setStatus('checkUrlStatus', 'Please enter a URL', 'error');
    return;
  }

  setStatus('checkUrlStatus', 'Checking URL...');
  document.getElementById('checkUrlResults').style.display = 'none';

  try {
    const resp = await fetch(`${API_BASE}/api/admin/data/check_url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });

    if (!resp.ok) {
      setStatus('checkUrlStatus', 'Error checking URL', 'error');
      return;
    }

    const result = await resp.json();
    renderCheckUrlResults(result);
    setStatus('checkUrlStatus', 'Check complete', 'success');
  } catch (err) {
    setStatus('checkUrlStatus', `Error: ${err.message}`, 'error');
  }
}

function renderCheckUrlResults(result) {
  const resultsDiv = document.getElementById('checkUrlResults');
  if (!resultsDiv) return;

  resultsDiv.style.display = 'block';

  // Artifact status
  const artifactEl = document.getElementById('checkUrlArtifact');
  if (result.artifact && result.artifact.found) {
    const a = result.artifact.most_recent;
    artifactEl.innerHTML = `
      <div style="color: var(--success-color); font-weight: 600;">Found (${result.artifact.count} total)</div>
      <div style="font-size: 0.85rem; margin-top: 4px;">
        <strong>ID:</strong> ${escapeHtml(a.artifact_id)}<br>
        <strong>Title:</strong> ${escapeHtml(a.title || 'N/A')}<br>
        <strong>Status:</strong> ${a.status_code || 'N/A'}<br>
        <strong>Captured:</strong> ${a.captured_at ? new Date(a.captured_at).toLocaleString() : 'N/A'}<br>
        ${a.snippet ? `<div style="margin-top: 4px; padding: 4px; background: var(--bg-secondary); border-radius: 4px; font-family: monospace; font-size: 0.75rem; max-height: 100px; overflow-y: auto;">${escapeHtml(a.snippet)}</div>` : ''}
      </div>
    `;
  } else {
    artifactEl.innerHTML = '<div style="color: var(--text-secondary);">Not found</div>';
  }

  // Validation status
  const validationEl = document.getElementById('checkUrlValidation');
  if (result.validation && result.validation.found) {
    const findings = result.validation.findings || [];
    validationEl.innerHTML = `
      <div style="color: var(--warning-color); font-weight: 600;">Found ${findings.length} finding(s)</div>
      <div style="font-size: 0.85rem; margin-top: 4px;">
        ${findings.map(f => `• ${escapeHtml(f.message || f.reason || 'Unknown finding')} (${f.severity || 'unknown'})`).join('<br>')}
      </div>
    `;
  } else {
    validationEl.innerHTML = '<div style="color: var(--text-secondary);">No findings</div>';
  }

  // Ingest status
  const ingestEl = document.getElementById('checkUrlIngest');
  if (result.ingest && result.ingest.found) {
    const i = result.ingest;
    ingestEl.innerHTML = `
      <div style="color: var(--success-color); font-weight: 600;">Found</div>
      <div style="font-size: 0.85rem; margin-top: 4px;">
        <strong>Doc ID:</strong> ${escapeHtml(i.doc_id)}<br>
        <strong>Chunks:</strong> ${i.chunk_count || 0}<br>
        <strong>Ingested:</strong> ${i.ingested_at ? new Date(i.ingested_at).toLocaleString() : 'N/A'}
      </div>
    `;
  } else {
    ingestEl.innerHTML = '<div style="color: var(--text-secondary);">Not found</div>';
  }

  // Qdrant status
  const qdrantEl = document.getElementById('checkUrlQdrant');
  if (result.qdrant && result.qdrant.found) {
    const q = result.qdrant;
    const chunks = q.example_chunks || [];
    qdrantEl.innerHTML = `
      <div style="color: var(--success-color); font-weight: 600;">Found ${q.points_count || 0} points</div>
      ${chunks.length ? `
        <div style="font-size: 0.85rem; margin-top: 4px;">
          <strong>Example chunks:</strong>
          ${chunks.map(c => `<div style="margin-top: 4px; padding: 4px; background: var(--bg-secondary); border-radius: 4px; font-size: 0.75rem;">${escapeHtml(c.text || '')}</div>`).join('')}
        </div>
      ` : ''}
    `;
  } else {
    qdrantEl.innerHTML = '<div style="color: var(--text-secondary);">Not found</div>';
  }
}

async function searchData() {
  const input = document.getElementById('searchDataInput');
  const query = input?.value.trim();
  if (!query) {
    setStatus('searchDataStatus', 'Please enter a search term', 'error');
    return;
  }

  setStatus('searchDataStatus', 'Searching...');
  document.getElementById('searchDataResults').style.display = 'none';

  try {
    const resp = await fetch(`${API_BASE}/api/admin/data/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, limit: 10 })
    });

    if (!resp.ok) {
      setStatus('searchDataStatus', 'Error searching', 'error');
      return;
    }

    const result = await resp.json();
    renderSearchResults(result);
    setStatus('searchDataStatus', `Found ${result.artifacts?.length || 0} artifact matches, ${result.qdrant?.length || 0} Qdrant matches`, 'success');
  } catch (err) {
    setStatus('searchDataStatus', `Error: ${err.message}`, 'error');
  }
}

function renderSearchResults(result) {
  const resultsDiv = document.getElementById('searchDataResults');
  if (!resultsDiv) return;

  resultsDiv.style.display = 'block';

  // Artifact matches
  const artifactResults = document.getElementById('searchArtifactResults');
  const artifacts = result.artifacts || [];
  if (artifacts.length) {
    artifactResults.innerHTML = artifacts.map(a => `
      <div class="validation-row" style="padding: 8px;">
        <div style="font-weight: 600; font-size: 0.85rem;">${escapeHtml(a.title || a.url || 'Unknown')}</div>
        <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 2px;">${escapeHtml(a.url || '')}</div>
        <div style="font-size: 0.75rem; margin-top: 4px; padding: 4px; background: var(--bg-secondary); border-radius: 4px; font-family: monospace; max-height: 60px; overflow-y: auto;">
          ${escapeHtml(a.snippet || '')}
        </div>
      </div>
    `).join('');
  } else {
    artifactResults.innerHTML = '<div class="status-text">No artifact matches found.</div>';
  }

  // Qdrant matches
  const qdrantResults = document.getElementById('searchQdrantResults');
  const qdrant = result.qdrant || [];
  if (qdrant.length && !qdrant.error) {
    qdrantResults.innerHTML = qdrant.map(q => `
      <div class="validation-row" style="padding: 8px;">
        <div style="font-weight: 600; font-size: 0.85rem;">Score: ${(q.score || 0).toFixed(3)}</div>
        <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 2px;">${escapeHtml(q.url || 'Unknown URL')}</div>
        <div style="font-size: 0.75rem; margin-top: 4px; padding: 4px; background: var(--bg-secondary); border-radius: 4px; max-height: 60px; overflow-y: auto;">
          ${escapeHtml(q.text || '')}
        </div>
      </div>
    `).join('');
  } else if (qdrant.error) {
    qdrantResults.innerHTML = `<div class="status-text" style="color: var(--error-color);">Error: ${escapeHtml(qdrant.error)}</div>`;
  } else {
    qdrantResults.innerHTML = '<div class="status-text">No Qdrant matches found.</div>';
  }
}

// Validation & Quarantine functions
let validationState = {
  allItems: [],
  lowerPriorityExpanded: false,
  lowerPriorityPageSize: 10,
  lowerPriorityFilter: 'all' // 'all', 'medium', 'low'
};

async function loadDataValidation() {
  const statusTarget = 'dataValidationStatus';
  setStatus(statusTarget, 'Loading validation summary...');
  try {
    const resp = await fetch(`${API_BASE}/api/admin/validate/crawl/summary`);
    if (!resp.ok) {
      if (resp.status === 404) {
        setStatus(statusTarget, 'No crawl validation summary yet. Run validation to generate one.', 'error');
        renderValidationSummary({});
        renderValidationList([]);
        return;
      }
      const message = await resp.text();
      setStatus(statusTarget, message || 'Error loading validation summary', 'error');
      return;
    }
    const payload = await resp.json();
    renderValidationSummary(payload.summary);
    renderValidationList(payload.validated || []);
    setStatus(statusTarget, 'Loaded', 'success');
  } catch (err) {
    setStatus(statusTarget, `Error: ${err.message}`, 'error');
  }
}

async function validateArtifacts() {
  const statusTarget = 'dataValidationStatus';
  setStatus(statusTarget, 'Validating artifacts...');
  disableButtons(['validateArtifactsBtn', 'quarantineSelectedBtn']);
  try {
    const resp = await fetch(`${API_BASE}/api/admin/validate/crawl`, { method: 'POST' });
    if (!resp.ok) {
      const message = await resp.text();
      setStatus(statusTarget, message || 'Validation failed', 'error');
      enableButtons(['validateArtifactsBtn', 'quarantineSelectedBtn']);
      return;
    }
    const result = await resp.json();
    renderValidationSummary(result.summary);
    renderValidationList(result.validated || []);
    setStatus(statusTarget, 'Validation complete', 'success');
  } catch (err) {
    setStatus(statusTarget, `Error: ${err.message}`, 'error');
  } finally {
    enableButtons(['validateArtifactsBtn', 'quarantineSelectedBtn']);
  }
}

function renderValidationSummary(summary = {}) {
  const el = document.getElementById('validationSummary');
  if (!el) return;
  el.innerHTML = `
    <div>
      <div class="field-label">Total examined</div>
      <div class="info-value">${summary.total || 0}</div>
    </div>
    <div>
      <div class="field-label">Flagged</div>
      <div class="info-value">${summary.flagged || 0}</div>
    </div>
    <div>
      <div class="field-label">Quarantined</div>
      <div class="info-value">${summary.quarantined || 0}</div>
    </div>
  `;
}

function renderValidationList(list = []) {
  const container = document.getElementById('validationList');
  if (!container) return;
  container.innerHTML = '';

  // Store the list for state management
  validationState.allItems = list;

  // Load UI state from localStorage
  const storedExpanded = localStorage.getItem('dataTab.lowerPriority.expanded');
  const storedPageSize = localStorage.getItem('dataTab.lowerPriority.pageSize');
  if (storedExpanded !== null) {
    validationState.lowerPriorityExpanded = storedExpanded === 'true';
  }
  if (storedPageSize !== null) {
    validationState.lowerPriorityPageSize = parseInt(storedPageSize, 10) || 10;
  }

  if (!list.length) {
    container.innerHTML = '<div class="status-text">No flagged artifacts.</div>';
    updateSelectedValidationCount();
    const selectAll = document.getElementById('selectAllValidation');
    if (selectAll) selectAll.checked = false;
    return;
  }

  // Sort items alphabetically by URL
  const sortedList = [...list].sort((a, b) => {
    const urlA = (a.url || '').toLowerCase();
    const urlB = (b.url || '').toLowerCase();
    if (urlA < urlB) return -1;
    if (urlA > urlB) return 1;
    // Same URL, sort by severity
    const severityOrder = { high: 3, medium: 2, low: 1 };
    const sevA = severityOrder[a.severity] || 0;
    const sevB = severityOrder[b.severity] || 0;
    return sevB - sevA;
  });

  // Split into high priority and lower priority
  const highPriority = sortedList.filter(item => {
    const severity = (item.severity || '').toLowerCase();
    const reason = (item.reason || '').toLowerCase();

    // High priority conditions
    if (severity === 'high') return true;
    if (reason.includes('login') || reason.includes('cas redirect')) return true;
    if (reason.includes('malformed_url')) return true;
    if (reason.includes('401') || reason.includes('403') || reason.includes('5')) return true;
    if (reason.includes('parser failed') || reason.includes('no content') || reason.includes('empty text')) return true;

    return false;
  });

  const lowerPriority = sortedList.filter(item => !highPriority.includes(item));

  // Render high priority items
  if (highPriority.length > 0) {
    const highSection = document.createElement('div');
    highSection.innerHTML = '<h5 style="margin: 16px 0 8px 0; color: var(--error-color);">High Priority</h5>';
    container.appendChild(highSection);

    highPriority.forEach(item => {
      container.appendChild(createValidationRow(item));
    });
  }

  // Render lower priority items (collapsible)
  if (lowerPriority.length > 0) {
    const mediumCount = lowerPriority.filter(i => (i.severity || '').toLowerCase() === 'medium').length;
    const lowCount = lowerPriority.filter(i => (i.severity || '').toLowerCase() === 'low').length;

    const lowerSection = document.createElement('div');
    lowerSection.style.marginTop = '24px';

    const header = document.createElement('div');
    header.style.cssText = 'display: flex; justify-content: space-between; align-items: center; cursor: pointer; padding: 8px 0;';
    header.innerHTML = `
      <h5 style="margin: 0; color: var(--text-secondary);">
        Lower Priority Findings (Medium: ${mediumCount}, Low: ${lowCount})
        <span style="font-size: 0.85rem; margin-left: 8px;">${validationState.lowerPriorityExpanded ? '▼' : '▶'}</span>
      </h5>
    `;
    header.addEventListener('click', () => {
      validationState.lowerPriorityExpanded = !validationState.lowerPriorityExpanded;
      localStorage.setItem('dataTab.lowerPriority.expanded', validationState.lowerPriorityExpanded);
      renderValidationList(validationState.allItems);
    });
    lowerSection.appendChild(header);

    if (validationState.lowerPriorityExpanded) {
      // Filter controls
      const controls = document.createElement('div');
      controls.style.cssText = 'display: flex; gap: 12px; align-items: center; margin: 12px 0;';
      controls.innerHTML = `
        <div style="display: flex; gap: 6px;">
          <button class="btn btn-small ${validationState.lowerPriorityFilter === 'all' ? 'btn-primary' : ''}" data-filter="all">All</button>
          <button class="btn btn-small ${validationState.lowerPriorityFilter === 'medium' ? 'btn-primary' : ''}" data-filter="medium">Medium</button>
          <button class="btn btn-small ${validationState.lowerPriorityFilter === 'low' ? 'btn-primary' : ''}" data-filter="low">Low</button>
        </div>
        <div style="margin-left: auto; display: flex; gap: 6px; align-items: center;">
          <span style="font-size: 0.85rem;">Page size:</span>
          <button class="btn btn-small ${validationState.lowerPriorityPageSize === 10 ? 'btn-primary' : ''}" data-pagesize="10">10</button>
          <button class="btn btn-small ${validationState.lowerPriorityPageSize === 25 ? 'btn-primary' : ''}" data-pagesize="25">25</button>
          <button class="btn btn-small ${validationState.lowerPriorityPageSize === 50 ? 'btn-primary' : ''}" data-pagesize="50">50</button>
          <button class="btn btn-small ${validationState.lowerPriorityPageSize === 100 ? 'btn-primary' : ''}" data-pagesize="100">100</button>
        </div>
      `;
      lowerSection.appendChild(controls);

      // Wire up filter buttons
      controls.querySelectorAll('[data-filter]').forEach(btn => {
        btn.addEventListener('click', () => {
          validationState.lowerPriorityFilter = btn.dataset.filter;
          renderValidationList(validationState.allItems);
        });
      });

      // Wire up page size buttons
      controls.querySelectorAll('[data-pagesize]').forEach(btn => {
        btn.addEventListener('click', () => {
          validationState.lowerPriorityPageSize = parseInt(btn.dataset.pagesize, 10);
          localStorage.setItem('dataTab.lowerPriority.pageSize', validationState.lowerPriorityPageSize);
          renderValidationList(validationState.allItems);
        });
      });

      // Filter items
      let filteredItems = lowerPriority;
      if (validationState.lowerPriorityFilter === 'medium') {
        filteredItems = lowerPriority.filter(i => (i.severity || '').toLowerCase() === 'medium');
      } else if (validationState.lowerPriorityFilter === 'low') {
        filteredItems = lowerPriority.filter(i => (i.severity || '').toLowerCase() === 'low');
      }

      // Paginate
      const pageSize = validationState.lowerPriorityPageSize;
      const displayedItems = filteredItems.slice(0, pageSize);

      const itemsContainer = document.createElement('div');
      displayedItems.forEach(item => {
        itemsContainer.appendChild(createValidationRow(item));
      });
      lowerSection.appendChild(itemsContainer);

      // Show "showing X of Y" message
      if (filteredItems.length > displayedItems.length) {
        const pagInfo = document.createElement('div');
        pagInfo.style.cssText = 'margin-top: 12px; font-size: 0.85rem; color: var(--text-secondary); text-align: center;';
        pagInfo.textContent = `Showing ${displayedItems.length} of ${filteredItems.length} items`;
        lowerSection.appendChild(pagInfo);
      }
    }

    container.appendChild(lowerSection);
  }

  // Wire up checkboxes
  container.querySelectorAll('.validation-checkbox').forEach(cb => {
    cb.addEventListener('change', updateSelectedValidationCount);
  });
  const selectAll = document.getElementById('selectAllValidation');
  if (selectAll) selectAll.checked = false;
  updateSelectedValidationCount();
}

function createValidationRow(item) {
  const row = document.createElement('div');
  row.className = 'validation-row';
  row.innerHTML = `
    <div style="display:flex; gap:12px; align-items:center;">
      <input type="checkbox" class="validation-checkbox" data-artifact-id="${item.id}" />
      <div style="flex:1">
        <div style="font-weight:600;">${escapeHtml(item.url || item.title || item.id)}</div>
        <div style="font-size:0.85rem;color:var(--text-secondary);margin-top:2px;">${escapeHtml(item.reason || '')}</div>
      </div>
      <div style="min-width:140px; text-align:right;">
        <div style="font-weight:600; text-transform: uppercase; color: ${getSeverityColor(item.severity)};">${item.severity || '—'}</div>
        <div class="actions-row" style="margin-top:6px;">
          <button class="btn btn-small" data-action="quarantine" data-id="${item.id}">Quarantine</button>
        </div>
      </div>
    </div>
  `;

  // Wire up quarantine button
  const quarantineBtn = row.querySelector('button[data-action="quarantine"]');
  if (quarantineBtn) {
    quarantineBtn.addEventListener('click', async () => {
      await quarantineArtifacts([item.id]);
    });
  }

  return row;
}

function getSeverityColor(severity) {
  const s = (severity || '').toLowerCase();
  if (s === 'high') return 'var(--error-color)';
  if (s === 'medium') return 'var(--warning-color)';
  if (s === 'low') return 'var(--text-secondary)';
  return 'var(--text-secondary)';
}

function updateSelectedValidationCount() {
  const checkboxes = Array.from(document.querySelectorAll('.validation-checkbox'));
  const selected = checkboxes.filter(cb => cb.checked).length;
  const countEl = document.getElementById('selectedValidationCount');
  if (countEl) {
    countEl.textContent = selected ? `${selected} selected` : '';
  }
  const selectAll = document.getElementById('selectAllValidation');
  if (selectAll && checkboxes.length) {
    selectAll.checked = selected === checkboxes.length;
  }
}

async function quarantineArtifacts(ids = []) {
  if (!ids.length) return;
  const statusTarget = 'dataValidationStatus';
  setStatus(statusTarget, `Quarantining ${ids.length} artifact(s)...`);
  try {
    const resp = await fetch(`${API_BASE}/api/admin/quarantine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids })
    });
    if (!resp.ok) {
      setStatus(statusTarget, 'Error while quarantining', 'error');
      return;
    }
    const result = await resp.json();
    // assume server returns list of quarantined ids
    const quarantined = result.quarantined || ids;
    // update UI rows
    quarantined.forEach(id => {
      const checkbox = document.querySelector(`.validation-checkbox[data-artifact-id="${id}"]`);
      if (checkbox) {
        const row = checkbox.closest('.validation-row');
        if (row) {
          row.style.opacity = '0.5';
          const tag = document.createElement('span');
          tag.className = 'status-pill';
          tag.textContent = 'Quarantined';
          row.appendChild(tag);
        }
      }
    });
    setStatus(statusTarget, `Quarantined ${quarantined.length} artifact(s)`, 'success');
    // refresh summary
    await loadDataValidation();
  } catch (err) {
    setStatus(statusTarget, `Error: ${err.message}`, 'error');
  }
}

// Ingest validation
async function loadIngestValidation() {
  const statusTarget = 'ingestValidationStatus';
  setStatus(statusTarget, 'Loading ingest validation summary...');
  try {
    const resp = await fetch(`${API_BASE}/api/admin/validate/ingest/summary`);
    if (!resp.ok) {
      if (resp.status === 404) {
        setStatus(statusTarget, 'No ingest validation summary yet. Run validation to generate one.', 'error');
        renderIngestValidationSummary({});
        renderIngestValidationFindings([]);
        return;
      }
      const message = await resp.text();
      setStatus(statusTarget, message || 'Error loading ingest validation summary', 'error');
      return;
    }
    const payload = await resp.json();
    renderIngestValidationSummary(payload.summary);
    renderIngestValidationFindings(payload.findings || []);
    setStatus(statusTarget, 'Loaded', 'success');
  } catch (err) {
    setStatus(statusTarget, `Error: ${err.message}`, 'error');
  }
}

async function validateIngest() {
  const statusTarget = 'ingestValidationStatus';
  setStatus(statusTarget, 'Validating ingest output...');
  disableButtons(['validateIngestBtn']);
  try {
    const resp = await fetch(`${API_BASE}/api/admin/validate/ingest`, { method: 'POST' });
    if (!resp.ok) {
      const message = await resp.text();
      setStatus(statusTarget, message || 'Validation failed', 'error');
      return;
    }
    const payload = await resp.json();
    renderIngestValidationSummary(payload.summary);
    renderIngestValidationFindings(payload.findings || []);
    setStatus(statusTarget, 'Validation complete', 'success');
  } catch (err) {
    setStatus(statusTarget, `Error: ${err.message}`, 'error');
  } finally {
    enableButtons(['validateIngestBtn']);
  }
}

function renderIngestValidationSummary(summary = {}) {
  const el = document.getElementById('ingestValidationSummary');
  if (!el) return;
  el.innerHTML = `
    <div>
      <div class="field-label">Total findings</div>
      <div class="info-value">${summary.total || 0}</div>
    </div>
    <div>
      <div class="field-label">High</div>
      <div class="info-value">${summary.high || 0}</div>
    </div>
    <div>
      <div class="field-label">Medium</div>
      <div class="info-value">${summary.medium || 0}</div>
    </div>
    <div>
      <div class="field-label">Low</div>
      <div class="info-value">${summary.low || 0}</div>
    </div>
  `;
}

function renderIngestValidationFindings(findings = []) {
  const container = document.getElementById('ingestValidationFindings');
  if (!container) return;
  if (!findings.length) {
    container.innerHTML = '<div class="status-text">No ingest findings reported.</div>';
    return;
  }
  container.innerHTML = findings
    .map((finding) => `
      <div class="validation-row">
        <div style="display:flex; justify-content:space-between; gap:12px;">
          <div>
            <div style="font-weight:600;">${escapeHtml(finding.code || 'Finding')}</div>
            <div style="font-size:0.85rem;color:var(--text-secondary)">${escapeHtml(finding.message || '')}</div>
            <div style="font-size:0.8rem;color:var(--text-secondary)">${escapeHtml(finding.evidence || '')}</div>
          </div>
          <div style="min-width:120px; text-align:right;">
            <div style="font-weight:600">${escapeHtml(finding.severity || '—')}</div>
          </div>
        </div>
      </div>
    `)
    .join('');
}

/* Ingest Job Functions (Redis Queue) */
let currentIngestJobEventSource = null;
let ingestProgressTimestamps = [];
let ingestProgressLastTime = null;

async function startIngestJob() {
  const statusTarget = 'ingestJobStatus';
  setStatus(statusTarget, 'Starting ingest job...');
  disableButtons(['startIngestJobBtn']);

  try {
    const resp = await fetch(`${API_BASE}/api/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        artifact_paths: [],  // Empty means process all artifacts
        chunks_estimate: 0,
        meta: {}
      })
    });

    if (!resp.ok) {
      setStatus(statusTarget, 'Failed to start ingest job', 'error');
      enableButtons(['startIngestJobBtn']);
      return;
    }

    const data = await resp.json();
    const jobId = data.job_id;
    currentIngestJobId = jobId;
    resetIngestProgressUi();

    setStatus(statusTarget, `Job ${jobId} started`, 'success');

    // Start listening to events
    listenIngestJobEvents(jobId);

    // Poll for status updates
    pollIngestJobStatus(jobId);

  } catch (err) {
    setStatus(statusTarget, `Error: ${err.message}`, 'error');
    enableButtons(['startIngestJobBtn']);
  }
}

function listenIngestJobEvents(jobId) {
  // Close existing connection
  if (currentIngestJobEventSource) {
    currentIngestJobEventSource.close();
  }

  const eventSource = new EventSource(`${API_BASE}/api/ingest/${jobId}/events`);
  currentIngestJobEventSource = eventSource;

  eventSource.onmessage = function(event) {
    try {
      const payload = JSON.parse(event.data);

      if (payload.type === 'start') {
        updateIngestProgress(0, payload.total_artifacts || 0, '-', 0);
        setStatus('ingestJobStatus', 'Ingest running', 'success');
      } else if (payload.type === 'artifact_progress') {
        updateIngestProgress(
          payload.done_artifacts || 0,
          payload.total_artifacts || 0,
          payload.current_artifact || '-',
          payload.errors || 0
        );
      } else if (payload.type === 'log') {
        appendIngestLog(`[${payload.level || 'info'}] ${payload.message || ''}`);
      } else if (payload.type === 'complete') {
        appendIngestLog(`✓ ${payload.msg || 'Ingest complete'}`);
        updateIngestSummary(payload);
        setStatus('ingestJobStatus', 'Ingest complete', 'success');
        eventSource.close();
        enableButtons(['startIngestJobBtn']);
      } else if (payload.type === 'error') {
        appendIngestLog(`✗ ERROR: ${payload.msg || 'Unknown error'}`);
        incrementIngestErrorCount();
        setStatus('ingestJobStatus', 'Ingest error reported', 'error');
      } else if (payload.type === 'connected') {
        appendIngestLog(`Connected to job ${jobId}`);
      }
    } catch (err) {
      appendIngestLog(`Malformed event: ${event.data}`);
    }
  };

  eventSource.onerror = function(event) {
    appendIngestLog('Event stream error or closed');
    eventSource.close();
    enableButtons(['startIngestJobBtn']);
    setStatus('ingestJobStatus', 'Event stream disconnected - polling status', 'error');
  };
}

async function pollIngestJobStatus(jobId) {
  try {
    const resp = await fetch(`${API_BASE}/api/ingest/${jobId}`);
    if (!resp.ok) return;

    const info = await resp.json();
    const done = parseInt(info.done_artifacts || info.done || 0, 10);
    const total = parseInt(info.total_artifacts || info.total || 0, 10);
    updateIngestProgress(done, total, info.current_artifact || '-', info.errors || 0);

    // Keep polling if job is still running
    if (info.status && !['done', 'error', 'cancelled'].includes(info.status)) {
      setTimeout(() => pollIngestJobStatus(jobId), 2000);
    }
  } catch (err) {
    // Ignore polling errors
  }
}

function updateIngestProgress(done, total, currentArtifact, errors) {
  const progressBar = document.getElementById('ingestProgressBarFill');
  const progressText = document.getElementById('ingestJobProgress');
  const etaText = document.getElementById('ingestEta');
  const currentText = document.getElementById('ingestCurrentArtifact');
  const errorText = document.getElementById('ingestErrorCount');
  const container = document.getElementById('ingestProgressContainer');

  if (container) {
    container.style.display = 'block';
  }

  if (progressText) {
    progressText.textContent = `${done} / ${total || 0}`;
  }
  if (progressBar) {
    const pct = total ? Math.round((done / total) * 100) : 0;
    progressBar.style.width = `${pct}%`;
  }
  if (currentText) {
    currentText.textContent = currentArtifact || '-';
  }
  if (errorText) {
    errorText.textContent = errors || 0;
  }

  const now = Date.now();
  if (done > 0) {
    if (ingestProgressLastTime) {
      ingestProgressTimestamps.push(now - ingestProgressLastTime);
      if (ingestProgressTimestamps.length > 10) {
        ingestProgressTimestamps.shift();
      }
    }
    ingestProgressLastTime = now;
  }
  if (etaText) {
    if (ingestProgressTimestamps.length >= 5 && total > done) {
      const avg = ingestProgressTimestamps.reduce((a, b) => a + b, 0) / ingestProgressTimestamps.length;
      const etaMs = avg * (total - done);
      etaText.textContent = formatDuration(etaMs);
    } else if (done === total && total > 0) {
      etaText.textContent = 'Complete';
    } else {
      etaText.textContent = 'Calculating…';
    }
  }
}

function updateIngestSummary(payload) {
  const summaryCard = document.getElementById('ingestSummaryCard');
  const summaryContent = document.getElementById('ingestSummaryContent');
  if (!summaryCard || !summaryContent) return;
  const artifacts = payload.total_artifacts || 0;
  const documents = payload.total_documents || 0;
  const chunks = payload.total_chunks || 0;
  const errors = payload.errors || 0;
  summaryContent.innerHTML = `
    <div class="info-grid">
      <div>
        <div class="field-label">Artifacts</div>
        <div class="info-value">${artifacts}</div>
      </div>
      <div>
        <div class="field-label">Documents</div>
        <div class="info-value">${documents}</div>
      </div>
      <div>
        <div class="field-label">Chunks</div>
        <div class="info-value">${chunks}</div>
      </div>
      <div>
        <div class="field-label">Errors</div>
        <div class="info-value">${errors}</div>
      </div>
    </div>
  `;
  summaryCard.style.display = 'block';
}

function incrementIngestErrorCount() {
  const errorText = document.getElementById('ingestErrorCount');
  if (!errorText) return;
  const current = parseInt(errorText.textContent || '0', 10);
  errorText.textContent = current + 1;
}

function appendIngestLog(msg) {
  const logArea = document.getElementById('ingestQueueLog');
  if (!logArea) return;

  const timestamp = new Date().toLocaleTimeString();
  logArea.textContent += `[${timestamp}] ${msg}\n`;
  logArea.scrollTop = logArea.scrollHeight;
}

async function refreshIngestJobStatus() {
  const jobId = currentIngestJobId;
  if (!jobId || jobId === '-') {
    setStatus('ingestJobStatus', 'No active job', 'error');
    return;
  }

  setStatus('ingestJobStatus', 'Refreshing...');

  try {
    const resp = await fetch(`${API_BASE}/api/ingest/${jobId}`);
    if (!resp.ok) {
      setStatus('ingestJobStatus', 'Job not found', 'error');
      return;
    }

    const info = await resp.json();
    updateIngestProgress(
      parseInt(info.done_artifacts || info.done || 0, 10),
      parseInt(info.total_artifacts || info.total || 0, 10),
      info.current_artifact || '-',
      info.errors || 0
    );
    setStatus('ingestJobStatus', 'Status refreshed', 'success');
  } catch (err) {
    setStatus('ingestJobStatus', `Error: ${err.message}`, 'error');
  }
}

function clearIngestLog() {
  const logArea = document.getElementById('ingestQueueLog');
  if (logArea) logArea.textContent = '';
}

function resetIngestProgressUi() {
  const container = document.getElementById('ingestProgressContainer');
  if (container) container.style.display = 'block';
  const progressBar = document.getElementById('ingestProgressBarFill');
  if (progressBar) progressBar.style.width = '0%';
  const progressText = document.getElementById('ingestJobProgress');
  if (progressText) progressText.textContent = '0 / 0';
  const etaText = document.getElementById('ingestEta');
  if (etaText) etaText.textContent = 'Calculating…';
  const currentText = document.getElementById('ingestCurrentArtifact');
  if (currentText) currentText.textContent = '-';
  const errorText = document.getElementById('ingestErrorCount');
  if (errorText) errorText.textContent = '0';
  const summaryCard = document.getElementById('ingestSummaryCard');
  if (summaryCard) summaryCard.style.display = 'none';
  ingestProgressTimestamps = [];
  ingestProgressLastTime = null;
  clearIngestLog();
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

/* Helper functions */
function disableButtons(ids) {
  ids.forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.setAttribute('disabled', 'disabled');
  });
}

function enableButtons(ids) {
  ids.forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.removeAttribute('disabled');
  });
}

function escapeHtml(s = '') {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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

document.getElementById('triggerIngestLegacy')?.addEventListener('click', () => triggerJob('ingest'));

document.getElementById('savePrompts').addEventListener('click', savePrompts);

document.getElementById('exportCrawlLog').addEventListener('click', () => {
  exportCurrentLog(currentCrawlJobId, 'crawlLogStatus');
});

document.getElementById('deleteCrawlLog').addEventListener('click', () => {
  deleteCurrentLog(currentCrawlJobId, 'crawlLogStatus', 'crawl', 'crawlLog');
  currentCrawlJobId = null;
});

document.getElementById('clearVectors')?.addEventListener('click', clearVectors);
document.getElementById('clearVectorsNew')?.addEventListener('click', clearVectors);
document.getElementById('resetCrawl')?.addEventListener('click', resetCrawl);
document.getElementById('resetIngest')?.addEventListener('click', resetIngest);
document.getElementById('resetAllData')?.addEventListener('click', resetAllData);

document.getElementById('exportJobLog').addEventListener('click', () => {
  exportCurrentLog(currentJobLogId, 'jobLogStatus');
});

document.getElementById('deleteJobLog').addEventListener('click', () => {
  deleteCurrentLog(currentJobLogId, 'jobLogStatus', 'jobs', 'jobLog');
  currentJobLogId = null;
});

document.getElementById('addAuthProfileBtn')?.addEventListener('click', () => {
  showAuthProfileEditor();
});

document.getElementById('saveProfileBtn')?.addEventListener('click', () => {
  saveAuthProfile();
});

document.getElementById('cancelProfileEdit')?.addEventListener('click', () => {
  hideAuthProfileEditor();
});

document.getElementById('migrateLegacyBtn')?.addEventListener('click', async () => {
  await migrateLegacyPlaywrightSettings();
});

document.getElementById('saveAuthConfigBtn')?.addEventListener('click', saveAuthConfig);

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

// Wire up Pipeline Health event listeners
document.getElementById('refreshHealthBtn')?.addEventListener('click', loadPipelineHealth);

// Wire up Check Data event listeners
document.getElementById('checkUrlBtn')?.addEventListener('click', checkUrl);
document.getElementById('checkUrlInput')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    checkUrl();
  }
});
document.getElementById('searchDataBtn')?.addEventListener('click', searchData);
document.getElementById('searchDataInput')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    searchData();
  }
});

// Wire up validation & quarantine event listeners
document.getElementById('validateArtifactsBtn')?.addEventListener('click', validateArtifacts);
document.getElementById('refreshValidationBtn')?.addEventListener('click', loadDataValidation);
document.getElementById('quarantineSelectedBtn')?.addEventListener('click', async () => {
  const checkboxes = Array.from(document.querySelectorAll('.validation-checkbox:checked'));
  const ids = checkboxes.map(cb => cb.dataset.artifactId);
  if (!ids.length) return setStatus('dataValidationStatus', 'No artifacts selected', 'error');
  await quarantineArtifacts(ids);
});
document.getElementById('selectAllValidation')?.addEventListener('change', (event) => {
  const isChecked = event.target.checked;
  document.querySelectorAll('.validation-checkbox').forEach(cb => {
    cb.checked = isChecked;
  });
  updateSelectedValidationCount();
});
document.getElementById('validateIngestBtn')?.addEventListener('click', validateIngest);
document.getElementById('refreshIngestValidationBtn')?.addEventListener('click', loadIngestValidation);

// Wire up ingest job event listeners
document.getElementById('startIngestJobBtn')?.addEventListener('click', startIngestJob);
document.getElementById('refreshIngestJobBtn')?.addEventListener('click', refreshIngestJobStatus);
document.getElementById('clearIngestLogBtn')?.addEventListener('click', clearIngestLog);
document.getElementById('saveIngestPerformance')?.addEventListener('click', saveIngestPerformanceSettings);

window.loadAdminData = () => {
  loadConfigs();
  loadJobs();
  loadDataValidation();
  loadIngestValidation();
  loadIngestWorkerStatus();
};

window.resetAdminSession = () => {
  closeStream('crawl');
  closeStream('ingest');
  closeStream('jobs');
  currentCrawlJobId = null;
  currentIngestJobId = null;
  currentJobLogId = null;
  if (ingestWorkerPoller) {
    clearTimeout(ingestWorkerPoller);
    ingestWorkerPoller = null;
  }

  // Close ingest job event source
  if (currentIngestJobEventSource) {
    currentIngestJobEventSource.close();
    currentIngestJobEventSource = null;
  }

  setStatus('saveCrawlStatus', '');
  setStatus('crawlLogStatus', '');
  setStatus('ingestLogStatus', '');
  setStatus('ingestJobStatus', '');
  setStatus('clearVectorsStatus', '');
  setStatus('savePromptsStatus', '');
  setStatus('jobLogStatus', '');
  const crawlLog = document.getElementById('crawlLog');
  const ingestLog = document.getElementById('ingestQueueLog');
  const jobLog = document.getElementById('jobLog');
  const summaryPanel = document.getElementById('crawlSummary');
  const pillSummary = document.getElementById('crawlPillSummary');
  const ingestProgressContainer = document.getElementById('ingestProgressContainer');
  const ingestSummaryCard = document.getElementById('ingestSummaryCard');
  if (crawlLog) crawlLog.textContent = '';
  if (ingestLog) ingestLog.textContent = '';
  if (jobLog) jobLog.textContent = '';
  if (summaryPanel) summaryPanel.style.display = 'none';
  if (pillSummary) pillSummary.style.display = 'none';
  if (ingestProgressContainer) ingestProgressContainer.style.display = 'none';
  if (ingestSummaryCard) ingestSummaryCard.style.display = 'none';
};
