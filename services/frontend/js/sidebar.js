const SIDEBAR_WIDTH_KEY = 'ragai.sidebar.width';
const SIDEBAR_COLLAPSED_KEY = 'ragai.sidebar.collapsed';
const DEFAULT_SIDEBAR_WIDTH = 320;
const MIN_SIDEBAR_WIDTH = 240;
const MAX_SIDEBAR_WIDTH = 520;
const COLLAPSED_SIDEBAR_WIDTH = 56;

const sidebar = document.getElementById('sidebar');
const conversationList = document.getElementById('conversationList');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarNew = document.getElementById('sidebarNew');
const resizeHandle = document.getElementById('sidebarResizeHandle');

if (sidebar && conversationList) {
  const getStoredWidth = () => {
    const stored = Number.parseInt(localStorage.getItem(SIDEBAR_WIDTH_KEY), 10);
    if (Number.isNaN(stored)) {
      return DEFAULT_SIDEBAR_WIDTH;
    }
    return Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, stored));
  };

  const setSidebarWidth = (width) => {
    sidebar.style.width = `${width}px`;
  };

  const isCollapsed = () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';

  const applyCollapsedState = (collapsed) => {
    sidebar.classList.toggle('collapsed', collapsed);
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
    sidebarToggle.textContent = collapsed ? '❯' : '❮';
    sidebarToggle.setAttribute(
      'aria-label',
      collapsed ? 'Expand conversations sidebar' : 'Collapse conversations sidebar'
    );
    if (collapsed) {
      setSidebarWidth(COLLAPSED_SIDEBAR_WIDTH);
    } else {
      setSidebarWidth(getStoredWidth());
    }
  };

  const getFilenameFromHeader = (response, fallbackName) => {
    const disposition = response.headers.get('content-disposition');
    if (!disposition) return fallbackName;
    const utfMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utfMatch) {
      return decodeURIComponent(utfMatch[1]);
    }
    const match = disposition.match(/filename="?([^"]+)"?/i);
    return match ? match[1] : fallbackName;
  };

  const downloadResponse = async (response, fallbackName) => {
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = getFilenameFromHeader(response, fallbackName);
    link.click();
    URL.revokeObjectURL(url);
  };

  const updateActiveConversation = () => {
    const activeId = window.getCurrentConversationId ? window.getCurrentConversationId() : null;
    document.querySelectorAll('.conversation-item').forEach((item) => {
      item.classList.toggle('active', item.dataset.id === activeId);
    });
  };

  const buildConversationItem = (conv) => {
    const item = document.createElement('div');
    item.className = 'conversation-item';
    item.dataset.id = conv.id;

    const header = document.createElement('div');
    header.className = 'conversation-item-header';

    const titleWrap = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'conversation-item-title';
    title.textContent = conv.title || 'Untitled';

    const meta = document.createElement('div');
    meta.className = 'conversation-item-meta';
    meta.textContent = `Updated ${conv.updated_at}`;

    titleWrap.appendChild(title);
    titleWrap.appendChild(meta);

    const actions = document.createElement('div');
    actions.className = 'conversation-item-actions';

    const openButton = document.createElement('button');
    openButton.className = 'btn btn-primary';
    openButton.textContent = 'Open';
    openButton.addEventListener('click', async () => {
      if (window.loadConversation) {
        await window.loadConversation(conv.id);
        updateActiveConversation();
      }
    });

    const renameButton = document.createElement('button');
    renameButton.className = 'btn';
    renameButton.textContent = 'Rename';

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

    actions.appendChild(openButton);
    actions.appendChild(renameButton);
    actions.appendChild(deleteButton);
    actions.appendChild(exportButton);

    header.appendChild(titleWrap);
    header.appendChild(actions);

    const renameRow = document.createElement('div');
    renameRow.className = 'conversation-rename';

    const renameInput = document.createElement('input');
    renameInput.type = 'text';
    renameInput.value = conv.title || '';

    const renameSave = document.createElement('button');
    renameSave.className = 'btn btn-primary';
    renameSave.textContent = 'Save';

    const renameCancel = document.createElement('button');
    renameCancel.className = 'btn';
    renameCancel.textContent = 'Cancel';

    renameRow.appendChild(renameInput);
    renameRow.appendChild(renameSave);
    renameRow.appendChild(renameCancel);

    renameButton.addEventListener('click', () => {
      renameRow.style.display = 'flex';
      renameInput.focus();
    });

    renameSave.addEventListener('click', async () => {
      const titleValue = renameInput.value.trim();
      if (!titleValue) return;
      await fetch(`${API_BASE}/api/chat/${conv.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: titleValue })
      });
      renameRow.style.display = 'none';
      loadConversations();
    });

    renameCancel.addEventListener('click', () => {
      renameRow.style.display = 'none';
      renameInput.value = conv.title || '';
    });

    item.appendChild(header);
    item.appendChild(renameRow);

    return item;
  };

  const loadConversations = async () => {
    const response = await fetch(`${API_BASE}/api/chat/list`);
    if (!response.ok) return;
    const data = await response.json();
    conversationList.innerHTML = '';
    data.forEach((conv) => {
      conversationList.appendChild(buildConversationItem(conv));
    });
    updateActiveConversation();
  };

  sidebarToggle?.addEventListener('click', () => {
    applyCollapsedState(!isCollapsed());
  });

  sidebarNew?.addEventListener('click', async () => {
    if (window.startNewConversation) {
      await window.startNewConversation();
      loadConversations();
      updateActiveConversation();
    }
  });

  if (resizeHandle) {
    resizeHandle.addEventListener('mousedown', (event) => {
      if (isCollapsed()) {
        return;
      }
      event.preventDefault();
      document.body.classList.add('sidebar-resizing');
      const sidebarRect = sidebar.getBoundingClientRect();

      const onMouseMove = (moveEvent) => {
        const nextWidth = Math.min(
          MAX_SIDEBAR_WIDTH,
          Math.max(MIN_SIDEBAR_WIDTH, moveEvent.clientX - sidebarRect.left)
        );
        setSidebarWidth(nextWidth);
        localStorage.setItem(SIDEBAR_WIDTH_KEY, String(nextWidth));
      };

      const onMouseUp = () => {
        document.body.classList.remove('sidebar-resizing');
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
      };

      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    });
  }

  document.addEventListener('conversation:changed', updateActiveConversation);

  applyCollapsedState(isCollapsed());
  loadConversations();
}
