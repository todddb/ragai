# RagAI Installation Guide

This guide walks you through installing and setting up RagAI on your system.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Installation](#quick-installation)
- [Detailed Setup](#detailed-setup)
- [GPU Acceleration (Optional)](#gpu-acceleration-optional)
- [Playwright Setup for Authenticated Crawling (Optional)](#playwright-setup-for-authenticated-crawling-optional)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### Required Software

- **Docker** (version 20.10 or later)
- **Docker Compose** (version 2.0 or later)
- **Git**
- **Bash** (for running ragaictl)
- **curl** (for health checks)

### System Requirements

- **Minimum**: 8GB RAM, 20GB disk space
- **Recommended**: 16GB+ RAM, 50GB+ disk space
- **GPU (Optional)**: NVIDIA GPU with 8GB+ VRAM for accelerated inference

### Operating Systems

- Linux (Ubuntu 20.04+, Debian 11+, etc.)
- macOS (11.0+)
- Windows (via WSL2 with Ubuntu)

## Quick Installation

For a fully automated setup, use the `setup-new` command:

```bash
git clone <your-repo-url>
cd ragai
./tools/ragaictl setup-new
```

This command will:
- Create all necessary directories
- Install required system packages (Ubuntu/Debian)
- Create empty configuration files
- Pull Ollama models
- Build Docker images
- Start all services

Skip to [Verification](#verification) if using this method.

## Detailed Setup

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd ragai
```

### 2. Create Required Directories

```bash
mkdir -p secrets/playwright
mkdir -p data/{artifacts,conversations,ingest,logs/jobs,candidates,sqlite}
```

### 3. Set Up Admin Tokens

Create an admin token file for accessing the admin interface:

```bash
# Generate a random token and save it
echo "$(openssl rand -hex 32)" > secrets/admin-tokens
chmod 600 secrets/admin-tokens
```

**Important**: Save this token! You'll need it to access the admin interface.

### 4. Configure Seed URLs

Edit `config/allow_block.yml` to specify what domains to crawl:

```yaml
seed_urls:
  - https://example.com/docs
  - https://yourdomain.com/wiki

allowed_domains:
  - example.com
  - yourdomain.com

blocked_domains: []

allowed_paths: []

blocked_paths:
  - /api/
  - /admin/
```

### 5. Pull Ollama Models

RagAI requires specific Ollama models for embeddings and text generation. Pull them before first use:

```bash
# Start the Ollama service
docker compose up -d ollama

# Pull the embedding model (required)
docker compose exec ollama ollama pull nomic-embed-text:latest

# Pull your preferred LLM (choose one or more)
docker compose exec ollama ollama pull llama3.2:latest
docker compose exec ollama ollama pull mistral:latest
docker compose exec ollama ollama pull qwen2.5:14b
```

Update `config/system.yml` to use your chosen model:

```yaml
ollama:
  base_url: http://ollama:11434
  model: llama3.2:latest  # or mistral:latest, qwen2.5:14b, etc.
  embed_model: nomic-embed-text:latest
```

### 6. Build and Start Services

```bash
# Build all Docker images
./tools/ragaictl build

# Start all services
./tools/ragaictl start
```

The `start` command will:
- Launch Ollama, Qdrant, API, and Frontend services
- Perform health checks
- Display service status

## GPU Acceleration (Optional)

To enable GPU-accelerated inference with Ollama (highly recommended for RTX-class GPUs):

### On Linux

1. Install NVIDIA drivers:

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y nvidia-driver-535
```

2. Install NVIDIA Container Toolkit:

```bash
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

3. Verify Docker can access the GPU:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### On WSL2 (Windows)

1. Install NVIDIA drivers on Windows (not WSL)
2. In WSL2, verify GPU visibility:

```bash
nvidia-smi
```

3. Install NVIDIA Container Toolkit (same as Linux above)

### On macOS

macOS does not support NVIDIA GPUs with Docker. Use CPU inference or consider using a Linux machine for GPU acceleration.

### Verify GPU Usage

After starting RagAI:

```bash
./tools/verify_gpu.sh
```

This script runs a test inference and displays GPU utilization.

## Playwright Setup for Authenticated Crawling (Optional)

If you need to crawl sites that require authentication (e.g., behind a login wall):

### 1. Install Playwright Locally

```bash
# In a Python virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install playwright==1.47.2
python -m playwright install chromium
```

### 2. Capture Authentication State

Run the helper script to log in and save your session:

```bash
python tools/playwright_capture_state.py \
  --url https://yoursite.com \
  --output secrets/playwright/yoursite-storageState.json
```

A browser window will open. Log in to the site, then press Enter in the terminal to save the session.

### 3. Configure Crawler

Edit `config/crawler.yml`:

```yaml
playwright:
  enabled: true
  headless: true
  storage_state_path: /app/secrets/playwright/yoursite-storageState.json
  use_for_domains:
    - yoursite.com
  navigation_timeout_ms: 60000
```

### 4. Rebuild Crawler

```bash
docker compose build crawler
```

## Verification

### 1. Check Service Status

```bash
./tools/ragaictl status
```

All services should show "Up" status.

### 2. Access the Frontend

Open your browser to:

- **Frontend**: http://localhost:5000
- **API Docs**: http://localhost:8000/docs

### 3. Verify Health

```bash
curl http://localhost:8000/api/health
```

Expected response:

```json
{
  "api": "ok",
  "ollama": "ok",
  "qdrant": "ok"
}
```

### 4. Run Your First Crawl

1. Go to http://localhost:5000/settings.html
2. Click "Admin" tab
3. Enter your admin token (from `secrets/admin-tokens`)
4. Click "Start Crawl"
5. Monitor logs in the Admin interface

### 5. Ingest and Chat

1. After crawl completes, click "Start Ingest"
2. Once ingest finishes, go to http://localhost:5000/chat.html
3. Ask a question about your crawled content

## Troubleshooting

### Services won't start

```bash
# View logs for a specific service
./tools/ragaictl logs api

# Check Docker daemon
sudo systemctl status docker

# Restart Docker
sudo systemctl restart docker
```

### API health check fails

```bash
# Check API logs
docker compose logs api --tail=100

# Verify Ollama is running
curl http://localhost:11434/api/version

# Verify Qdrant is running
curl http://localhost:6333/health
```

### Ollama models not found

```bash
# List installed models
docker compose exec ollama ollama list

# Pull missing model
docker compose exec ollama ollama pull llama3.2:latest
```

### Permission denied errors

```bash
# Fix permissions on data directories
sudo chown -R $USER:$USER data/ secrets/

# Make ragaictl executable
chmod +x ./tools/ragaictl
```

### Port conflicts

If ports 8000, 5000, 11434, or 6333 are in use:

1. Stop conflicting services
2. Or modify `docker-compose.yml` to use different ports

### GPU not detected

```bash
# Verify NVIDIA drivers
nvidia-smi

# Check Docker GPU access
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

# Reinstall NVIDIA Container Toolkit
sudo apt-get install --reinstall nvidia-container-toolkit
sudo systemctl restart docker
```

### Playwright authentication fails

```bash
# Test storage state file exists
ls -la secrets/playwright/

# Recapture authentication state
python tools/playwright_capture_state.py --url https://yoursite.com --output secrets/playwright/yoursite-storageState.json

# Rebuild crawler with new state
docker compose build crawler
```

## Next Steps

- Read the [User Guide](USER_GUIDE.md) to learn how to use the chat interface
- Read the [Admin Guide](ADMIN_GUIDE.md) to learn how to manage crawls and configure settings
- Explore `config/` files to customize behavior
- Check out the API documentation at http://localhost:8000/docs
