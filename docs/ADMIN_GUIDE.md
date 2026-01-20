# RagAI Admin Guide

This guide covers administrative tasks including managing crawls, configuring settings, using the CLI tool, and maintaining the RagAI system.

## Table of Contents

- [Admin Console Access](#admin-console-access)
- [Configuration Management](#configuration-management)
- [Crawl Management](#crawl-management)
- [Ingest Management](#ingest-management)
- [CLI Tool: ragaictl](#cli-tool-ragaictl)
- [Monitoring and Logs](#monitoring-and-logs)
- [Database Management](#database-management)
- [Backup and Restore](#backup-and-restore)
- [Performance Tuning](#performance-tuning)
- [Troubleshooting](#troubleshooting)

## Admin Console Access

### Unlocking the Admin Console

1. Navigate to http://localhost:5000/settings.html
2. Click the "Admin" tab
3. Enter your admin token
4. Click "Unlock"

### Managing Admin Tokens

Admin tokens are stored in `secrets/admin-tokens` (one token per line).

**Creating tokens:**

```bash
# Generate a secure random token
echo "$(openssl rand -hex 32)" >> secrets/admin-tokens
```

**Security best practices:**

- Use long, random tokens (32+ characters)
- Keep the file permissions restrictive: `chmod 600 secrets/admin-tokens`
- Rotate tokens periodically
- Never commit tokens to version control (already in `.gitignore`)
- Share tokens securely with team members

### Admin Console Features

Once unlocked, the admin console provides:

- Configuration file editing
- Crawl job management
- Ingest job management
- Real-time log streaming
- Vector database management
- Candidate URL review

## Configuration Management

### Configuration Files

All configuration files are located in `config/`:

- **system.yml** - Ollama, Qdrant, and API settings
- **allow_block.yml** - URL seeds, domain rules, and path filters
- **crawler.yml** - Crawl behavior, depth, and Playwright settings
- **ingest.yml** - Chunking, embedding, and Qdrant collection settings
- **agents.yml** - Agent system prompts and behavior

### Editing Configuration via Admin Console

1. Access Admin Console (see above)
2. Select the configuration file from the dropdown
3. Edit the YAML content directly
4. Click "Save Config"
5. Restart relevant services if needed

### Configuration Reference

#### system.yml

```yaml
ollama:
  base_url: http://ollama:11434
  model: llama3.2:latest              # LLM for chat/agents
  embed_model: nomic-embed-text:latest # Model for embeddings
  timeout: 300                         # Request timeout (seconds)

qdrant:
  url: http://qdrant:6333
  collection_name: ragai               # Vector collection name

api:
  host: 0.0.0.0
  port: 8000
```

**Key settings:**

- `ollama.model` - Change to use different LLMs (llama3.2, mistral, qwen2.5, etc.)
- `ollama.embed_model` - Embedding model (must match ingest.yml)
- `qdrant.collection_name` - Vector database collection (change requires re-ingest)

#### allow_block.yml

```yaml
seed_urls:
  - https://example.com/docs
  - https://wiki.company.com/

allowed_domains:
  - example.com
  - wiki.company.com
  - docs.company.com

blocked_domains:
  - ads.example.com
  - tracker.company.com

allowed_paths:
  - /docs/
  - /api/reference/

blocked_paths:
  - /api/internal/
  - /admin/
  - /private/
```

**Best practices:**

- Start with specific seed URLs, not just domain homepages
- Use `allowed_domains` to prevent crawling outside your knowledge base
- Use `blocked_paths` to exclude irrelevant sections (login, admin, etc.)
- Review candidate URLs (in admin console) to refine rules

#### crawler.yml

```yaml
discovery:
  enabled: true
  max_depth: 3                    # How many links deep to follow
  max_pages: 1000                 # Maximum pages to crawl
  respect_robots_txt: true
  user_agent: "RagAI-Crawler/1.0"

capture:
  chunk_size: 512                 # Tokens per chunk
  chunk_overlap: 50               # Overlap between chunks
  min_chunk_size: 100             # Discard chunks smaller than this

playwright:
  enabled: false
  headless: true
  storage_state_path: /app/secrets/playwright/storageState.json
  use_for_domains:
    - authenticated-site.com
  navigation_timeout_ms: 60000

url_canonicalization:
  strip_params:
    - utm_source
    - utm_medium
    - utm_campaign
    - fbclid
    - ref
```

**Key settings:**

- `max_depth` - Higher values find more pages but take longer
- `max_pages` - Hard limit to prevent runaway crawls
- `chunk_size` - Smaller chunks = more precise retrieval, larger = more context
- `playwright.enabled` - Enable for JavaScript-heavy or authenticated sites

#### ingest.yml

```yaml
embedding:
  model: nomic-embed-text:latest  # Must match system.yml
  batch_size: 10                   # Embeddings per batch
  max_retries: 3

qdrant:
  collection_name: ragai           # Must match system.yml
  vector_size: 768                 # Depends on embedding model
  distance: Cosine
  hnsw_ef_construct: 100
  hnsw_m: 16

chunking:
  overlap: 50                      # Should match crawler.yml
```

**Key settings:**

- `batch_size` - Higher = faster but more memory
- `vector_size` - nomic-embed-text uses 768, don't change unless changing model
- `distance` - Cosine is standard for text embeddings

#### agents.yml

Contains system prompts for each agent in the pipeline:

- `intent_agent` - Analyzes questions and generates search queries
- `research_agent` - Summarizes search results
- `synthesis_agent` - Drafts answers with citations
- `validation_agent` - Validates answers and asks clarifying questions

**Customizing agents:**

Edit prompts to change behavior:

- Tone (formal vs. casual)
- Verbosity (brief vs. comprehensive)
- Citation style
- Domain expertise

After editing, restart the API: `./tools/ragaictl restart api`

## Crawl Management

### Starting a Crawl

1. Access Admin Console
2. Click "Start Crawl"
3. Monitor real-time logs
4. Wait for "Crawl completed" message

### Monitoring Crawl Progress

The admin console shows:

- Pages discovered
- Pages crawled
- Chunks created
- Errors encountered

Logs stream in real-time, showing each URL as it's fetched.

### Stopping a Crawl

Click "Stop Crawl" (if implemented) or:

```bash
# Find the crawl job process
docker compose ps

# Stop the crawler service
docker compose stop crawler
```

### Reviewing Crawl Results

After a crawl:

1. **Check artifacts**: `ls -la data/artifacts/`
2. **Review logs**: Admin console or `./tools/ragaictl logs`
3. **Inspect candidate URLs**: Admin console shows discovered but not crawled URLs

### Troubleshooting Crawls

**No pages found:**

- Verify `seed_urls` in `allow_block.yml`
- Check `allowed_domains` includes seed URL domains
- Review `blocked_paths` - may be too aggressive

**Crawl is slow:**

- Reduce `max_depth` in `crawler.yml`
- Set `max_pages` limit
- Enable `playwright` only for domains that need it
- Check network connectivity to target sites

**Missing content:**

- Some sites may require authentication (use Playwright)
- JavaScript-heavy sites may need Playwright
- Check `robots.txt` on target site (if `respect_robots_txt: true`)

## Ingest Management

### Starting an Ingest

1. Ensure crawl has completed and artifacts exist
2. Access Admin Console
3. Click "Start Ingest"
4. Monitor logs for progress

### Ingest Process

The ingest process:

1. Reads artifact files from `data/artifacts/`
2. Generates embeddings using Ollama
3. Upserts vectors to Qdrant
4. Tracks state in `data/ingest/metadata.db`

### Clearing the Vector Database

To start fresh:

1. Admin Console → "Clear Vector DB"
2. Confirms deletion of all vectors
3. Re-run ingest to repopulate

Or via CLI:

```bash
docker compose exec api python -c "
from qdrant_client import QdrantClient
client = QdrantClient(url='http://qdrant:6333')
client.delete_collection('ragai')
print('Collection deleted')
"
```

### Incremental Ingests

RagAI tracks ingested documents in `data/ingest/metadata.db`. Running ingest again will:

- Skip already-ingested documents
- Ingest only new artifacts
- Update modified documents (based on hash)

### Troubleshooting Ingests

**Ingest fails immediately:**

- Verify Ollama is running: `curl http://localhost:11434/api/version`
- Verify embedding model is pulled: `docker compose exec ollama ollama list`
- Check Qdrant: `curl http://localhost:6333/health`

**Ingest is slow:**

- Enable GPU acceleration (see [Install Guide](INSTALL.md))
- Increase `batch_size` in `ingest.yml` (if you have enough RAM)
- Use a faster embedding model (trade-off with quality)

**Vector dimension mismatch:**

- Ensure `vector_size` in `ingest.yml` matches your embedding model
- nomic-embed-text = 768 dimensions
- If changed, must clear vector DB and re-ingest

## CLI Tool: ragaictl

The `ragaictl` tool is your primary interface for managing RagAI services.

### Commands

#### setup-new

Fully automated setup for new installations.

```bash
./tools/ragaictl setup-new
```

**What it does:**

- Creates all required directories (`data/`, `secrets/`, etc.)
- Installs system dependencies (Ubuntu/Debian only)
  - `curl`, `git`, `python3`, `python3-pip`, `python3-venv`
- Creates empty `secrets/admin-tokens` file
- Generates example admin token
- Pulls Ollama models:
  - `nomic-embed-text:latest` (embedding model)
  - `llama3.2:latest` (LLM)
- Builds all Docker images
- Starts all services
- Runs health checks

**Usage:**

```bash
# First-time setup
./tools/ragaictl setup-new

# After completion, access frontend at http://localhost:5000
```

**Notes:**

- Requires sudo access for apt package installation
- Takes 10-30 minutes depending on network speed (Ollama models are large)
- Safe to re-run (idempotent where possible)
- On non-Debian systems, install dependencies manually first

#### start

Starts all RagAI services.

```bash
./tools/ragaictl start
```

**What it does:**

- Starts Ollama, Qdrant, API, and Frontend services
- Waits up to 30 seconds for API to become available
- Performs health checks on API, Ollama, and Qdrant
- Displays service status with visual indicators

**Example output:**

```
⏳ Waiting for API to become available...
✅ API is responding

=== Health Check Summary ===
✅ API OK
✅ Ollama OK
✅ Qdrant OK
===========================
```

#### stop

Stops all services.

```bash
./tools/ragaictl stop
```

Equivalent to `docker compose down`.

#### status

Shows current service status.

```bash
./tools/ragaictl status
```

**Example output:**

```
NAME                   IMAGE              STATUS          PORTS
ragai-ollama-1         ollama/ollama      Up 2 hours      11434/tcp
ragai-qdrant-1         qdrant/qdrant      Up 2 hours      6333/tcp
ragai-api-1            ragai-api          Up 2 hours      8000/tcp
ragai-frontend-1       nginx:alpine       Up 2 hours      80/tcp->5000/tcp
```

#### logs

View and follow service logs.

```bash
# All services
./tools/ragaictl logs

# Specific service
./tools/ragaictl logs api
./tools/ragaictl logs ollama
./tools/ragaictl logs qdrant
./tools/ragaictl logs frontend

# Last 100 lines
docker compose logs api --tail=100
```

**Use cases:**

- Debugging startup issues
- Monitoring crawl/ingest jobs
- Troubleshooting API errors
- Checking Ollama model loading

#### build

Builds Docker images.

```bash
./tools/ragaictl build
```

**When to use:**

- After cloning the repository
- After modifying service code
- After updating dependencies in `requirements.txt`

#### rebuild

Clean rebuild of images.

```bash
# Standard rebuild
./tools/ragaictl rebuild

# Force rebuild without cache
./tools/ragaictl rebuild --no-cache
```

**`--no-cache` option:**

- Forces Docker to rebuild every layer
- Useful when troubleshooting image issues
- Takes longer but ensures clean state

**What it does (with --no-cache):**

1. Stops all services
2. Removes containers
3. Rebuilds images without cache
4. Prunes unused images

#### restart

Restarts services.

```bash
# Restart all services
./tools/ragaictl restart

# Restart specific service
./tools/ragaictl restart api
./tools/ragaictl restart ollama
```

**Use cases:**

- Applying configuration changes
- Recovering from service crash
- Forcing reconnection to dependencies

#### dump_project

Creates a snapshot of project code and data.

```bash
# Default: code only, no data, no secrets
./tools/ragaictl dump_project

# Include data
./tools/ragaictl dump_project --scope all

# Specific service
./tools/ragaictl dump_project --scope api

# Limit line length
./tools/ragaictl dump_project --max-lines 2000
```

**Scopes:**

- `all-code` (default) - All code files, excludes data and secrets
- `all` - Code + data, excludes secrets
- `api` - API service only
- `crawler` - Crawler service only
- `ingestor` - Ingestor service only
- `frontend` - Frontend service only

**Output location:**

Dumps are saved to `dumps/` directory with timestamp:

```
dumps/ragai-dump-20250120-143022.txt
```

**Use cases:**

- Debugging with LLM assistance (paste dump to Claude, etc.)
- Code review
- Backup before major changes
- Sharing project structure with team

## Monitoring and Logs

### Service Health

Monitor service health via:

1. **Web UI**: http://localhost:5000/settings.html → Connection tab
2. **API**: `curl http://localhost:8000/api/health`
3. **CLI**: `./tools/ragaictl status`

### Log Locations

**Docker logs (stdout/stderr):**

```bash
./tools/ragaictl logs api
./tools/ragaictl logs crawler
./tools/ragaictl logs ingestor
```

**Job logs (file-based):**

- Crawl logs: `data/logs/jobs/crawl-*.log`
- Ingest logs: `data/logs/jobs/ingest-*.log`

### Real-time Monitoring

**Admin console:**

Provides real-time streaming logs for active crawl/ingest jobs.

**Terminal:**

```bash
# Follow API logs
./tools/ragaictl logs api

# Follow all logs
./tools/ragaictl logs

# Tail last 50 lines
docker compose logs api --tail=50
```

### Log Retention

Logs are retained indefinitely by default. To clean up:

```bash
# Remove old job logs
find data/logs/jobs/ -name "*.log" -mtime +30 -delete

# Clear Docker logs (stops containers first)
./tools/ragaictl stop
docker compose logs --no-log-prefix > /dev/null 2>&1
./tools/ragaictl start
```

## Database Management

### SQLite Databases

RagAI uses SQLite for:

1. **Conversations**: `data/conversations/conversations.db`
2. **Ingest metadata**: `data/ingest/metadata.db`
3. **Structured data**: `data/sqlite/structured.db` (Excel cell storage)

### Backing Up Databases

```bash
# Backup conversations
cp data/conversations/conversations.db backups/conversations-$(date +%Y%m%d).db

# Backup all databases
tar -czf backups/databases-$(date +%Y%m%d).tar.gz data/conversations/ data/ingest/ data/sqlite/
```

### Inspecting Databases

```bash
# Open conversations database
sqlite3 data/conversations/conversations.db

# List tables
.tables

# Query conversations
SELECT id, title, created_at FROM conversations ORDER BY updated_at DESC LIMIT 10;

# Exit
.quit
```

### Resetting Databases

**Clear all conversations:**

```bash
rm data/conversations/conversations.db
./tools/ragaictl restart api
```

**Clear ingest state (force re-ingest):**

```bash
rm data/ingest/metadata.db
# Next ingest will reprocess all artifacts
```

### Qdrant Vector Database

**Access Qdrant console:**

http://localhost:6333/dashboard

**Clear collection via API:**

```bash
curl -X DELETE http://localhost:6333/collections/ragai
```

**Recreate collection:**

```bash
# Run an ingest - will automatically recreate collection
# Or via Admin Console → Clear Vector DB
```

## Backup and Restore

### What to Backup

**Essential:**

- `config/` - All configuration files
- `secrets/` - Admin tokens, Playwright state (NEVER commit to git)
- `data/conversations/` - Chat history
- `data/artifacts/` - Crawled content

**Optional:**

- `data/ingest/metadata.db` - Ingest state (can be rebuilt)
- Qdrant vector database (can be rebuilt from artifacts)

### Backup Script

```bash
#!/bin/bash
BACKUP_DIR="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Config and secrets
cp -r config/ "$BACKUP_DIR/"
cp -r secrets/ "$BACKUP_DIR/"

# Data
cp -r data/conversations/ "$BACKUP_DIR/"
cp -r data/artifacts/ "$BACKUP_DIR/"
cp -r data/sqlite/ "$BACKUP_DIR/"

# Compress
tar -czf "$BACKUP_DIR.tar.gz" "$BACKUP_DIR"
rm -rf "$BACKUP_DIR"

echo "Backup created: $BACKUP_DIR.tar.gz"
```

### Restore Procedure

```bash
# Stop services
./tools/ragaictl stop

# Extract backup
tar -xzf backups/20250120-143000.tar.gz

# Restore files
cp -r 20250120-143000/config/* config/
cp -r 20250120-143000/secrets/* secrets/
cp -r 20250120-143000/data/conversations/* data/conversations/
cp -r 20250120-143000/data/artifacts/* data/artifacts/

# Clear vector DB and re-ingest
rm -rf data/ingest/metadata.db

# Start services
./tools/ragaictl start

# Re-ingest (via Admin Console or API)
```

## Performance Tuning

### GPU Acceleration

See [Installation Guide](INSTALL.md) for GPU setup.

**Verify GPU usage:**

```bash
./tools/verify_gpu.sh
```

**Check GPU utilization:**

```bash
# While RagAI is processing a query
nvidia-smi -l 1  # Updates every second
```

### Model Selection

**Faster models (lower quality):**

- `llama3.2:1b` - Smallest, fastest
- `llama3.2:3b` - Good balance
- `mistral:7b` - Decent quality, reasonable speed

**Higher quality (slower):**

- `llama3.2:latest` (8B) - Good default
- `qwen2.5:14b` - High quality
- `llama3.1:70b` - Best quality (requires lots of VRAM)

**Change model:**

Edit `config/system.yml`:

```yaml
ollama:
  model: llama3.2:3b  # Change this
```

Restart API: `./tools/ragaictl restart api`

### Chunk Size Optimization

**Smaller chunks (512 tokens):**

- More precise retrieval
- Better for factual Q&A
- More chunks = larger vector DB

**Larger chunks (1024+ tokens):**

- More context per chunk
- Better for narrative/explanatory content
- Fewer chunks = smaller vector DB

Edit `config/crawler.yml` and `config/ingest.yml`, then re-crawl and re-ingest.

### Batch Size Tuning

Edit `config/ingest.yml`:

```yaml
embedding:
  batch_size: 10  # Increase if you have RAM
```

**Guidelines:**

- CPU: 5-10
- GPU (8GB VRAM): 20-50
- GPU (24GB VRAM): 50-100

### Resource Limits

Edit `docker-compose.yml` to set memory/CPU limits:

```yaml
services:
  api:
    deploy:
      resources:
        limits:
          memory: 4G
        reservations:
          memory: 2G
```

## Troubleshooting

### Services Won't Start

```bash
# Check Docker daemon
sudo systemctl status docker

# Check for port conflicts
sudo lsof -i :8000  # API
sudo lsof -i :5000  # Frontend
sudo lsof -i :11434 # Ollama
sudo lsof -i :6333  # Qdrant

# Check logs
./tools/ragaictl logs api
```

### Admin Console Won't Unlock

- Verify token in `secrets/admin-tokens`
- Check for whitespace/newlines in token
- Try generating a new token
- Check browser console for errors (F12)

### Crawl Produces No Results

- Verify `seed_urls` and `allowed_domains` in `config/allow_block.yml`
- Check crawl logs for errors
- Test URLs manually: `curl https://your-site.com`
- Check `blocked_paths` - may be too broad

### Ingest Fails

- Verify Ollama is running and models are pulled
- Check embedding model matches between `system.yml` and `ingest.yml`
- Verify artifacts exist: `ls data/artifacts/`
- Check Qdrant is healthy: `curl http://localhost:6333/health`

### Chat Produces Poor Answers

- Verify relevant content was crawled
- Check vector search is finding chunks (see logs)
- Try adjusting chunk size (smaller = more precise)
- Improve agent prompts in `config/agents.yml`
- Use a better LLM model

### High Memory Usage

- Reduce Ollama model size
- Limit ingest `batch_size`
- Set Docker memory limits
- Clear old conversations
- Reduce `max_pages` in crawler config

### GPU Not Used

```bash
# Verify Docker GPU access
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

# Check Ollama GPU config
docker compose exec ollama env | grep OLLAMA

# Restart Ollama with GPU
./tools/ragaictl restart ollama
```

## Maintenance Tasks

### Weekly

- Review crawl logs for errors
- Check disk usage: `du -sh data/`
- Verify backups are current

### Monthly

- Rotate admin tokens
- Clean old job logs
- Update Ollama models: `docker compose exec ollama ollama pull <model>`
- Review and optimize `allow_block.yml` rules

### As Needed

- Re-crawl sites when content updates
- Re-ingest after crawl configuration changes
- Clear vector DB if changing embedding model
- Rebuild images after dependency updates

## Security Considerations

- Keep `secrets/` secure and never commit to git
- Use strong admin tokens (32+ character random strings)
- Run RagAI on trusted networks (no public exposure without authentication)
- Review crawled content for sensitive information before ingesting
- Regularly update Docker images and dependencies
- Monitor logs for suspicious activity
- Use HTTPS for production deployments (reverse proxy)

## Next Steps

- Explore [User Guide](USER_GUIDE.md) for end-user features
- Review [Installation Guide](INSTALL.md) for setup details
- Customize agent prompts in `config/agents.yml`
- Set up scheduled crawls via cron
- Integrate with external authentication (SSO, OAuth)
- Deploy to production with Docker Swarm or Kubernetes
