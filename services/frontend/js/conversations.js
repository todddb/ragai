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
  wrapper.setAttribute('role', 'button');
  wrapper.setAttribute('tabindex', '0');

  const details = document.createElement('div');
  details.className = 'conversation-details';

  const title = document.createElement('div');
  title.className = 'conversation-title';
  title.textContent = conv.title || 'Untitled';

  const meta = document.createElement('div');
  meta.className = 'conversation-meta';
  meta.textContent = `Updated ${conv.updated_at}`;

  const renameForm = document.createElement('div');
  renameForm.className = 'conversation-rename';
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

  const createActionButton = (label, svgPath) => {
    const button = document.createElement('button');
    button.className = 'icon-btn';
    button.type = 'button';
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
    button.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="${svgPath}"></path>
      </svg>
    `;
    button.addEventListener('click', (event) => {
      event.stopPropagation();
    });
    return button;
  };

  const renameButton = createActionButton(
    'Edit',
    'M5 17.25V19h1.75L16.81 8.94l-1.75-1.75L5 17.25zm12.71-7.04a1 1 0 0 0 0-1.41l-2.5-2.5a1 1 0 0 0-1.41 0l-1.13 1.13 3.91 3.91 1.13-1.13z'
  );
  renameButton.addEventListener('click', () => {
    renameForm.style.display = 'flex';
    renameInput.focus();
  });

  const deleteButton = createActionButton(
    'Delete',
    'M6 7h12v2H6V7zm2 3h8l-1 9H9l-1-9zm3-5h2l1 1H10l1-1z'
  );
  deleteButton.addEventListener('click', async () => {
    await fetch(`${API_BASE}/api/chat/${conv.id}`, { method: 'DELETE' });
    loadConversations();
  });

  const exportButton = createActionButton(
    'Export',
    'M12 3l4 4h-3v7h-2V7H8l4-4zm-7 14h14v2H5v-2z'
  );
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

  actions.appendChild(renameButton);
  actions.appendChild(deleteButton);
  actions.appendChild(exportButton);

  wrapper.appendChild(details);
  wrapper.appendChild(actions);

  const openConversation = () => {
    window.location.href = `chat.html?conversation_id=${conv.id}`;
  };

  wrapper.addEventListener('click', openConversation);
  wrapper.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openConversation();
    }
  });

  renameForm.addEventListener('click', (event) => {
    event.stopPropagation();
  });
  actions.addEventListener('click', (event) => {
    event.stopPropagation();
  });

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
