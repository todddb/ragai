let conversationId = null;
let lastStatus = '';

const STATUS_LABELS = {
  intent: 'Intent',
  research: 'Research',
  synthesis: 'Synthesis',
  validation: 'Validation'
};

function formatTimestamp(timestamp) {
  const date = timestamp ? new Date(timestamp) : new Date();
  return date.toLocaleString();
}

function addMessage(role, text, timestamp) {
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
  bubble.textContent = text;

  const meta = document.createElement('div');
  meta.className = 'message-meta';
  meta.textContent = formatTimestamp(timestamp);

  const wrapper = document.createElement('div');
  wrapper.className = 'message-content';
  wrapper.appendChild(bubble);
  wrapper.appendChild(meta);

  message.appendChild(wrapper);
  container.appendChild(message);
  container.scrollTop = container.scrollHeight;
  return bubble;
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
    let content = {};
    try {
      content = JSON.parse(message.content);
    } catch (error) {
      content = { text: message.content };
    }
    addMessage(message.role, content.text || '', message.timestamp);
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
  let assistantBubble = addMessage('assistant', '');
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
        assistantBubble.textContent += payload.text;
      }
      if (payload.type === 'done') {
        statusText.textContent = '';
        lastStatus = '';
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
