# RagAI

Local-first agentic RAG system powered by Ollama, Qdrant, and FastAPI.

## Quick Start

```bash
./tools/ragaictl start
```

Access:
- API: http://localhost:8000
- Frontend: http://localhost:5000

Before your first crawl, update `config/allow_block.yml` with your `seed_urls` and `allowed_domains`.

## Services

- **API**: FastAPI orchestrator (chat, admin, config)
- **Crawler**: HTML discovery + capture
- **Ingestor**: Embedding + Qdrant upsert
- **Frontend**: Static HTML/CSS/JS UI

## Configuration

Configs live in `config/`:
- `system.yml`
- `allow_block.yml`
- `crawler.yml`
- `ingest.yml`
- `agents.yml`

Admin tokens must be placed in `secrets/admin_tokens` (one token per line).

## ragaictl Commands

```bash
./tools/ragaictl start
./tools/ragaictl stop
./tools/ragaictl status
./tools/ragaictl logs api
./tools/ragaictl build
./tools/ragaictl rebuild --no-cache
./tools/ragaictl restart api
./tools/ragaictl dump_project --scope all-code --max-lines 2000
```

Dump scopes:
- `all-code` (default, no data, no secrets)
- `all` (includes data, excludes secrets)
- `api`
- `crawler`
- `ingestor`
- `frontend`

Project dumps are written to `dumps/` by default.

## Frontend Usage

### Conversations

Use the Conversations page to manage saved threads:
- Open a conversation by clicking **Open** to jump into `chat.html` with the selected `conversation_id`.
- Rename inline, delete, or export a conversation log.

### Admin Workflow

1. **Unlock** the admin console with a token from `secrets/admin_tokens`.
2. Update `allow_block` configuration and click **Save Config**.
3. Trigger a crawl, monitor logs, and export/delete logs as needed.
4. Trigger ingest to push artifacts into Qdrant.
5. Chat in the Chat UI once ingest completes.

### API URL Overrides

The frontend reads `window.API_URL` (or `localStorage.API_URL`) before falling back to `http://localhost:8000`.
You can set a custom API URL by running this in the browser console:

```js
localStorage.setItem('API_URL', 'http://your-api-host:8000');
```

Reload the page to apply the change.
