let conversationId = null;
let lastStatus = '';
let statusTimeout = null;
let currentStreamStatus = null;
let currentStreamContent = null;
const autoTitleRequested = new Set();

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
  try {
    const t = dayjs(date);
    const relative = t.fromNow();
    const fullTime = t.format('YYYY-MM-DD HH:mm:ss');
    const displayTime = t.format('MM-DD-YYYY hh:mm A');
    return `<span class="message-timestamp" title="${fullTime}">${relative} (${displayTime})</span>`;
  } catch (e) {
    return `<span class="message-timestamp">${date.toLocaleString()}</span>`;
  }
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

async function getDetailedErrorMessage(error, context = '') {
  if (error.name === 'TypeError' && error.message.includes('fetch')) {
    return `Network error: Failed to fetch ${context}. Check if API is running at ${API_BASE}`;
  }
  if (error.message.includes('CORS')) {
    return `CORS error: ${API_BASE} is blocking requests. Check API CORS configuration.`;
  }
  return error.message || 'Unknown error occurred';
}

async function checkApiHealth() {
  try {
    const response = await fetch(`${API_BASE}/api/health`, { timeout: 2000 });
    if (!response.ok) {
      setStatusMessage(`⚠️ API health check failed (HTTP ${response.status})`, 'error', { temporary: false });
      return false;
    }
    const health = await response.json();
    if (health.ollama !== 'ok' || health.qdrant !== 'ok') {
      const issues = [];
      if (health.ollama !== 'ok') issues.push('Ollama');
      if (health.qdrant !== 'ok') issues.push('Qdrant');
      setStatusMessage(`⚠️ Connected but ${issues.join(' and ')} ${issues.length > 1 ? 'are' : 'is'} down`, 'error', { temporary: false });
      return false;
    }
    return true;
  } catch (error) {
    const errorMsg = await getDetailedErrorMessage(error, '/api/health');
    setStatusMessage(`❌ ${errorMsg}`, 'error', { temporary: false });
    return false;
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
  // Try to use new aggregated sources first, fall back to old hits
  let sources = [];
  if (content.sources && Array.isArray(content.sources) && content.sources.length > 0) {
    sources = content.sources;
  } else {
    const hits = normalizeHits(getCitationHits(content));
    if (!hits.length) {
      return null;
    }
    // Convert old hits format to new sources format
    sources = hits.map(hit => ({
      doc_id: hit.doc_id || '',
      title: hit.title || hit.url || 'Unknown',
      url: hit.url || '',
      best_score: hit.score || 0,
      match_count: 1,
      snippet: hit.text || ''
    }));
  }

  if (!sources.length) {
    return null;
  }

  const container = document.createElement('div');
  container.className = 'sources-container';

  const heading = document.createElement('div');
  heading.className = 'sources-heading';
  heading.textContent = 'Sources';
  container.appendChild(heading);

  const list = document.createElement('div');
  list.className = 'sources-list-inline';

  // Show top 3 sources inline
  const topSources = sources.slice(0, 3);
  topSources.forEach((source, index) => {
    const item = document.createElement('div');
    item.className = 'source-item-inline';

    // Header with citation number and title
    const header = document.createElement('div');
    header.className = 'source-header';

    const citation = document.createElement('span');
    citation.className = 'source-citation';
    citation.textContent = `[${index + 1}] `;

    const titleLink = document.createElement('a');
    titleLink.href = source.url || '#';
    titleLink.target = '_blank';
    titleLink.rel = 'noopener noreferrer';
    titleLink.className = 'source-link';
    titleLink.textContent = source.title || source.url || `Source ${index + 1}`;

    header.appendChild(citation);
    header.appendChild(titleLink);
    item.appendChild(header);

    // URL
    if (source.url) {
      const urlDiv = document.createElement('div');
      urlDiv.className = 'source-url';
      urlDiv.textContent = source.url;
      item.appendChild(urlDiv);
    }

    // Snippet
    if (source.snippet) {
      const snippetDiv = document.createElement('div');
      snippetDiv.className = 'source-snippet-inline';
      snippetDiv.textContent = (source.snippet || '').slice(0, 200) + '...';
      item.appendChild(snippetDiv);
    }

    // Metadata (matches and score)
    if (source.match_count > 1 || source.best_score) {
      const metaDiv = document.createElement('div');
      metaDiv.className = 'source-meta-inline';
      const parts = [];
      if (source.match_count > 1) {
        parts.push(`${source.match_count} matches`);
      }
      if (source.best_score) {
        parts.push(`score ${Number(source.best_score).toFixed(2)}`);
      }
      metaDiv.textContent = parts.join(' • ');
      item.appendChild(metaDiv);
    }

    list.appendChild(item);
  });

  container.appendChild(list);

  // If there are more than 3 sources, add an expander
  if (sources.length > 3) {
    const moreBtn = document.createElement('button');
    moreBtn.className = 'sources-more-btn';
    moreBtn.textContent = `More sources (${sources.length - 3})`;
    moreBtn.type = 'button';

    const moreContainer = document.createElement('div');
    moreContainer.className = 'sources-more-list';
    moreContainer.style.display = 'none';

    sources.slice(3).forEach((source, j) => {
      const item = document.createElement('div');
      item.className = 'source-item-inline';

      // Header with citation number and title
      const header = document.createElement('div');
      header.className = 'source-header';

      const citation = document.createElement('span');
      citation.className = 'source-citation';
      citation.textContent = `[${3 + j + 1}] `;

      const titleLink = document.createElement('a');
      titleLink.href = source.url || '#';
      titleLink.target = '_blank';
      titleLink.rel = 'noopener noreferrer';
      titleLink.className = 'source-link';
      titleLink.textContent = source.title || source.url || `Source ${3 + j + 1}`;

      header.appendChild(citation);
      header.appendChild(titleLink);
      item.appendChild(header);

      // URL
      if (source.url) {
        const urlDiv = document.createElement('div');
        urlDiv.className = 'source-url';
        urlDiv.textContent = source.url;
        item.appendChild(urlDiv);
      }

      // Snippet
      if (source.snippet) {
        const snippetDiv = document.createElement('div');
        snippetDiv.className = 'source-snippet-inline';
        snippetDiv.textContent = (source.snippet || '').slice(0, 200) + '...';
        item.appendChild(snippetDiv);
      }

      // Metadata (matches and score) - only if available
      if (source.match_count > 1 || source.best_score) {
        const metaDiv = document.createElement('div');
        metaDiv.className = 'source-meta-inline';
        const parts = [];
        if (source.match_count > 1) {
          parts.push(`${source.match_count} matches`);
        }
        if (source.best_score) {
          parts.push(`score ${Number(source.best_score).toFixed(2)}`);
        }
        metaDiv.textContent = parts.join(' • ');
        item.appendChild(metaDiv);
      }

      moreContainer.appendChild(item);
    });

    moreBtn.addEventListener('click', () => {
      const isHidden = moreContainer.style.display === 'none';
      moreContainer.style.display = isHidden ? 'block' : 'none';
      moreBtn.textContent = isHidden ? 'Hide additional sources' : `More sources (${sources.length - 3})`;
    });

    container.appendChild(moreBtn);
    container.appendChild(moreContainer);
  }

  return container;
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
  message.className = `msg-row msg-${role}`;

  let statusLine = null;
  let contentNode = null;

  // Create avatar for both user and assistant messages
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'assistant' ? 'AI' : 'USER';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  if (role === 'assistant') {
    statusLine = document.createElement('div');
    statusLine.className = 'assistant-status';

    contentNode = document.createElement('div');
    contentNode.className = 'markdown-body';
    contentNode.innerHTML = renderMarkdown(text);

    bubble.appendChild(statusLine);
    bubble.appendChild(contentNode);
  } else {
    bubble.textContent = text;
  }

  const meta = document.createElement('div');
  meta.className = 'message-meta';
  meta.innerHTML = formatTimestamp(timestamp);

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

  // For AI messages: avatar first, then wrapper
  // For USER messages: wrapper first, then avatar
  if (role === 'assistant') {
    message.appendChild(avatar);
    message.appendChild(wrapper);
  } else {
    message.appendChild(wrapper);
    message.appendChild(avatar);
  }

  container.appendChild(message);
  container.scrollTop = container.scrollHeight;
  return { bubble, statusLine, contentNode };
}

function createStreamRenderer(contentNode) {
  let text = '';
  let pendingRender = false;
  const render = () => {
    contentNode.innerHTML = renderMarkdown(text);
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
  try {
    const response = await fetch(`${API_BASE}/api/chat/start`, { method: 'POST' });
    if (!response.ok) {
      const errorBody = await response.text().catch(() => 'No error details');
      setStatusMessage(`❌ API error ${response.status}: ${errorBody}`, 'error');
      return false;
    }
    const data = await response.json();
    conversationId = data.conversation_id;
    return true;
  } catch (error) {
    const errorMsg = await getDetailedErrorMessage(error, '/api/chat/start');
    setStatusMessage(`❌ ${errorMsg}`, 'error');
    return false;
  }
}

function clearConversationUI() {
  document.getElementById('chatContainer').innerHTML = '';
  setStatusMessage('');
  lastStatus = '';
  currentStreamStatus = null;
  currentStreamContent = null;
}

async function loadConversation(conversationIdToLoad, options = {}) {
  const { updateUrl = true } = options;
  let response;
  try {
    response = await fetch(`${API_BASE}/api/chat/${conversationIdToLoad}`);
  } catch (error) {
    const errorMsg = await getDetailedErrorMessage(error, `/api/chat/${conversationIdToLoad}`);
    setStatusMessage(`❌ ${errorMsg}`, 'error');
    return;
  }
  if (!response.ok) {
    await startConversation();
    clearConversationUI();
    setStatusMessage('❌ Conversation not found. Started a new one.', 'error', { temporary: true });
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
  await maybeAutoTitleConversation(data.conversation, data.messages);
}

function updateStatus(payload) {
  const label = STATUS_LABELS[payload.stage] || 'Status';
  lastStatus = `${label}: ${payload.message}`;
  if (!currentStreamStatus) {
    const assistantMessage = addMessage('assistant', '');
    currentStreamStatus = assistantMessage.statusLine;
    currentStreamContent = assistantMessage.contentNode;
  }
  if (currentStreamStatus) {
    currentStreamStatus.textContent = lastStatus;
  }
}

function setStatusMessage(message, type = '', options = {}) {
  const statusText = document.getElementById('chatStatus');
  const { temporary = false } = options;
  if (!statusText) {
    return;
  }
  statusText.textContent = message;
  statusText.classList.remove('success', 'error');
  if (type) {
    statusText.classList.add(type);
  }
  if (statusTimeout) {
    clearTimeout(statusTimeout);
    statusTimeout = null;
  }
  if (temporary && message) {
    statusTimeout = window.setTimeout(() => {
      statusText.textContent = '';
      statusText.classList.remove('success', 'error');
    }, 3000);
  }
}

async function sendMessage() {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text) return;
  if (!conversationId) {
    const started = await startConversation();
    if (!started) {
      return;
    }
  }
  input.value = '';
  input.focus();
  addMessage('user', text);

  setStatusMessage('');
  lastStatus = '';
  const assistantMessage = addMessage('assistant', '');
  currentStreamStatus = assistantMessage.statusLine;
  currentStreamContent = assistantMessage.contentNode;
  if (currentStreamStatus) {
    currentStreamStatus.textContent = '';
  }
  const renderer = createStreamRenderer(currentStreamContent);

  try {
    const response = await fetch(`${API_BASE}/api/chat/${conversationId}/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });

    if (!response.ok) {
      const detail = await response.text();
      setStatusMessage(`❌ API error ${response.status}: ${detail}`, 'error');
      if (currentStreamStatus) {
        currentStreamStatus.textContent = '';
      }
      currentStreamStatus = null;
      currentStreamContent = null;
      renderer.finish();
      return;
    }

    if (!response.body) {
      setStatusMessage('❌ Response stream unavailable from API.', 'error');
      if (currentStreamStatus) {
        currentStreamStatus.textContent = '';
      }
      currentStreamStatus = null;
      currentStreamContent = null;
      renderer.finish();
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let doneReceived = false;

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
          doneReceived = true;
          lastStatus = '';
          if (currentStreamStatus) {
            currentStreamStatus.textContent = '';
          }
          currentStreamStatus = null;
          currentStreamContent = null;
          renderer.finish();
          await loadConversation(conversationId, { updateUrl: false });
        }
      }
    }

    if (!doneReceived) {
      setStatusMessage('❌ Response stream interrupted before completion.', 'error');
    }
  } catch (error) {
    const errorMsg = await getDetailedErrorMessage(error, '/api/chat/message');
    setStatusMessage(`❌ ${errorMsg}`, 'error');
    if (currentStreamStatus) {
      currentStreamStatus.textContent = '';
    }
    currentStreamStatus = null;
    currentStreamContent = null;
    renderer.finish();
  }
}

function shouldAutoTitle(conversation, messages) {
  if (!conversation || !conversation.id) {
    return false;
  }
  if (conversation.title && conversation.title !== 'New Conversation') {
    return false;
  }
  if (conversation.auto_titled) {
    return false;
  }
  if (autoTitleRequested.has(conversation.id)) {
    return false;
  }
  const hasUser = messages.some((message) => message.role === 'user');
  const hasAssistant = messages.some((message) => message.role === 'assistant');
  return hasUser && hasAssistant;
}

async function maybeAutoTitleConversation(conversation, messages) {
  if (!shouldAutoTitle(conversation, messages)) {
    return;
  }
  autoTitleRequested.add(conversation.id);
  try {
    const response = await fetch(`${API_BASE}/api/chat/${conversation.id}/title/auto`, {
      method: 'POST'
    });
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    if (data.title && window.refreshConversationList) {
      window.refreshConversationList();
    }
  } catch (error) {
    autoTitleRequested.delete(conversation.id);
  }
}

async function startNewConversation() {
  const started = await startConversation();
  if (!started) {
    return false;
  }
  clearConversationUI();
  setConversationIdInUrl(null);
  setStatusMessage('New conversation started.', 'success', { temporary: true });
  document.dispatchEvent(new CustomEvent('conversation:changed', { detail: { id: conversationId } }));
  return true;
}

function getCurrentConversationId() {
  return conversationId;
}

const sendButton = document.getElementById('sendButton');
const messageInput = document.getElementById('messageInput');

sendButton.addEventListener('click', sendMessage);
messageInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    const text = messageInput.value.trim();
    if (text === '') {
      messageInput.value = '';
      return;
    }
    sendMessage();
  }
  // Shift+Enter allows newline (default browser behavior)
});

checkApiHealth();

const params = new URLSearchParams(window.location.search);
const requestedConversation = params.get('conversation_id');
if (requestedConversation) {
  loadConversation(requestedConversation, { updateUrl: false });
}
// Don't create a conversation on page load - only when user sends first message

window.loadConversation = loadConversation;
window.startNewConversation = startNewConversation;
window.getCurrentConversationId = getCurrentConversationId;
window.setStatusMessage = setStatusMessage;
