const apiBaseDisplay = document.getElementById('apiBaseDisplay');
const apiStatus = document.getElementById('apiStatus');
const ollamaStatus = document.getElementById('ollamaStatus');
const qdrantStatus = document.getElementById('qdrantStatus');
const ollamaModel = document.getElementById('ollamaModel');
const connectionMessage = document.getElementById('connectionMessage');
const testConnectionButton = document.getElementById('testConnection');
const toggleUser = document.getElementById('toggleUser');
const toggleAdmin = document.getElementById('toggleAdmin');
const adminPanel = document.getElementById('adminPanel');
const modeToggle = document.querySelector('.segmented-control');
const adminLockStatus = document.getElementById('adminLockStatus');
const lockAdminButton = document.getElementById('lockAdminButton');
const lockAdminInline = document.getElementById('lockAdminInline');
const unlockSection = document.getElementById('unlockSection');
const adminSection = document.getElementById('adminSection');

const ADMIN_UNLOCK_KEY = 'ADMIN_UNLOCKED';

const isAdminUnlocked = () => localStorage.getItem(ADMIN_UNLOCK_KEY) === 'true';

const updateAdminLockUi = (unlocked) => {
  if (adminLockStatus) {
    adminLockStatus.textContent = unlocked ? 'Admin unlocked' : 'Admin locked';
  }
  if (lockAdminButton) {
    lockAdminButton.disabled = !unlocked;
  }
  if (lockAdminInline) {
    lockAdminInline.disabled = !unlocked;
  }
  if (unlockSection) {
    unlockSection.style.display = unlocked ? 'none' : 'grid';
  }
  if (adminSection) {
    adminSection.style.display = unlocked ? 'block' : 'none';
  }
};

const setAdminUnlocked = (unlocked) => {
  if (unlocked) {
    localStorage.setItem(ADMIN_UNLOCK_KEY, 'true');
  } else {
    localStorage.removeItem(ADMIN_UNLOCK_KEY);
  }
  updateAdminLockUi(unlocked);
};

const setPillStatus = (element, ok, label) => {
  element.textContent = label;
  element.classList.remove('success', 'error');
  element.classList.add(ok ? 'success' : 'error');
};

const setConnectionMessage = (message, type = '') => {
  connectionMessage.textContent = message;
  connectionMessage.classList.remove('success', 'error');
  if (type) {
    connectionMessage.classList.add(type);
  }
};

const checkConnection = async () => {
  setConnectionMessage('Testing connectivity...');
  let apiOk = false;
  let apiError = '';

  try {
    const apiResponse = await fetch(`${API_BASE}/api/chat/list`);
    apiOk = apiResponse.ok;
    if (!apiOk) {
      apiError = `API responded with ${apiResponse.status}`;
    }
  } catch (error) {
    apiError = error instanceof Error ? error.message : 'API unreachable';
  }

  if (apiOk) {
    setPillStatus(apiStatus, true, '✅ Connected');
  } else {
    setPillStatus(apiStatus, false, '❌ Not reachable');
  }

  let healthPayload = null;
  if (apiOk) {
    try {
      const healthResponse = await fetch(`${API_BASE}/api/health`);
      if (healthResponse.ok) {
        healthPayload = await healthResponse.json();
      }
    } catch (error) {
      healthPayload = null;
    }
  }

  if (healthPayload && healthPayload.ollama === 'ok') {
    setPillStatus(ollamaStatus, true, '✅ Reachable');
  } else {
    setPillStatus(ollamaStatus, false, '❌ Not reachable');
  }

  if (healthPayload && healthPayload.qdrant === 'ok') {
    setPillStatus(qdrantStatus, true, '✅ Reachable');
  } else {
    setPillStatus(qdrantStatus, false, '❌ Not reachable');
  }

  ollamaModel.textContent = healthPayload?.model || '-';

  if (apiOk && healthPayload?.ollama === 'ok' && healthPayload?.qdrant === 'ok') {
    setConnectionMessage('✅ All services are healthy.', 'success');
  } else if (!apiOk) {
    setConnectionMessage(`❌ Cannot reach API at ${API_BASE}. ${apiError}`, 'error');
  } else if (healthPayload?.ollama !== 'ok' && healthPayload?.qdrant !== 'ok') {
    setConnectionMessage('❌ API reachable, but Ollama and Qdrant are not responding.', 'error');
  } else if (healthPayload?.ollama !== 'ok') {
    setConnectionMessage('❌ API reachable, but Ollama is not responding.', 'error');
  } else if (healthPayload?.qdrant !== 'ok') {
    setConnectionMessage('❌ API reachable, but Qdrant is not responding.', 'error');
  }
};

const setMode = (mode) => {
  const isAdmin = mode === 'admin';
  toggleUser.classList.toggle('active', !isAdmin);
  toggleUser.setAttribute('aria-selected', String(!isAdmin));
  toggleAdmin.classList.toggle('active', isAdmin);
  toggleAdmin.setAttribute('aria-selected', String(isAdmin));
  adminPanel.style.display = isAdmin ? 'block' : 'none';
  modeToggle?.classList.toggle('is-admin', isAdmin);
  if (isAdmin) {
    const unlocked = isAdminUnlocked();
    updateAdminLockUi(unlocked);
    if (unlocked && window.loadAdminData) {
      window.loadAdminData();
    }
  }
};

toggleUser.addEventListener('click', () => setMode('user'));
toggleAdmin.addEventListener('click', () => setMode('admin'));
lockAdminButton?.addEventListener('click', () => {
  setAdminUnlocked(false);
  if (window.resetAdminSession) {
    window.resetAdminSession();
  }
});
lockAdminInline?.addEventListener('click', () => {
  setAdminUnlocked(false);
  if (window.resetAdminSession) {
    window.resetAdminSession();
  }
});

window.onAdminUnlocked = () => {
  setAdminUnlocked(true);
  if (window.loadAdminData) {
    window.loadAdminData();
  }
};

window.setAdminUnlocked = (value) => {
  setAdminUnlocked(Boolean(value));
};

apiBaseDisplay.textContent = API_BASE;

testConnectionButton.addEventListener('click', checkConnection);

setMode('user');
updateAdminLockUi(isAdminUnlocked());
checkConnection();
