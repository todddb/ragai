# RagAI

Local-first agentic RAG system powered by Ollama, Qdrant, and FastAPI.

## Documentation

- **[Installation Guide](docs/INSTALL.md)** - Complete setup instructions, GPU configuration, and Playwright setup
- **[User Guide](docs/USER_GUIDE.md)** - How to use the chat interface, manage conversations, and configure settings
- **[Admin Guide](docs/ADMIN_GUIDE.md)** - Admin console, crawl/ingest management, and CLI tool reference

## Quick Start

### First-Time Setup

For a fully automated setup:

```bash
git clone <your-repo-url>
cd ragai
./tools/ragaictl setup-new
```

This will create directories, install dependencies, pull Ollama models, build images, and start services.

### Manual Start

If already installed:

```bash
./tools/ragaictl start
```

Access:
- **Frontend**: http://localhost:5000
- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

Before your first crawl, update `config/allow_block.yml` with your `seed_urls` and `allowed_domains`.

## Services

- **API**: FastAPI orchestrator (chat, admin, config, crawling via integrated worker)
- **Ingestor**: Embedding + Qdrant upsert
- **Frontend**: Static HTML/CSS/JS UI

**Note**: The old dedicated crawler service has been archived to `archive/crawler_service_DEPRECATED`. Crawling is now performed directly by the API container using the crawl worker (`services/api/app/workers/crawl_worker.py`). Crawler behavior and Playwright authentication profiles are still configured via `config/crawler.yml`.

## Configuration

Configs live in `config/`:
- `system.yml`
- `allow_block.yml`
- `crawler.yml`
- `ingest.yml`
- `agents.yml`

Admin tokens must be placed in `secrets/admin_tokens` (one token per line).

## Playwright-authenticated crawling (policy.byu.edu)

To crawl authenticated pages on `policy.byu.edu`, generate a Playwright storage state file using
the interactive auth capture tool and configure auth profiles in `config/crawler.yml`.

### 1) Generate a storage state file

Install Playwright locally (or in your virtual environment), then run the helper script:

```bash
pip install playwright==1.47.2
python -m playwright install chromium
python tools/capture_auth_state.py
```

The script will:
- Guide you through creating or selecting an auth profile
- Open a browser window for you to log in (including MFA)
- Validate that authentication works by testing protected URLs
- Save the storage state to the configured path

The JSON file is ignored by git.

### 2) Verify crawler config

The capture tool automatically updates `config/crawler.yml` with your auth profile. Verify it includes:

```yaml
playwright:
  enabled: true
  auth_profiles:
    policy_cas:
      storage_state_path: secrets/playwright/policy-byu-storageState.json
      use_for_domains:
        - policy.byu.edu
      start_url: https://policy.byu.edu/
      test_urls:
        - https://policy.byu.edu/view/business-gifts-and-entertainment-policy
  headless: true
  navigation_timeout_ms: 60000
```

### 3) Restart the API container

Playwright is integrated into the API container (which handles crawling). Restart to apply changes:

```bash
./tools/ragaictl restart api
```

Run a crawl via the admin console and confirm logs include `FETCH=playwright` for `policy.byu.edu` URLs.

## GPU Acceleration (Ollama + NVIDIA)

To enable GPU-accelerated inference with Ollama (recommended for RTX-class GPUs), ensure the
host has NVIDIA drivers installed and that Docker can access the GPU.

### WSL2 (Ubuntu) Prerequisites

1. Verify GPU visibility in WSL2:

```bash
nvidia-smi
```

2. Install NVIDIA Container Toolkit (if not already installed):

```bash
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

3. Confirm Docker can access the GPU:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### Compose Configuration

The `ollama` service is configured to request NVIDIA GPUs (see `docker-compose.yml`) and sets:

- `OLLAMA_NUM_GPU=1`
- `OLLAMA_GPU_OVERHEAD=0`

### Verifying GPU Usage

Run the helper script after starting the stack:

```bash
./tools/verify_gpu.sh
```

The script prints `nvidia-smi` output before/after a short generation against Ollama and should
show non-zero GPU utilization during the request.

## ragaictl Commands

```bash
./tools/ragaictl start
./tools/ragaictl stop
./tools/ragaictl status
./tools/ragaictl logs api
./tools/ragaictl build
./tools/ragaictl build --no-cache
./tools/ragaictl restart api
./tools/ragaictl export_code all-code --max-lines 2000
```

Export scopes:
- `all-code` (default, no data, no secrets)
- `all` (includes data, excludes secrets)
- `api`
- `ingestor`
- `frontend`

Project exports are written to `dumps/` by default.

## Frontend Usage

### Conversations

Use the Conversations page to manage saved threads:
- Open a conversation by selecting the conversation card to jump into `chat.html` with the selected `conversation_id`.
- Rename inline, delete, or export a conversation log.

### Admin Workflow

1. **Open Settings → Admin** and unlock the admin console with a token from `secrets/admin_tokens`.
2. Update `allow_block` configuration and click **Save Config**.
3. Trigger a crawl, monitor logs, and export/delete logs as needed.
4. Trigger ingest to push artifacts into Qdrant.
5. Chat in the Chat UI once ingest completes.

### Connection Awareness

Use **Settings → Connection** to verify API and Ollama connectivity. The UI can also override
the API base URL via localStorage for pointing at a remote API.

Health endpoint:

```
GET /api/health
```

### API URL Overrides

The frontend reads `window.API_URL` (or `localStorage.API_URL`) before falling back to `http://localhost:8000`.
You can set a custom API URL by running this in the browser console:

```js
localStorage.setItem('API_URL', 'http://your-api-host:8000');
```

Reload the page to apply the change.

## Learn More

For detailed information on installation, usage, and administration:

- **[Installation Guide](docs/INSTALL.md)** - Prerequisites, setup, GPU acceleration, troubleshooting
- **[User Guide](docs/USER_GUIDE.md)** - Chat interface, conversations, settings, tips and best practices
- **[Admin Guide](docs/ADMIN_GUIDE.md)** - Configuration, crawl/ingest management, CLI reference, monitoring

## Contributing

Contributions are welcome! Please see the documentation guides for understanding the codebase structure and features.
