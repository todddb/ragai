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

function buildConversationCard(conv) {
  const wrapper = document.createElement('div');
  wrapper.className = 'conversation-card';

  const details = document.createElement('div');
  details.className = 'conversation-details';

  const title = document.createElement('div');
  title.className = 'conversation-title';
  title.textContent = conv.title || 'Untitled';

  const meta = document.createElement('div');
  meta.className = 'conversation-meta';
  meta.textContent = `Updated ${conv.updated_at}`;

  const renameForm = document.createElement('div');
  renameForm.className = 'conversation-actions';
  renameForm.style.display = 'none';

  const renameInput = document.createElement('input');
  renameInput.type = 'text';
  renameInput.value = conv.title || '';

  const renameSave = document.createElement('button');
  renameSave.className = 'btn btn-primary';
  renameSave.textContent = 'Save';

  const renameCancel = document.createElement('button');
  renameCancel.className = 'btn';
  renameCancel.textContent = 'Cancel';

  renameForm.appendChild(renameInput);
  renameForm.appendChild(renameSave);
  renameForm.appendChild(renameCancel);

  details.appendChild(title);
  details.appendChild(meta);
  details.appendChild(renameForm);

  const actions = document.createElement('div');
  actions.className = 'conversation-actions';

  const openButton = document.createElement('button');
  openButton.className = 'btn btn-primary';
  openButton.textContent = 'Open';
  openButton.addEventListener('click', () => {
    window.location.href = `chat.html?conversation_id=${conv.id}`;
  });

  const renameButton = document.createElement('button');
  renameButton.className = 'btn';
  renameButton.textContent = 'Rename';
  renameButton.addEventListener('click', () => {
    renameForm.style.display = 'flex';
    renameInput.focus();
  });

  const deleteButton = document.createElement('button');
  deleteButton.className = 'btn';
  deleteButton.textContent = 'Delete';
  deleteButton.addEventListener('click', async () => {
    await fetch(`${API_BASE}/api/chat/${conv.id}`, { method: 'DELETE' });
    loadConversations();
  });

  const exportButton = document.createElement('button');
  exportButton.className = 'btn';
  exportButton.textContent = 'Export';
  exportButton.addEventListener('click', async () => {
    const response = await fetch(`${API_BASE}/api/chat/${conv.id}/export`);
    if (!response.ok) return;
    await downloadResponse(response, `conversation_${conv.id}.json`);
  });

  renameSave.addEventListener('click', async () => {
    const titleValue = renameInput.value.trim();
    if (!titleValue) return;
    await fetch(`${API_BASE}/api/chat/${conv.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: titleValue })
    });
    renameForm.style.display = 'none';
    loadConversations();
  });

  renameCancel.addEventListener('click', () => {
    renameForm.style.display = 'none';
    renameInput.value = conv.title || '';
  });

  actions.appendChild(openButton);
  actions.appendChild(renameButton);
  actions.appendChild(deleteButton);
  actions.appendChild(exportButton);

  wrapper.appendChild(details);
  wrapper.appendChild(actions);

  return wrapper;
}

async function loadConversations() {
  const response = await fetch(`${API_BASE}/api/chat/list`);
  const data = await response.json();
  const container = document.getElementById('conversationList');
  container.innerHTML = '';
  data.forEach((conv) => {
    container.appendChild(buildConversationCard(conv));
  });
}

loadConversations();
