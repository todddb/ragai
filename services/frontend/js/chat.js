let conversationId = null;

function addMessage(role, text) {
  const container = document.getElementById('chatContainer');
  const message = document.createElement('div');
  message.className = `message ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  bubble.textContent = text;
  const meta = document.createElement('div');
  meta.className = 'message-meta';
  meta.textContent = new Date().toLocaleString();
  const wrapper = document.createElement('div');
  wrapper.appendChild(bubble);
  wrapper.appendChild(meta);
  message.appendChild(wrapper);
  container.appendChild(message);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

async function startConversation() {
  const response = await fetch(`${API_BASE}/api/chat/start`, { method: 'POST' });
  const data = await response.json();
  conversationId = data.conversation_id;
}

async function sendMessage() {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addMessage('user', text);

  const statusText = document.getElementById('statusText');
  statusText.textContent = '';

  const response = await fetch(`${API_BASE}/api/chat/${conversationId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let assistantBubble = addMessage('assistant', '');

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    chunk.split('\n\n').forEach((line) => {
      if (!line.startsWith('data: ')) return;
      const payload = JSON.parse(line.replace('data: ', ''));
      if (payload.type === 'status') {
        statusText.textContent = payload.message;
      }
      if (payload.type === 'token') {
        assistantBubble.textContent += payload.text;
      }
      if (payload.type === 'done') {
        statusText.textContent = '';
      }
    });
  }
}

const sendButton = document.getElementById('sendButton');
const newConversation = document.getElementById('newConversation');

sendButton.addEventListener('click', sendMessage);
newConversation.addEventListener('click', async () => {
  await startConversation();
  document.getElementById('chatContainer').innerHTML = '';
});

startConversation();
