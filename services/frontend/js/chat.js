let conversationId = null;
let lastStatus = '';

const STATUS_LABELS = {
  intent: 'Intent',
  research: 'Research',
  synthesis: 'Synthesis',
  validation: 'Validation'
};

if (window.marked) {
  window.marked.setOptions({ breaks: true, gfm: true });
}

function formatTimestamp(timestamp) {
  const date = timestamp ? new Date(timestamp) : new Date();
  return date.toLocaleString();
}

function escapeHtml(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function renderMarkdown(text) {
  if (window.marked) {
    return window.marked.parse(text);
  }
  return escapeHtml(text).replace(/\n/g, '<br />');
}

function parseMessageContent(rawContent) {
  if (!rawContent) {
    return { text: '' };
  }
  if (typeof rawContent === 'object') {
    return rawContent;
  }
  try {
    return JSON.parse(rawContent);
  } catch (error) {
    return { text: rawContent };
  }
}

function getCitationHits(content) {
  if (!content) {
    return [];
  }
  if (Array.isArray(content.citations)) {
    return content.citations;
  }
  if (content.pipeline && content.pipeline.research && Array.isArray(content.pipeline.research.hits)) {
    return content.pipeline.research.hits;
  }
  return [];
}

function normalizeHits(hits, limit = 8) {
  if (!Array.isArray(hits)) {
    return [];
  }
  const seen = new Map();
  hits.forEach((hit) => {
    const docId = hit.doc_id || '';
    const chunkId = hit.chunk_id || '';
    const keyBase = docId || chunkId ? `${docId}::${chunkId}` : null;
    const fallbackKey = `${hit.url || ''}::${hit.title || ''}::${(hit.text || '').slice(0, 40)}`;
    const key = keyBase || fallbackKey;
    const existing = seen.get(key);
    if (!existing || (hit.score || 0) > (existing.score || 0)) {
      seen.set(key, hit);
    }
  });
  return Array.from(seen.values())
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, limit);
}

function buildSourcesPanel(content) {
  const hits = normalizeHits(getCitationHits(content));
  if (!hits.length) {
    return null;
  }

  const details = document.createElement('details');
  details.className = 'message-panel';
  const summary = document.createElement('summary');
  summary.className = 'message-panel-summary';
  summary.textContent = 'Sources';
  details.appendChild(summary);

  const list = document.createElement('div');
  list.className = 'sources-list';

  hits.forEach((hit, index) => {
    const item = document.createElement('div');
    item.className = 'source-item';

    const header = document.createElement('div');
    header.className = 'source-header';

    const title = document.createElement('div');
    title.className = 'source-title';
    const titleText = hit.title || hit.url || `Source ${index + 1}`;

    if (hit.url) {
      const link = document.createElement('a');
      link.href = hit.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = titleText;
      link.className = 'link';
      title.appendChild(link);
    } else {
      title.textContent = titleText;
    }

    const score = document.createElement('span');
    score.className = 'source-score';
    score.textContent = hit.score ? `Score ${hit.score.toFixed(2)}` : '';

    header.appendChild(title);
    header.appendChild(score);

    const url = document.createElement('div');
    url.className = 'source-url';
    url.textContent = hit.url || '';

    const snippet = document.createElement('div');
    snippet.className = 'source-snippet';
    const snippetText = document.createElement('p');
    snippetText.className = 'source-snippet-text';
    snippetText.textContent = hit.text || '';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'source-snippet-toggle';
    toggle.textContent = 'Expand';
    toggle.addEventListener('click', () => {
      snippet.classList.toggle('expanded');
      toggle.textContent = snippet.classList.contains('expanded') ? 'Collapse' : 'Expand';
    });

    snippet.appendChild(snippetText);
    if (hit.text) {
      snippet.appendChild(toggle);
    }

    item.appendChild(header);
    if (hit.url) {
      item.appendChild(url);
    }
    if (hit.text) {
      item.appendChild(snippet);
    }
    list.appendChild(item);
  });

  details.appendChild(list);
  return details;
}

function buildDebugPanel(content) {
  if (!content || !content.pipeline) {
    return null;
  }
  const { intent, research, synthesis, validation } = content.pipeline;
  const payload = { intent, research, synthesis, validation };
  const hasData = Object.values(payload).some((value) => value !== undefined);
  if (!hasData) {
    return null;
  }

  const details = document.createElement('details');
  details.className = 'message-panel';
  const summary = document.createElement('summary');
  summary.className = 'message-panel-summary';
  summary.textContent = 'Debug';
  details.appendChild(summary);

  const pre = document.createElement('pre');
  pre.className = 'json-block';
  pre.textContent = JSON.stringify(payload, null, 2);
  details.appendChild(pre);
  return details;
}

function addMessage(role, text, timestamp, content = null) {
  const container = document.getElementById('chatContainer');
  const message = document.createElement('div');
  message.className = `message ${role}`;

  if (role === 'assistant') {
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = 'AI';
    message.appendChild(avatar);
  }

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  if (role === 'assistant') {
    bubble.classList.add('markdown-body');
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = text;
  }

  const meta = document.createElement('div');
  meta.className = 'message-meta';
  meta.textContent = formatTimestamp(timestamp);

  const wrapper = document.createElement('div');
  wrapper.className = 'message-content';
  wrapper.appendChild(bubble);

  if (role === 'assistant' && content) {
    const panels = document.createElement('div');
    panels.className = 'message-panels';
    const sourcesPanel = buildSourcesPanel(content);
    const debugPanel = buildDebugPanel(content);
    if (sourcesPanel) {
      panels.appendChild(sourcesPanel);
    }
    if (debugPanel) {
      panels.appendChild(debugPanel);
    }
    if (panels.childElementCount) {
      wrapper.appendChild(panels);
    }
  }

  wrapper.appendChild(meta);

  message.appendChild(wrapper);
  container.appendChild(message);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

function createStreamRenderer(bubble) {
  let text = '';
  let pendingRender = false;
  const render = () => {
    bubble.innerHTML = renderMarkdown(text);
  };
  const interval = setInterval(() => {
    if (!pendingRender) {
      return;
    }
    render();
    pendingRender = false;
  }, 120);

  return {
    append(chunk) {
      text += chunk;
      pendingRender = true;
    },
    finish() {
      pendingRender = true;
      render();
      clearInterval(interval);
    },
    getText() {
      return text;
    }
  };
}

function setConversationIdInUrl(value) {
  const url = new URL(window.location.href);
  if (value) {
    url.searchParams.set('conversation_id', value);
  } else {
    url.searchParams.delete('conversation_id');
  }
  window.history.replaceState({}, '', url);
}

async function startConversation() {
  const response = await fetch(`${API_BASE}/api/chat/start`, { method: 'POST' });
  const data = await response.json();
  conversationId = data.conversation_id;
}

function clearConversationUI() {
  document.getElementById('chatContainer').innerHTML = '';
  const statusText = document.getElementById('statusText');
  statusText.textContent = '';
  lastStatus = '';
}

async function loadConversation(conversationIdToLoad, options = {}) {
  const { updateUrl = true } = options;
  const response = await fetch(`${API_BASE}/api/chat/${conversationIdToLoad}`);
  if (!response.ok) {
    await startConversation();
    clearConversationUI();
    return;
  }
  const data = await response.json();
  conversationId = data.conversation.id;
  clearConversationUI();
  data.messages.forEach((message) => {
    const content = parseMessageContent(message.content);
    addMessage(message.role, content.text || '', message.timestamp, message.role === 'assistant' ? content : null);
  });
  if (updateUrl) {
    setConversationIdInUrl(conversationIdToLoad);
  }
  document.dispatchEvent(new CustomEvent('conversation:changed', { detail: { id: conversationId } }));
}

function updateStatus(payload) {
  const statusText = document.getElementById('statusText');
  const label = STATUS_LABELS[payload.stage] || 'Status';
  lastStatus = `${label}: ${payload.message}`;
  statusText.textContent = lastStatus;
}

async function sendMessage() {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addMessage('user', text);

  const statusText = document.getElementById('statusText');
  statusText.textContent = lastStatus;

  const response = await fetch(`${API_BASE}/api/chat/${conversationId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const assistantBubble = addMessage('assistant', '');
  const renderer = createStreamRenderer(assistantBubble);
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    while (buffer.includes('\n\n')) {
      const boundaryIndex = buffer.indexOf('\n\n');
      const rawEvent = buffer.slice(0, boundaryIndex);
      buffer = buffer.slice(boundaryIndex + 2);

      const dataLines = rawEvent
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.replace(/^data:\s?/, ''));

      if (!dataLines.length) {
        continue;
      }

      const payloadString = dataLines.join('\n');
      let payload = null;
      try {
        payload = JSON.parse(payloadString);
      } catch (error) {
        continue;
      }

      if (payload.type === 'status') {
        updateStatus(payload);
      }
      if (payload.type === 'token') {
        renderer.append(payload.text);
      }
      if (payload.type === 'done') {
        statusText.textContent = '';
        lastStatus = '';
        renderer.finish();
        await loadConversation(conversationId, { updateUrl: false });
      }
    }
  }
}

async function startNewConversation() {
  await startConversation();
  clearConversationUI();
  setConversationIdInUrl(null);
  document.dispatchEvent(new CustomEvent('conversation:changed', { detail: { id: conversationId } }));
}

function getCurrentConversationId() {
  return conversationId;
}

const sendButton = document.getElementById('sendButton');

sendButton.addEventListener('click', sendMessage);

const params = new URLSearchParams(window.location.search);
const requestedConversation = params.get('conversation_id');
if (requestedConversation) {
  loadConversation(requestedConversation, { updateUrl: false });
} else {
  startNewConversation();
}

window.loadConversation = loadConversation;
window.startNewConversation = startNewConversation;
window.getCurrentConversationId = getCurrentConversationId;
