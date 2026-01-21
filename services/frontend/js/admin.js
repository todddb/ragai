let currentCrawlJobId = null;
let currentIngestJobId = null;
let currentJobLogId = null;
let cachedCrawlerConfig = {};
let authProfiles = [];
let authHints = { by_domain: {}, recent: [] };
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
      pattern: '',
      match: 'prefix',
      types: getTypeDefaults(),
      playwright: false,
      allow_http: false,
      auth_profile: null
    };
  }
  if (typeof rule === 'string') {
    return {
      pattern: rule,
      match: 'prefix',
      types: getTypeDefaults(),
      playwright: false,
      allow_http: false,
      auth_profile: null
    };
  }
  return {
    pattern: rule.pattern || '',
    match: rule.match || 'prefix',
    types: normalizeTypes(rule.types),
    playwright: Boolean(rule.playwright),
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

  crawlState.seeds.forEach((seedObj, index) => {
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
    <div>Auth</div>
    <div>Auth Profile</div>
    <div>Web</div>
    <div>PDF</div>
    <div>DOCX</div>
    <div>XLSX</div>
    <div>PPTX</div>
    <div>Allow HTTP</div>
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

    const authHint = getAuthHintForRule(rule);
    const authCell = document.createElement('div');
    if (authHint) {
      const badge = document.createElement('span');
      badge.className = 'auth-hint';
      badge.textContent = '🔒 Auth likely';
      badge.title = authHint.tooltip || 'Crawl attempts redirected to login.';
      authCell.appendChild(badge);
    }

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
      select.value = rule.auth_profile || '';
      select.addEventListener('change', () => {
        rule.auth_profile = select.value || null;
      });
      authProfileCell = select;
    } else {
      const label = document.createElement('span');
      label.className = 'auth-profile-label';
      label.textContent = rule.auth_profile || 'None';
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

    row.append(
      urlCell,
      matchCell,
      authCell,
      authProfileCell,
      ...checkboxes,
      allowHttpCell,
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
          playwright: false,
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

function ruleMatchesUrl(rule, url) {
  if (!rule || !url) return false;
  if (rule.match === 'exact') {
    return rule.pattern === url;
  }
  return url.startsWith(rule.pattern);
}

function getAuthHintForRule(rule) {
  const recentHints = authHints?.recent || [];
  const matchHint = recentHints.find((hint) => ruleMatchesUrl(rule, hint.original_url || ''));
  if (matchHint) {
    return {
      tooltip: `Crawl attempts redirected to login (${matchHint.redirect_host || 'auth host'}).`
    };
  }
  const host = getRuleHost(rule.pattern || '');
  if (host && authHints?.by_domain?.[host]) {
    const domainHint = authHints.by_domain[host];
    return {
      tooltip: `Crawl attempts redirected to login (${domainHint.redirect_host || 'auth host'}).`
    };
  }
  return null;
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

function renderAuthProfilesList() {
  const list = document.getElementById('authProfilesList');
  if (!list) return;

  const playwright = cachedCrawlerConfig.playwright || {};
  const profiles = playwright.auth_profiles || {};
  const profileNames = Object.keys(profiles);

  if (profileNames.length === 0) {
    list.innerHTML = '<div style="padding: 12px; text-align: center; color: var(--text-secondary); font-size: 0.85rem;">No auth profiles configured. Click "+ Add Profile" to create one.</div>';
    return;
  }

  list.innerHTML = '';
  profileNames.forEach((name) => {
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

    const domainsEl = document.createElement('div');
    domainsEl.style.fontSize = '0.75rem';
    domainsEl.style.color = 'var(--text-tertiary)';
    const domains = profile.use_for_domains || [];
    domainsEl.textContent = domains.length > 0 ? `Domains: ${domains.join(', ')}` : 'No auto-apply domains';

    details.append(nameEl, pathEl, domainsEl);

    const actions = document.createElement('div');
    actions.className = 'list-actions';

    const editBtn = document.createElement('button');
    editBtn.className = 'icon-btn';
    editBtn.setAttribute('aria-label', 'Edit auth profile');
    editBtn.textContent = '✏️';
    editBtn.addEventListener('click', () => startEditAuthProfile(name));

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'icon-btn';
    deleteBtn.setAttribute('aria-label', 'Delete auth profile');
    deleteBtn.textContent = '🗑️';
    deleteBtn.addEventListener('click', () => deleteAuthProfile(name));

    actions.append(editBtn, deleteBtn);
    row.append(details, actions);
    list.appendChild(row);
  });

  renderAuthDiagnostics();
}

function renderAuthDiagnostics() {
  const diagnosticsEl = document.getElementById('authDiagnostics');
  if (!diagnosticsEl) return;

  const playwright = cachedCrawlerConfig.playwright || {};
  const profiles = playwright.auth_profiles || {};
  const profileNames = Object.keys(profiles);

  if (profileNames.length === 0) {
    diagnosticsEl.innerHTML = 'No profiles to diagnose.';
    return;
  }

  diagnosticsEl.innerHTML = '<div style="font-weight: 600; margin-bottom: 4px;">Storage State Files:</div>';

  profileNames.forEach((name) => {
    const profile = profiles[name];
    const path = profile.storage_state_path || '';
    const row = document.createElement('div');
    row.style.marginBottom = '2px';
    row.innerHTML = `<span style="font-weight: 500;">${name}:</span> <span style="font-family: monospace; font-size: 0.8rem;">${path}</span> <span style="color: var(--text-tertiary);">(validation requires backend support)</span>`;
    diagnosticsEl.appendChild(row);
  });
}

function showAuthProfileEditor(profileName = null) {
  const editor = document.getElementById('authProfileEditor');
  const title = document.getElementById('profileEditorTitle');
  const nameInput = document.getElementById('profileName');
  const pathInput = document.getElementById('profileStoragePath');
  const domainsInput = document.getElementById('profileUseDomains');
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
    domainsInput.value = (profile.use_for_domains || []).join('\n');
    editState.authProfile = profileName;
  } else {
    title.textContent = 'Add Auth Profile';
    nameInput.value = '';
    nameInput.disabled = false;
    pathInput.value = '';
    domainsInput.value = '';
    editState.authProfile = null;
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

function saveAuthProfile() {
  const nameInput = document.getElementById('profileName');
  const pathInput = document.getElementById('profileStoragePath');
  const domainsInput = document.getElementById('profileUseDomains');
  const status = document.getElementById('profileEditorStatus');

  const name = nameInput.value.trim();
  const path = pathInput.value.trim();
  const domainsText = domainsInput.value.trim();
  const domains = domainsText ? domainsText.split('\n').map(d => d.trim()).filter(Boolean) : [];

  if (!name) {
    setStatus('profileEditorStatus', 'Profile name is required', 'error');
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
    storage_state_path: path,
    use_for_domains: domains
  };

  // Update authProfiles array
  authProfiles = Object.keys(cachedCrawlerConfig.playwright.auth_profiles);

  hideAuthProfileEditor();
  renderAuthProfilesList();
  renderAllowTable();
  setStatus('profileEditorStatus', 'Profile saved (remember to click Save above to persist)', 'success');
}

function deleteAuthProfile(profileName) {
  if (!confirm(`Delete auth profile "${profileName}"? This cannot be undone.`)) {
    return;
  }

  const playwright = cachedCrawlerConfig.playwright || {};
  const profiles = playwright.auth_profiles || {};

  if (profiles[profileName]) {
    delete profiles[profileName];
    authProfiles = Object.keys(profiles);
    renderAuthProfilesList();
    renderAllowTable();
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

  if (!playwright.auth_profiles.default) {
    playwright.auth_profiles.default = {
      storage_state_path: playwright.storage_state_path || '',
      use_for_domains: playwright.use_for_domains || []
    };
  }

  // Remove legacy fields from the config object
  delete playwright.storage_state_path;
  delete playwright.use_for_domains;

  cachedCrawlerConfig.playwright = playwright;
  authProfiles = Object.keys(playwright.auth_profiles);

  // Hide migration banner
  const banner = document.getElementById('migrationBanner');
  if (banner) {
    banner.style.display = 'none';
  }

  renderAuthProfilesList();
  renderAllowTable();
  setStatus('saveAuthStatus', 'Legacy settings migrated to "default" profile. Click Save to persist.', 'success');
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
  authProfiles = Object.keys(playwright.auth_profiles || {});
  document.getElementById('playwrightEnabled').checked = Boolean(playwright.enabled);

  renderAuthProfilesList();
  checkForLegacySettings();
  await loadAuthHints();
  renderAllowTable();
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

async function loadAuthHints() {
  try {
    const response = await fetch(`${API_BASE}/api/admin/crawl/auth_hints`);
    if (!response.ok) {
      authHints = { by_domain: {}, recent: [] };
      return;
    }
    authHints = await response.json();
  } catch (error) {
    authHints = { by_domain: {}, recent: [] };
  }
}

async function saveCrawlConfig() {
  const allowPayload = {
    seed_urls: crawlState.seeds.map((seed) => {
      const normalized = normalizeSeed(seed);
      return {
        url: normalized.url,
        allow_http: normalized.allow_http
      };
    }),
    blocked_domains: crawlState.blocked,
    allow_rules: crawlState.allowRules.map((rule) => ({
      pattern: rule.pattern,
      match: rule.match || 'prefix',
      types: normalizeTypes(rule.types),
      playwright: Boolean(rule.playwright),
      allow_http: Boolean(rule.allow_http),
      auth_profile: rule.auth_profile || null
    })),
    allowed_domains: extractAllowedDomains(crawlState.allowRules)
  };
  const existingPlaywright = cachedCrawlerConfig.playwright || {};
  const playwrightPayload = {
    enabled: document.getElementById('playwrightEnabled').checked,
    auth_profiles: existingPlaywright.auth_profiles || {}
  };

  // Never write legacy fields (storage_state_path, use_for_domains)
  // Auth profiles are managed separately via the auth profiles UI

  if (playwrightPayload.headless === undefined) {
    playwrightPayload.headless = existingPlaywright.headless !== undefined ? existingPlaywright.headless : true;
  } else {
    playwrightPayload.headless = existingPlaywright.headless;
  }
  if (playwrightPayload.navigation_timeout_ms === undefined) {
    playwrightPayload.navigation_timeout_ms = existingPlaywright.navigation_timeout_ms || 60000;
  } else {
    playwrightPayload.navigation_timeout_ms = existingPlaywright.navigation_timeout_ms;
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

function addAllowFromInput() {
  const input = document.getElementById('allowAddInput');
  const match = document.getElementById('allowAddMatch');
  const allowHttpCheckbox = document.getElementById('allowAddAllowHttp');
  if (!input || !match) return;
  const allowHttp = allowHttpCheckbox ? allowHttpCheckbox.checked : false;
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
    crawlState.allowRules.push({
      pattern: value,
      match: match.value,
      types,
      playwright: false,
      allow_http: allowHttp,
      auth_profile: null
    });
    input.value = '';
    if (allowHttpCheckbox) allowHttpCheckbox.checked = false;
    renderAllowTable();
    renderRecommendations();
  } catch (e) {
    alert(e.message);
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
    await loadAuthHints();
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

document.getElementById('saveAuthConfigBtn')?.addEventListener('click', saveCrawlConfig);

document.getElementById('openCaptureInstructions')?.addEventListener('click', (e) => {
  e.preventDefault();
  alert('Capture Instructions:\n\n1. Use the tool at tools/wsl/capture_auth_state.py to capture auth state\n2. Run: python tools/wsl/capture_auth_state.py\n3. The tool will guide you through creating/updating auth profiles\n4. Storage state files are saved to secrets/playwright/\n5. Update your auth profile with the new storage state path\n\nSee the README in tools/wsl/ for more details.');
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
  const pillSummary = document.getElementById('crawlPillSummary');
  if (crawlLog) crawlLog.textContent = '';
  if (ingestLog) ingestLog.textContent = '';
  if (jobLog) jobLog.textContent = '';
  if (summaryPanel) summaryPanel.style.display = 'none';
  if (pillSummary) pillSummary.style.display = 'none';
};
