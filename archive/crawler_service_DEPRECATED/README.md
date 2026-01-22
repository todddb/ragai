# Crawler Service (DEPRECATED)

⚠️ **This service is deprecated and not used by RagAI as of 2026-01-22.**

## Why this exists
The original crawler ran as a separate Docker service. Crawling is now performed
inside the `ragai-api` container using Playwright, which simplifies:
- authentication handling
- shared configuration
- operational complexity

## Status
- ❌ Not referenced in docker-compose.yml
- ❌ Not started by ragaictl
- ❌ Not maintained
- ✅ Kept temporarily for reference only

## Removal plan
This directory is expected to be **deleted entirely** after a short stabilization
period once the new crawl/auth flow is confirmed stable.

If you are reading this and wondering whether you need this service: **you do not**.
