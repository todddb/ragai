const SIDEBAR_WIDTH_KEY = 'ragai.sidebar.width';
const SIDEBAR_COLLAPSED_KEY = 'ragai.sidebar.collapsed';
const DEFAULT_SIDEBAR_WIDTH = 320;
const MIN_SIDEBAR_WIDTH = 240;
const MAX_SIDEBAR_WIDTH = 520;
const COLLAPSED_SIDEBAR_WIDTH = 56;

const sidebar = document.getElementById('sidebar');
const conversationList = document.getElementById('conversationList');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarCollapsedToggle = document.getElementById('sidebarCollapsedToggle');
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
    if (sidebarCollapsedToggle) {
      sidebarCollapsedToggle.setAttribute(
        'aria-label',
        collapsed ? 'Expand conversations sidebar' : 'Collapse conversations sidebar'
      );
    }
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
    item.setAttribute('role', 'button');
    item.setAttribute('tabindex', '0');

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

    const deleteButton = createActionButton(
      'Delete',
      'M6 7h12v2H6V7zm2 3h8l-1 9H9l-1-9zm3-5h2l1 1H10l1-1z'
    );

    const exportButton = createActionButton(
      'Export',
      'M12 3l4 4h-3v7h-2V7H8l4-4zm-7 14h14v2H5v-2z'
    );
    deleteButton.addEventListener('click', async () => {
      await fetch(`${API_BASE}/api/chat/${conv.id}`, { method: 'DELETE' });
      loadConversations();
    });

    exportButton.addEventListener('click', async () => {
      const response = await fetch(`${API_BASE}/api/chat/${conv.id}/export`);
      if (!response.ok) return;
      await downloadResponse(response, `conversation_${conv.id}.json`);
    });

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

    const openConversation = async () => {
      if (window.loadConversation) {
        await window.loadConversation(conv.id);
        updateActiveConversation();
      }
    };

    item.addEventListener('click', openConversation);
    item.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openConversation();
      }
    });

    renameRow.addEventListener('click', (event) => {
      event.stopPropagation();
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

  sidebarCollapsedToggle?.addEventListener('click', () => {
    applyCollapsedState(false);
  });

  sidebarNew?.addEventListener('click', async () => {
    if (window.startNewConversation) {
      const started = await window.startNewConversation();
      if (!started) {
        return;
      }
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

  window.refreshConversationList = loadConversations;
}
