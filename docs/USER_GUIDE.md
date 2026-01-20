# RagAI User Guide

This guide explains how to use RagAI's chat interface, manage conversations, and configure settings.

## Table of Contents

- [Accessing RagAI](#accessing-ragai)
- [Chat Interface](#chat-interface)
- [Managing Conversations](#managing-conversations)
- [Settings and Configuration](#settings-and-configuration)
- [Advanced Features](#advanced-features)
- [Tips and Best Practices](#tips-and-best-practices)

## Accessing RagAI

Once RagAI is running, access the web interface at:

**http://localhost:5000**

The main pages are:

- **Chat** - Ask questions and interact with your knowledge base
- **Conversations** - View, manage, and organize chat history
- **Settings** - Check system health, configure API connection, and access admin controls

## Chat Interface

### Starting a Conversation

1. Navigate to http://localhost:5000/chat.html
2. Type your question in the input box at the bottom
3. Press Enter or click the Send button
4. Watch as RagAI processes your question through its agentic pipeline

### How RagAI Processes Questions

RagAI uses a four-stage agentic pipeline:

1. **Intent Analysis** - Understands your question and generates search queries
2. **Research** - Searches the vector database for relevant information
3. **Synthesis** - Drafts a comprehensive answer with citations
4. **Validation** - Validates answer quality and may ask clarifying questions

You'll see real-time updates as each stage completes, providing transparency into the reasoning process.

### Understanding Responses

#### Citations

RagAI includes citations in its responses:

```
According to the documentation [1], the feature works by...

[1] https://example.com/docs/feature-guide
```

Click citation links to view the source material.

#### Streaming Responses

Answers stream in real-time, character by character. This allows you to start reading while RagAI is still generating the response.

#### Follow-up Questions

RagAI maintains conversation context. Ask follow-up questions naturally:

```
You: How do I configure the API?
RagAI: [provides answer with citations]

You: What about authentication?
RagAI: [understands you're asking about API authentication specifically]
```

### Conversation Actions

During a chat session, you can:

- **Continue the conversation** - Ask follow-up questions
- **Start a new conversation** - Click the "New Conversation" button
- **View conversation history** - Click "Conversations" in the header
- **Copy responses** - Highlight and copy text from RagAI's responses

## Managing Conversations

### Viewing Conversations

1. Navigate to http://localhost:5000/conversations.html
2. Browse all saved conversations, sorted by most recent

Each conversation card shows:

- Conversation title (auto-generated from first question)
- Number of messages
- Last activity timestamp

### Opening a Conversation

Click any conversation card to open it in the chat interface with full history preserved.

### Renaming Conversations

1. Hover over a conversation card
2. Click the edit/rename icon
3. Enter a new title
4. Press Enter to save

### Deleting Conversations

1. Hover over a conversation card
2. Click the delete icon
3. Confirm deletion

**Warning**: Deleted conversations cannot be recovered.

### Exporting Conversations

1. Hover over a conversation card
2. Click the export icon
3. Downloads a JSON file with the complete conversation history

Useful for:
- Backing up important conversations
- Sharing knowledge with team members
- Analyzing conversation patterns

## Settings and Configuration

### Connection Health

Navigate to http://localhost:5000/settings.html and click the "Connection" tab.

#### Health Check

Click "Check Health" to verify:

- **API Status** - FastAPI backend is responsive
- **Ollama Status** - LLM service is available
- **Qdrant Status** - Vector database is accessible

Status indicators:

- Green checkmark: Service is healthy
- Red X: Service is unavailable (see troubleshooting)

### API URL Configuration

By default, RagAI connects to `http://localhost:8000`. To use a different API (e.g., remote server):

#### Using the Settings UI

1. Go to Settings → Connection
2. Enter the new API URL (e.g., `http://192.168.1.100:8000`)
3. Click "Save API URL"
4. Reload the page

#### Using Browser Console

```javascript
localStorage.setItem('API_URL', 'http://your-api-host:8000');
location.reload();
```

#### Resetting to Default

```javascript
localStorage.removeItem('API_URL');
location.reload();
```

### User Preferences

#### Theme (if available)

Some browsers may respect system dark mode preferences. Custom theme support may be added in future versions.

#### Chat Settings

These are configured globally via `config/agents.yml` on the server (see [Admin Guide](ADMIN_GUIDE.md)).

## Advanced Features

### Multi-turn Conversations

RagAI excels at multi-turn conversations where context from previous messages informs future responses:

```
You: What are the system requirements?
RagAI: [lists requirements]

You: Do I need Docker?
RagAI: Yes, as mentioned in the requirements above, Docker 20.10+ is required...
```

### Clarifying Questions

If RagAI needs more information, it may ask clarifying questions:

```
You: How do I set it up?
RagAI: I found multiple setup guides. Are you asking about:
       1. Initial installation
       2. GPU configuration
       3. Playwright authentication
```

Respond naturally to guide RagAI to the right answer.

### Complex Queries

RagAI handles complex, multi-part questions:

```
You: Compare the performance characteristics of the different embedding models
     and explain when I should use each one.
RagAI: [comprehensive comparison with citations]
```

### Code and Technical Content

RagAI preserves formatting for code snippets, commands, and technical content:

```bash
./tools/ragaictl start
```

```python
def example():
    return "formatted code"
```

## Tips and Best Practices

### Asking Better Questions

**Be Specific**

Poor: "How does it work?"
Better: "How does the agentic pipeline process user questions?"

**Provide Context**

Poor: "Is it compatible?"
Better: "Is RagAI compatible with Windows 11 via WSL2?"

**Break Down Complex Questions**

Instead of: "How do I install, configure GPU support, set up authenticated crawling, and optimize performance?"

Ask separately:
1. "How do I install RagAI?"
2. "How do I enable GPU acceleration?"
3. "How do I set up authenticated crawling?"
4. "What are the best practices for performance optimization?"

### Leveraging Citations

Always check citations to:

- Verify information
- Explore source material in depth
- Understand context
- Discover related content

### Managing Long Conversations

For focused results:

- Start a new conversation for each major topic
- Use descriptive conversation titles
- Archive or export old conversations
- Delete test/experimental conversations

### When RagAI Can't Answer

If RagAI can't find relevant information:

1. **Check if content has been crawled** - Verify the source is in your seed URLs
2. **Rephrase your question** - Try different wording or keywords
3. **Run a new crawl** - Content may have been updated (see [Admin Guide](ADMIN_GUIDE.md))
4. **Check crawl/ingest logs** - Look for errors during data ingestion

### Privacy and Data

RagAI is **local-first**:

- All data stays on your machine
- No external API calls (except to sites you're crawling)
- Conversations are stored in SQLite locally
- You control what gets crawled and ingested

## Keyboard Shortcuts

- **Enter** - Send message
- **Shift + Enter** - New line in message input
- **Ctrl/Cmd + K** - Clear conversation (if implemented)
- **Ctrl/Cmd + /** - Focus message input (if implemented)

(Note: Some shortcuts may vary by implementation)

## Accessibility

RagAI frontend aims to be accessible:

- Semantic HTML structure
- Keyboard navigation support
- Screen reader friendly
- High contrast text
- Responsive design for mobile devices

If you encounter accessibility issues, please report them.

## Troubleshooting

### No Response from RagAI

1. Check Settings → Connection → Health Check
2. Verify all services are running: `./tools/ragaictl status`
3. Check for errors in logs: `./tools/ragaictl logs api`

### Incomplete or Inaccurate Answers

1. Verify content has been crawled and ingested
2. Try rephrasing your question
3. Check if vector database has data:
   - Settings → Admin → View Qdrant status
4. Run a fresh crawl and ingest cycle

### Citations Not Working

1. Verify URLs in citations match crawled content
2. Check if source pages are still accessible
3. Review crawl logs for fetch errors

### Slow Responses

1. **Enable GPU acceleration** - See [Installation Guide](INSTALL.md)
2. **Use smaller models** - Edit `config/system.yml` to use a faster model
3. **Reduce context** - Start a new conversation to clear history
4. **Check system resources** - Ensure sufficient RAM and CPU available

### Connection Lost

1. Refresh the page
2. Check API URL in Settings → Connection
3. Verify API is running: `./tools/ragaictl status`
4. Check browser console for errors (F12 → Console tab)

## Next Steps

- Learn about admin features in the [Admin Guide](ADMIN_GUIDE.md)
- Customize agent behavior by editing `config/agents.yml`
- Explore the API at http://localhost:8000/docs
- Join the community to share tips and get help

## Feedback

If you encounter issues or have suggestions:

1. Check the logs: `./tools/ragaictl logs api`
2. Review configuration in `config/`
3. Consult the [Installation Guide](INSTALL.md) and [Admin Guide](ADMIN_GUIDE.md)
4. Open an issue on the project repository
