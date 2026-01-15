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

## Utilities

```bash
./tools/ragaictl status
./tools/ragaictl logs api
./tools/ragaictl build
./tools/ragaictl dump_project --scope all-code --max-lines 2000
```
