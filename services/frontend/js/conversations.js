async function loadConversations() {
  const response = await fetch(`${API_BASE}/api/chat/list`);
  const data = await response.json();
  const container = document.getElementById('conversationList');
  container.innerHTML = '';
  data.forEach((conv) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'message-bubble';
    wrapper.style.marginBottom = '1rem';
    wrapper.innerHTML = `
      <strong>${conv.title}</strong><br />
      <small>${conv.updated_at}</small>
      <div style="margin-top:0.5rem;">
        <button class="btn" data-id="${conv.id}" data-action="rename">Rename</button>
        <button class="btn" data-id="${conv.id}" data-action="delete">Delete</button>
        <button class="btn" data-id="${conv.id}" data-action="export">Export</button>
      </div>
    `;
    container.appendChild(wrapper);
  });
}

document.addEventListener('click', async (event) => {
  const target = event.target;
  if (!target.dataset || !target.dataset.action) {
    return;
  }
  const conversationId = target.dataset.id;
  if (target.dataset.action === 'rename') {
    const title = prompt('New title');
    if (!title) return;
    await fetch(`${API_BASE}/api/chat/${conversationId}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title})
    });
    loadConversations();
  }
  if (target.dataset.action === 'delete') {
    await fetch(`${API_BASE}/api/chat/${conversationId}`, { method: 'DELETE' });
    loadConversations();
  }
  if (target.dataset.action === 'export') {
    const response = await fetch(`${API_BASE}/api/chat/${conversationId}/export`);
    const data = await response.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `conversation_${conversationId}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }
});

loadConversations();
