import asyncio
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from app.utils.auth_validation import (
    playwright_available,
    validate_auth_profile,
)
from app.utils.config import refresh_config, write_yaml_config
from app.utils.jobs import delete_job, get_job, list_jobs, start_job
from app.utils.ollama_embed import embed_text
from app.workers.ingest_worker import DB_PATH, ensure_metadata_db_initialized, run_ingest_job

router = APIRouter(prefix="/api/admin", tags=["admin"])

SECRETS_PATH = Path("/app/secrets/admin_tokens")
CONFIG_DIR = Path("/app/config")
CANDIDATES_PATH = Path("/app/data/candidates/candidates.jsonl")
PROCESSED_PATH = Path("/app/data/candidates/processed.json")
AUTH_HINTS_PATH = Path("/app/data/logs/auth_hints.json")
SUMMARY_DIR = Path("/app/data/logs/summaries")
QUARANTINE_DIR = Path("/app/data/quarantine")
QUARANTINE_AUDIT_LOG = Path("/app/data/logs/quarantine_audit.log")
ALLOWED_URL_STATUS_TTL_SECONDS = 60
_ALLOWED_URL_STATUS_CACHE: Dict[str, Any] = {"timestamp": 0.0, "payload": None}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_validation(command: List[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise HTTPException(
            status_code=500,
            detail=(
                "Validation failed. "
                f"stdout: {result.stdout.strip()} stderr: {result.stderr.strip()}"
            ),
        )


def _latest_summary(prefix: str) -> Optional[Path]:
    """
    Return the newest summary JSON file matching prefix, or None if unavailable.
    """
    if not SUMMARY_DIR.exists():
        return None

    summaries = sorted(
        SUMMARY_DIR.glob(f"{prefix}*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not summaries:
        return None

    # Extra safety: ensure we only return files
    for p in summaries:
        if p.is_file():
            return p

    return None

def _format_crawl_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    findings = payload.get("findings", [])
    by_doc: Dict[str, Dict[str, Any]] = {}
    severity_weight = {"low": 1, "medium": 2, "high": 3}
    for finding in findings:
        doc_id = finding.get("doc_id") or finding.get("artifact_dir") or "unknown"
        current = by_doc.get(doc_id)
        severity = finding.get("severity", "low")
        weight = severity_weight.get(severity, 1)
        if not current:
            by_doc[doc_id] = {
                "id": doc_id,
                "url": finding.get("url", ""),
                "title": finding.get("message", "Finding"),
                "risk_score": weight,
                "reason": finding.get("message", ""),
                "severity": severity,
                "artifact_dir": finding.get("artifact_dir"),
            }
        else:
            current["risk_score"] = max(current["risk_score"], weight)
            current["reason"] = f"{current['reason']}; {finding.get('message', '')}".strip("; ")
    return {
        "summary": {
            "total": payload.get("artifacts_validated", 0),
            "flagged": len(findings),
            "quarantined": len(payload.get("quarantined", [])),
        },
        "validated": list(by_doc.values()),
        "raw": payload,
    }


def _format_ingest_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    counts = payload.get("finding_counts", {}) or {}
    findings = payload.get("findings", []) or []
    return {
        "summary": {
            "total": sum(counts.values()),
            "high": counts.get("high", 0),
            "medium": counts.get("medium", 0),
            "low": counts.get("low", 0),
        },
        "findings": findings,
        "raw": payload,
    }


def _rule_matches_url(pattern: str, match_type: str, url: str) -> bool:
    if match_type == "exact":
        return pattern == url
    return url.startswith(pattern)


def _get_auth_hint_for_rule(rule: Dict[str, Any], hints: Dict[str, Any]) -> bool:
    pattern = rule.get("pattern", "")
    match_type = rule.get("match", "prefix")
    if not pattern:
        return False
    for entry in hints.get("recent", []) or []:
        original_url = entry.get("original_url") or ""
        if original_url and _rule_matches_url(pattern, match_type, original_url):
            return True
    host = ""
    try:
        host = urlparse(pattern).hostname or ""
    except Exception:
        host = ""
    return bool(host and hints.get("by_domain", {}).get(host))


def _allowed_url_status_cache_fresh() -> bool:
    return (datetime.now(timezone.utc).timestamp() - float(_ALLOWED_URL_STATUS_CACHE.get("timestamp", 0.0))) < ALLOWED_URL_STATUS_TTL_SECONDS


def _parse_redis_host_port() -> Dict[str, Any]:
    redis_url = os.getenv("REDIS_HOST", "redis://redis:6379/0")
    if redis_url.startswith("redis://"):
        host_port = redis_url.replace("redis://", "").split("/")[0]
        if ":" in host_port:
            host, port = host_port.split(":", 1)
            return {"host": host, "port": int(port)}
        return {"host": host_port, "port": 6379}
    return {"host": "redis", "port": 6379}


def _load_tokens() -> List[str]:
    if not SECRETS_PATH.exists():
        return []
    return [line.strip() for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


@router.post("/unlock")
async def unlock(payload: Dict[str, str]) -> Dict[str, str]:
    token = payload.get("token")
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")
    if token not in _load_tokens():
        raise HTTPException(status_code=403, detail="Invalid token")
    return {"status": "ok"}


@router.get("/config/{name}")
async def get_config(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / f"{name}.yml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Config not found")
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if name == "allow_block" and "allow_rules" in config:
        updated = False
        for rule in config.get("allow_rules", []):
            if isinstance(rule, dict):
                if not rule.get("id"):
                    _ensure_rule_id(rule)
                    updated = True
        if updated:
            write_yaml_config(path, config)
            refresh_config("allow_block")
    return config


@router.put("/config/{name}")
async def update_config(name: str, payload: Dict[str, Any]) -> Dict[str, str]:
    path = CONFIG_DIR / f"{name}.yml"
    try:
        write_yaml_config(path, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refresh_config(name)
    return {"status": "ok"}


def _derive_allowed_domains(allow_rules: List[Dict[str, Any]]) -> List[str]:
    """Derive allowed_domains from allow_rules patterns."""
    domains = set()
    for rule in allow_rules:
        pattern = rule.get("pattern", "")
        try:
            parsed = urlparse(pattern)
            if parsed.netloc:
                domains.add(parsed.netloc)
        except Exception:
            continue
    return sorted(domains)


def _ensure_rule_id(rule: Dict[str, Any]) -> str:
    """Ensure a rule has an ID, generating one if missing."""
    if "id" not in rule or not rule["id"]:
        rule["id"] = str(uuid.uuid4())
    return rule["id"]


@router.post("/allowed-urls")
async def create_allowed_url(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new allowed URL rule."""
    # Validate required fields
    if "pattern" not in payload or not payload["pattern"]:
        raise HTTPException(status_code=400, detail="Missing required field: pattern")

    # Set defaults
    rule = {
        "id": str(uuid.uuid4()),
        "pattern": payload["pattern"],
        "match": payload.get("match", "prefix"),
        "types": payload.get("types", {
            "web": True,
            "pdf": False,
            "docx": False,
            "xlsx": False,
            "pptx": False,
        }),
        "allow_http": payload.get("allow_http", False),
        "auth_profile": payload.get("auth_profile"),
    }
    rule["playwright"] = bool(rule.get("auth_profile"))

    # Validate match type
    if rule["match"] not in ["prefix", "exact"]:
        raise HTTPException(status_code=400, detail="Invalid match type (must be 'prefix' or 'exact')")

    # Load current config
    path = CONFIG_DIR / "allow_block.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Ensure allow_rules exists
    if "allow_rules" not in config:
        config["allow_rules"] = []

    # Ensure existing rules have IDs
    for existing_rule in config["allow_rules"]:
        _ensure_rule_id(existing_rule)

    # Add new rule
    config["allow_rules"].append(rule)

    # Update allowed_domains
    config["allowed_domains"] = _derive_allowed_domains(config["allow_rules"])

    # Save config
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    refresh_config("allow_block")

    return rule


@router.put("/allowed-urls/{rule_id}")
async def update_allowed_url(rule_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing allowed URL rule."""
    # Load current config
    path = CONFIG_DIR / "allow_block.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    if "allow_rules" not in config:
        raise HTTPException(status_code=404, detail="No allow rules found")

    # Ensure existing rules have IDs
    for existing_rule in config["allow_rules"]:
        _ensure_rule_id(existing_rule)

    # Find the rule to update
    rule_index = None
    for i, rule in enumerate(config["allow_rules"]):
        if rule.get("id") == rule_id:
            rule_index = i
            break

    if rule_index is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Update the rule
    updated_rule = {
        "id": rule_id,
        "pattern": payload.get("pattern", config["allow_rules"][rule_index].get("pattern")),
        "match": payload.get("match", config["allow_rules"][rule_index].get("match", "prefix")),
        "types": payload.get("types", config["allow_rules"][rule_index].get("types", {})),
        "allow_http": payload.get("allow_http", config["allow_rules"][rule_index].get("allow_http", False)),
        "auth_profile": payload.get("auth_profile", config["allow_rules"][rule_index].get("auth_profile")),
    }
    updated_rule["playwright"] = bool(updated_rule.get("auth_profile"))

    # Validate match type
    if updated_rule["match"] not in ["prefix", "exact"]:
        raise HTTPException(status_code=400, detail="Invalid match type (must be 'prefix' or 'exact')")

    # Validate pattern
    if not updated_rule["pattern"]:
        raise HTTPException(status_code=400, detail="Pattern cannot be empty")

    # Update the rule in config
    config["allow_rules"][rule_index] = updated_rule

    # Update allowed_domains
    config["allowed_domains"] = _derive_allowed_domains(config["allow_rules"])

    # Save config
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    refresh_config("allow_block")

    return updated_rule


@router.delete("/allowed-urls/{rule_id}")
async def delete_allowed_url(rule_id: str) -> Dict[str, str]:
    """Delete an allowed URL rule."""
    # Load current config
    path = CONFIG_DIR / "allow_block.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    if "allow_rules" not in config:
        raise HTTPException(status_code=404, detail="No allow rules found")

    # Ensure existing rules have IDs
    for existing_rule in config["allow_rules"]:
        _ensure_rule_id(existing_rule)

    # Find and remove the rule
    original_count = len(config["allow_rules"])
    config["allow_rules"] = [rule for rule in config["allow_rules"] if rule.get("id") != rule_id]

    if len(config["allow_rules"]) == original_count:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Update allowed_domains
    config["allowed_domains"] = _derive_allowed_domains(config["allow_rules"])

    # Save config
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    refresh_config("allow_block")

    return {"status": "ok"}


@router.put("/playwright-settings")
async def update_playwright_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Update Playwright settings (enabled flag and auth profiles)."""
    # Load current crawler config
    path = CONFIG_DIR / "crawler.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Ensure playwright section exists
    if "playwright" not in config:
        config["playwright"] = {}

    # Update enabled flag if provided
    if "enabled" in payload:
        config["playwright"]["enabled"] = bool(payload["enabled"])

    # Update auth_profiles if provided
    if "auth_profiles" in payload:
        config["playwright"]["auth_profiles"] = payload["auth_profiles"]

    # Preserve other playwright settings
    for key in ["headless", "navigation_timeout_ms"]:
        if key not in config["playwright"] and key in payload:
            config["playwright"][key] = payload[key]

    # Save config
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    refresh_config("crawler")

    return config["playwright"]


@router.get("/allowed-urls/auth-status")
async def allowed_urls_auth_status() -> Dict[str, Any]:
    if _allowed_url_status_cache_fresh() and _ALLOWED_URL_STATUS_CACHE.get("payload"):
        return _ALLOWED_URL_STATUS_CACHE["payload"]

    allow_block = yaml.safe_load((CONFIG_DIR / "allow_block.yml").read_text(encoding="utf-8")) or {}
    crawler_config = yaml.safe_load((CONFIG_DIR / "crawler.yml").read_text(encoding="utf-8")) or {}
    playwright_config = crawler_config.get("playwright", {})
    profiles = playwright_config.get("auth_profiles", {})

    auth_hints = {"by_domain": {}, "recent": []}
    if AUTH_HINTS_PATH.exists():
        try:
            auth_hints = json.loads(AUTH_HINTS_PATH.read_text(encoding="utf-8")) or auth_hints
        except json.JSONDecodeError:
            auth_hints = {"by_domain": {}, "recent": []}

    allow_rules = allow_block.get("allow_rules", []) or []
    playwright_ok = playwright_available()
    rules_payload = []

    updated = False
    for rule in allow_rules:
        if isinstance(rule, dict) and not rule.get("id"):
            _ensure_rule_id(rule)
            updated = True

    if updated:
        write_yaml_config(CONFIG_DIR / "allow_block.yml", allow_block)
        refresh_config("allow_block")

    for rule in allow_rules:
        if not isinstance(rule, dict):
            continue
        rule_id = rule.get("id") or str(uuid.uuid4())
        pattern = rule.get("pattern", "")
        auth_profile = rule.get("auth_profile") or rule.get("authProfile")
        auth_required_hint = _get_auth_hint_for_rule(rule, auth_hints)

        auth_test = None
        ui_status = "unknown"

        if auth_profile:
            profile = profiles.get(auth_profile)
            if not profile:
                ui_status = "invalid"
                auth_test = {
                    "profile_name": auth_profile,
                    "ok": False,
                    "final_url": "",
                    "title": "",
                    "status": None,
                    "error_reason": "auth profile not found",
                    "checked_at": _utcnow(),
                }
            elif not playwright_ok:
                ui_status = "cannot_test"
                auth_test = {
                    "profile_name": auth_profile,
                    "ok": False,
                    "final_url": "",
                    "title": "",
                    "status": None,
                    "error_reason": "playwright unavailable",
                    "checked_at": _utcnow(),
                }
            else:
                candidate_url = (
                    profile.get("test_url")
                    or (profile.get("test_urls") or [None])[0]
                    or pattern
                    or profile.get("start_url")
                )
                result = await validate_auth_profile(
                    auth_profile,
                    profile,
                    crawler_config,
                    allow_block,
                    test_url_override=candidate_url,
                )
                auth_test = result.to_dict()
                ui_status = "valid" if result.ok else "invalid"
        else:
            if auth_required_hint:
                ui_status = "needs_profile"

        rules_payload.append(
            {
                "rule_id": rule_id,
                "pattern": pattern,
                "auth_required_hint": auth_required_hint,
                "auth_profile": auth_profile,
                "auth_test": auth_test,
                "ui_status": ui_status,
            }
        )

    payload = {"rules": rules_payload, "playwright_available": playwright_ok}
    _ALLOWED_URL_STATUS_CACHE["timestamp"] = datetime.now(timezone.utc).timestamp()
    _ALLOWED_URL_STATUS_CACHE["payload"] = payload
    return payload


@router.get("/candidates/recommendations")
async def candidate_recommendations() -> Dict[str, List[Dict[str, Any]]]:
    if not CANDIDATES_PATH.exists():
        return {"items": []}
    counts: Dict[str, Dict[str, Any]] = {}
    for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = entry.get("url")
        if not url:
            continue
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            continue
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            suggested_url = f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}/"
        else:
            suggested_url = f"{parsed.scheme}://{parsed.netloc}/"
        seen_types = {
            "web": True,
            "pdf": False,
            "docx": False,
            "xlsx": False,
            "pptx": False,
        }
        lower_url = url.lower()
        if lower_url.endswith(".pdf"):
            seen_types = {**seen_types, "web": False, "pdf": True}
        elif lower_url.endswith(".docx"):
            seen_types = {**seen_types, "web": False, "docx": True}
        elif lower_url.endswith(".xlsx"):
            seen_types = {**seen_types, "web": False, "xlsx": True}
        elif lower_url.endswith(".pptx"):
            seen_types = {**seen_types, "web": False, "pptx": True}
        entry_key = suggested_url
        if entry_key not in counts:
            counts[entry_key] = {
                "suggested_url": suggested_url,
                "host": parsed.netloc,
                "count": 0,
                "seen_types": {
                    "web": False,
                    "pdf": False,
                    "docx": False,
                    "xlsx": False,
                    "pptx": False,
                },
            }
        counts[entry_key]["count"] += 1
        for key, value in seen_types.items():
            if value:
                counts[entry_key]["seen_types"][key] = True
    items = sorted(counts.values(), key=lambda item: item["count"], reverse=True)[:50]
    return {"items": items}


@router.post("/candidates/purge")
async def purge_candidates() -> Dict[str, str]:
    try:
        if CANDIDATES_PATH.exists():
            CANDIDATES_PATH.unlink()
        if PROCESSED_PATH.exists():
            PROCESSED_PATH.unlink()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/reset_crawl")
async def reset_crawl() -> Dict[str, Any]:
    """Reset crawl state by deleting artifacts, candidates, and job logs."""
    import shutil
    deleted_items = []

    # Delete artifacts
    artifacts_path = Path("/app/data/artifacts")
    if artifacts_path.exists():
        artifact_count = len(list(artifacts_path.glob("*/artifact.json")))
        shutil.rmtree(artifacts_path)
        artifacts_path.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{artifact_count} artifacts")

    # Delete candidates
    if CANDIDATES_PATH.exists():
        CANDIDATES_PATH.unlink()
        deleted_items.append("candidates.jsonl")

    if PROCESSED_PATH.exists():
        PROCESSED_PATH.unlink()
        deleted_items.append("processed.json")

    # Delete job logs
    job_logs_path = Path("/app/data/logs/jobs")
    if job_logs_path.exists():
        log_count = len(list(job_logs_path.glob("*.log")))
        shutil.rmtree(job_logs_path)
        job_logs_path.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{log_count} job logs")

    # Delete summaries
    summaries_path = Path("/app/data/logs/summaries")
    if summaries_path.exists():
        summary_count = len(list(summaries_path.glob("*.json")))
        shutil.rmtree(summaries_path)
        summaries_path.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{summary_count} summaries")

    return {"status": "ok", "deleted": deleted_items}


@router.get("/ingest-metadata/status")
async def get_ingest_metadata_status() -> Dict[str, Any]:
    """Get status of the ingest metadata database."""
    import sqlite3

    status = {
        "db_path": str(DB_PATH),
        "exists": DB_PATH.exists(),
        "size_bytes": 0,
        "tables_present": [],
        "doc_count": 0,
        "chunk_count": 0,
        "schema_version": 0,
        "initialized": False,
    }

    if not DB_PATH.exists():
        return status

    try:
        status["size_bytes"] = DB_PATH.stat().st_size

        # Ensure schema is initialized
        ensure_metadata_db_initialized()

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Check which tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [row[0] for row in cursor.fetchall()]
        status["tables_present"] = tables

        # Get counts if tables exist
        if "documents" in tables:
            cursor.execute("SELECT COUNT(*) FROM documents")
            status["doc_count"] = cursor.fetchone()[0]

        if "chunks" in tables:
            cursor.execute("SELECT COUNT(*) FROM chunks")
            status["chunk_count"] = cursor.fetchone()[0]

        # Get schema version
        cursor.execute("PRAGMA user_version")
        status["schema_version"] = cursor.fetchone()[0]

        # Mark as initialized if we have the expected tables
        status["initialized"] = "documents" in tables and "chunks" in tables

        conn.close()

    except Exception as e:
        status["error"] = str(e)

    return status


@router.post("/reset_ingest")
async def reset_ingest() -> Dict[str, Any]:
    """Reset ingest state by deleting metadata database."""
    import sqlite3
    deleted_items = []

    if DB_PATH.exists():
        # Count records before deleting (ensure schema exists first)
        try:
            ensure_metadata_db_initialized()
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            doc_count = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunk_count = cursor.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            conn.close()
            deleted_items.append(f"{doc_count} documents")
            deleted_items.append(f"{chunk_count} chunks")
        except Exception:
            deleted_items.append("metadata.db (corrupted or empty)")

        DB_PATH.unlink()

    return {"status": "ok", "deleted": deleted_items}


@router.post("/reset/artifacts")
async def reset_artifacts() -> Dict[str, Any]:
    """Delete crawl artifacts, candidates, logs, and summaries."""
    import shutil

    deleted_items = []
    artifacts_path = Path("/app/data/artifacts")
    if artifacts_path.exists():
        artifact_count = len(list(artifacts_path.glob("*/artifact.json")))
        shutil.rmtree(artifacts_path)
        artifacts_path.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{artifact_count} artifacts")

    if CANDIDATES_PATH.exists():
        CANDIDATES_PATH.unlink()
        deleted_items.append("candidates.jsonl")

    if PROCESSED_PATH.exists():
        PROCESSED_PATH.unlink()
        deleted_items.append("processed.json")

    job_logs_path = Path("/app/data/logs/jobs")
    if job_logs_path.exists():
        log_count = len(list(job_logs_path.glob("*.log")))
        shutil.rmtree(job_logs_path)
        job_logs_path.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{log_count} job logs")

    if SUMMARY_DIR.exists():
        summary_count = len(list(SUMMARY_DIR.glob("*.json")))
        shutil.rmtree(SUMMARY_DIR)
        SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{summary_count} summaries")

    if QUARANTINE_DIR.exists():
        quarantine_count = len(list(QUARANTINE_DIR.glob("*")))
        shutil.rmtree(QUARANTINE_DIR)
        QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{quarantine_count} quarantined artifacts")

    return {"status": "ok", "deleted": deleted_items}


@router.post("/reset/qdrant")
async def reset_qdrant() -> Dict[str, Any]:
    """Reset Qdrant collection and ingest metadata database."""
    system_config = yaml.safe_load((CONFIG_DIR / "system.yml").read_text(encoding="utf-8")) or {}
    qdrant_config = system_config.get("qdrant", {})
    ollama_config = system_config.get("ollama", {})
    collection = qdrant_config.get("collection")
    qdrant_host = qdrant_config.get("host")
    embedding_model = ollama_config.get("embedding_model")
    ollama_host = ollama_config.get("host")
    if not collection or not qdrant_host:
        raise HTTPException(status_code=400, detail="Missing qdrant configuration")
    client = QdrantClient(url=qdrant_host)
    deleted_items = []

    vector_size = None
    try:
        collections = client.get_collections().collections
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error connecting to Qdrant: {e}")

    if any(col.name == collection for col in collections):
        try:
            info = client.get_collection(collection)
            vector_size = info.config.params.vectors.size
            count_before = info.points_count
        except Exception:
            count_before = 0
        client.delete_collection(collection_name=collection)
        deleted_items.append(f"{count_before} vectors from collection '{collection}'")
    if vector_size is None:
        if not embedding_model or not ollama_host:
            raise HTTPException(status_code=400, detail="Missing embedding configuration")
        vector_size = len(embed_text(ollama_host, embedding_model, "dimension probe"))
    client.create_collection(
        collection_name=collection,
        vectors_config=rest.VectorParams(size=vector_size, distance=rest.Distance.COSINE),
    )
    client.create_payload_index(collection_name=collection, field_name="doc_id", field_schema="keyword")

    if DB_PATH.exists():
        DB_PATH.unlink()
        deleted_items.append("ingest metadata.db")

    return {"status": "ok", "deleted": deleted_items, "collection": collection}


@router.post("/reset/all")
async def reset_all() -> Dict[str, Any]:
    """Delete all crawl artifacts, logs, quarantine, and reset Qdrant + ingest metadata."""
    artifacts_result = await reset_artifacts()
    qdrant_result = await reset_qdrant()
    return {
        "status": "ok",
        "deleted": (artifacts_result.get("deleted", []) + qdrant_result.get("deleted", [])),
    }


@router.post("/validate/crawl")
async def validate_crawl() -> Dict[str, Any]:
    """Run crawl artifact validation and return summary."""
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    # Run the crawl validator script
    _run_validation(
        [
            "python",
            "/app/tools/validate_crawl.py",
            "--artifacts-dir",
            "/app/data/artifacts",
            "--quarantine-dir",
            "/app/data/quarantine",
            "--output-dir",
            "/app/data/logs/summaries",
            "--all",
        ]
    )

    # Read the latest summary
    summary_path = SUMMARY_DIR / "validate_crawl_latest.json"
    if not summary_path.is_file():
        latest = _latest_summary("validate_crawl_")
        if not latest or not latest.is_file():
            raise HTTPException(status_code=500, detail="Crawl validation summary not found")
        summary_path = latest

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return _format_crawl_summary(payload)


@router.get("/validate/crawl/summary")
async def get_crawl_summary() -> Dict[str, Any]:
    summary_path = SUMMARY_DIR / "validate_crawl_latest.json"

    if not summary_path.is_file():
        latest = _latest_summary("validate_crawl_")
        if not latest or not latest.is_file():
            # Return empty status instead of 404 for better UI handling
            return {
                "status": "empty",
                "summary": {
                    "total": 0,
                    "flagged": 0,
                    "quarantined": 0,
                },
                "validated": [],
                "raw": None,
            }
        summary_path = latest

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return _format_crawl_summary(payload)


@router.post("/validate/ingest")
async def validate_ingest() -> Dict[str, Any]:
    """Run ingest validation and return summary."""
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = SUMMARY_DIR / "validate_ingest_latest.json"
    redis_info = _parse_redis_host_port()
    _run_validation(
        [
            "python",
            "/app/tools/validate_ingest.py",
            "--data-integrity",
            "--redis-host",
            redis_info["host"],
            "--redis-port",
            str(redis_info["port"]),
            "--config",
            "/app/config/system.yml",
            "--db",
            "/app/data/ingest/metadata.db",
        ]
    )
    latest = _latest_summary("validate_ingest_")
    if not latest.exists():
        raise HTTPException(status_code=500, detail="Ingest validation summary not found")
    payload = json.loads(latest.read_text(encoding="utf-8"))
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _format_ingest_summary(payload)


@router.get("/validate/ingest/summary")
async def get_ingest_summary() -> Dict[str, Any]:
    summary_path = SUMMARY_DIR / "validate_ingest_latest.json"
    if not summary_path.exists():
        summary_path = _latest_summary("validate_ingest_")
    if not summary_path.exists():
        raise HTTPException(status_code=404, detail="No ingest validation summary available")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return _format_ingest_summary(payload)


@router.post("/quarantine")
async def quarantine_artifacts(payload: Dict[str, Any]) -> Dict[str, Any]:
    ids = payload.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="No artifact ids provided")
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    quarantined = []
    missing = []
    for artifact_id in ids:
        source_dir = Path("/app/data/artifacts") / artifact_id
        if not source_dir.exists():
            missing.append(artifact_id)
            continue
        destination = QUARANTINE_DIR / artifact_id
        try:
            source_dir.rename(destination)
            quarantined.append(artifact_id)
            with QUARANTINE_AUDIT_LOG.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{_utcnow()} quarantine id={artifact_id} src={source_dir} dst={destination}\n"
                )
        except Exception as exc:
            missing.append(f"{artifact_id}: {exc}")
    return {"status": "ok", "quarantined": quarantined, "missing": missing}


@router.get("/crawl/auth_hints")
async def get_auth_hints() -> Dict[str, Any]:
    if not AUTH_HINTS_PATH.exists():
        return {"by_domain": {}, "recent": []}
    try:
        return json.loads(AUTH_HINTS_PATH.read_text(encoding="utf-8")) or {"by_domain": {}, "recent": []}
    except json.JSONDecodeError:
        return {"by_domain": {}, "recent": []}


@router.post("/crawl")
async def trigger_crawl() -> Dict[str, str]:
    from app.workers.crawl_worker import run_crawl_job

    job = start_job("crawl", run_crawl_job)
    return {"job_id": job.job_id}


@router.post("/ingest")
async def trigger_ingest() -> Dict[str, str]:
    job = start_job("ingest", run_ingest_job)
    return {"job_id": job.job_id}


@router.get("/jobs")
async def get_jobs() -> List[Dict[str, Any]]:
    return [job.__dict__ for job in list_jobs().values()]


@router.get("/jobs/{job_id}")
async def get_job_detail(job_id: str) -> Dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.__dict__


async def _tail_log(job_id: str) -> AsyncGenerator[str, None]:
    log_path = Path("/app/data/logs/jobs") / f"{job_id}.log"
    if not log_path.exists():
        yield f"data: {job_id} not found\n\n"
        return
    with log_path.open("r", encoding="utf-8") as handle:
        while True:
            line = handle.readline()
            if line:
                yield f"data: {line.strip()}\n\n"
            else:
                await asyncio.sleep(1.0)


@router.get("/jobs/{job_id}/log")
async def stream_log(job_id: str) -> StreamingResponse:
    return StreamingResponse(_tail_log(job_id), media_type="text/event-stream")


@router.get("/jobs/{job_id}/log/export")
async def export_log(job_id: str) -> FileResponse:
    log_path = Path("/app/data/logs/jobs") / f"{job_id}.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    return FileResponse(log_path, filename=f"{job_id}.log", media_type="text/plain")


@router.get("/jobs/{job_id}/summary")
async def get_job_summary(job_id: str) -> Dict[str, Any]:
    summary_path = Path("/app/data/logs/summaries") / f"{job_id}.json"
    if not summary_path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")
    return json.loads(summary_path.read_text(encoding="utf-8"))


@router.delete("/jobs/{job_id}")
async def remove_job(job_id: str) -> Dict[str, str]:
    delete_job(job_id)
    return {"status": "ok"}


@router.get("/data/health")
async def get_data_health() -> Dict[str, Any]:
    """Get comprehensive health status for all data pipeline components."""
    import glob
    import sqlite3
    from app.utils.config import load_config

    health = {}

    # Artifacts status
    artifacts_path = Path("/app/data/artifacts")
    quarantine_path = Path("/app/data/quarantine")
    artifacts_count = 0
    quarantined_count = 0
    last_captured_at = None

    if artifacts_path.exists():
        artifact_dirs = list(artifacts_path.glob("*/artifact.json"))
        artifacts_count = len(artifact_dirs)

        # Find most recent artifact
        if artifact_dirs:
            latest_artifact = max(artifact_dirs, key=lambda p: p.stat().st_mtime)
            last_captured_at = datetime.fromtimestamp(
                latest_artifact.stat().st_mtime, tz=timezone.utc
            ).isoformat()

    if quarantine_path.exists():
        quarantined_count = len(list(quarantine_path.glob("*")))

    health["artifacts"] = {
        "count": artifacts_count,
        "quarantined": quarantined_count,
        "last_captured_at": last_captured_at,
    }

    # Crawl job status
    jobs = list_jobs()
    crawl_jobs = [j for j in jobs.values() if j.job_type == "crawl"]
    last_crawl_job = None

    if crawl_jobs:
        latest_crawl = max(crawl_jobs, key=lambda j: j.started_at or "")
        last_crawl_job = {
            "id": latest_crawl.job_id,
            "status": latest_crawl.status,
            "finished_at": latest_crawl.finished_at,
            "started_at": latest_crawl.started_at,
        }

    health["crawl"] = {"last_job": last_crawl_job}

    # Ingest worker and job status
    try:
        from app.routes.ingest_jobs import get_worker_status
        worker_status = await get_worker_status()
        health["ingest"] = {
            "worker": worker_status,
            "last_job": None,
        }
    except Exception:
        health["ingest"] = {
            "worker": {"status": "unknown", "details": {}},
            "last_job": None,
        }

    ingest_jobs = [j for j in jobs.values() if j.job_type == "ingest"]
    if ingest_jobs:
        latest_ingest = max(ingest_jobs, key=lambda j: j.started_at or "")
        health["ingest"]["last_job"] = {
            "id": latest_ingest.job_id,
            "status": latest_ingest.status,
            "finished_at": latest_ingest.finished_at,
            "started_at": latest_ingest.started_at,
        }

    # Qdrant status
    try:
        system_config = load_config("system")
        qdrant_config = system_config.get("qdrant", {})
        qdrant_host = qdrant_config.get("host")
        collection_name = qdrant_config.get("collection", "ragai_chunks")

        client = QdrantClient(url=qdrant_host)
        collections_info = []

        try:
            collection_info = client.get_collection(collection_name)
            collections_info.append({
                "name": collection_name,
                "points": collection_info.points_count or 0,
            })
        except Exception:
            collections_info.append({
                "name": collection_name,
                "points": 0,
            })

        health["qdrant"] = {"collections": collections_info}
    except Exception as e:
        health["qdrant"] = {"collections": [], "error": str(e)}

    # System health
    try:
        from app.routes.health import check_health
        api_health = await check_health()
        health["system"] = {"api_health": "ok" if api_health.get("status") == "ok" else "degraded"}
    except Exception:
        health["system"] = {"api_health": "unknown"}

    return health


@router.post("/data/check_url")
async def check_url(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Check a specific URL across artifacts, validation, ingest, and Qdrant."""
    import sqlite3
    from app.utils.config import load_config

    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url' field")

    result = {
        "url": url,
        "artifact": None,
        "validation": None,
        "ingest": None,
        "qdrant": None,
    }
    artifact_doc_id = None

    # Check artifacts
    artifacts_path = Path("/app/data/artifacts")
    if artifacts_path.exists():
        found_artifacts = []

        # Scan all artifact.json files
        for artifact_file in artifacts_path.glob("*/artifact.json"):
            try:
                artifact_data = json.loads(artifact_file.read_text(encoding="utf-8"))
                artifact_url = artifact_data.get("url", "")

                if artifact_url == url or artifact_data.get("final_url") == url:
                    artifact_dir = artifact_file.parent
                    captured_at = (
                        artifact_data.get("fetched_at")
                        or artifact_data.get("captured_at")
                        or artifact_data.get("timestamp")
                    )

                    # Read content snippet
                    content_file = artifact_dir / "content.html"
                    snippet = ""
                    if content_file.exists():
                        content_text = content_file.read_text(encoding="utf-8")
                        snippet = content_text[:500] + ("..." if len(content_text) > 500 else "")

                    found_artifacts.append({
                        "artifact_id": artifact_dir.name,
                        "doc_id": artifact_data.get("doc_id"),
                        "url": artifact_data.get("url"),
                        "final_url": artifact_data.get("final_url"),
                        "http_status": artifact_data.get("http_status") or artifact_data.get("status_code"),
                        "auth_profile": artifact_data.get("auth_profile"),
                        "title": artifact_data.get("title"),
                        "captured_at": captured_at,
                        "content_hash": artifact_data.get("content_hash"),
                        "snippet": snippet,
                    })
            except Exception:
                continue

        if found_artifacts:
            # Sort by captured_at, most recent first
            found_artifacts.sort(key=lambda a: a.get("captured_at", ""), reverse=True)
            artifact_doc_id = found_artifacts[0].get("doc_id")
            result["artifact"] = {
                "found": True,
                "count": len(found_artifacts),
                "most_recent": found_artifacts[0] if found_artifacts else None,
                "all": found_artifacts,
            }
        else:
            result["artifact"] = {"found": False}

    # Check validation findings
    latest_validation = _latest_summary("validate_crawl_")
    if latest_validation and latest_validation.exists():
        try:
            validation_data = json.loads(latest_validation.read_text(encoding="utf-8"))
            findings = validation_data.get("findings", [])
            url_findings = [f for f in findings if f.get("url") == url]

            if url_findings:
                result["validation"] = {
                    "found": True,
                    "findings": url_findings,
                }
            else:
                result["validation"] = {"found": False}
        except Exception:
            result["validation"] = {"found": False, "error": "Could not read validation data"}
    else:
        result["validation"] = {"found": False, "error": "No validation summary available"}

    # Check ingest status
    try:
        # Ensure schema is initialized before querying
        ensure_metadata_db_initialized()

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Find document by URL
        cursor.execute(
            "SELECT doc_id, url, ingested_at, chunk_count, content_hash FROM documents WHERE url = ?",
            (url,),
        )
        doc_row = cursor.fetchone()

        if doc_row:
            doc_id, doc_url, ingested_at, chunk_count, content_hash = doc_row
            artifact_doc_id = artifact_doc_id or doc_id

            # Count chunks for this document
            cursor.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,))
            chunk_count_db = cursor.fetchone()[0]

            result["ingest"] = {
                "found": True,
                "doc_id": doc_id,
                "url": doc_url,
                "chunk_count": chunk_count_db,
                "chunk_count_recorded": chunk_count,
                "ingested_at": ingested_at,
                "content_hash": content_hash,
            }
        else:
            result["ingest"] = {"found": False}

        conn.close()
    except Exception as e:
        result["ingest"] = {"found": False, "error": str(e)}

    # Check Qdrant
    try:
        system_config = load_config("system")
        qdrant_config = system_config.get("qdrant", {})
        qdrant_host = qdrant_config.get("host")
        collection_name = qdrant_config.get("collection", "ragai_chunks")

        client = QdrantClient(url=qdrant_host)

        filters = []
        if artifact_doc_id:
            filters.append(
                rest.FieldCondition(
                    key="doc_id",
                    match=rest.MatchValue(value=artifact_doc_id),
                )
            )
        filters.append(
            rest.FieldCondition(
                key="url",
                match=rest.MatchValue(value=url),
            )
        )
        scroll_filter = rest.Filter(should=filters)
        points_count = 0
        try:
            count_result = client.count(
                collection_name=collection_name,
                count_filter=scroll_filter,
                exact=True,
            )
            points_count = count_result.count or 0
        except Exception:
            search_result = client.scroll(
                collection_name=collection_name,
                scroll_filter=scroll_filter,
                limit=10,
            )
            points = search_result[0] if search_result else []
            points_count = len(points)
        search_result = client.scroll(
            collection_name=collection_name,
            scroll_filter=scroll_filter,
            limit=10,
        )

        points = search_result[0] if search_result else []

        if points:
            # Extract chunk snippets
            chunks = []
            for point in points[:3]:  # Limit to 3 examples
                payload_data = point.payload or {}
                chunks.append({
                    "chunk_id": point.id,
                    "text": payload_data.get("text", "")[:200],
                    "doc_id": payload_data.get("doc_id"),
                })

            result["qdrant"] = {
                "found": True,
                "points_count": points_count,
                "example_chunks": chunks,
            }
        else:
            result["qdrant"] = {"found": False, "points_count": points_count}
    except Exception as e:
        result["qdrant"] = {"found": False, "error": str(e)}

    return result


@router.post("/data/repair_url")
async def repair_url(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Repair ingest state for a single URL by clearing metadata/vectors and re-queueing ingest."""
    import sqlite3
    from app.utils.config import load_config
    from app.utils.redis_queue import push_job

    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url' field")

    artifacts_path = Path("/app/data/artifacts")
    artifact_match = None
    artifact_candidates = []
    if artifacts_path.exists():
        for artifact_file in artifacts_path.glob("*/artifact.json"):
            try:
                artifact_data = json.loads(artifact_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            artifact_url = artifact_data.get("url")
            if artifact_url == url or artifact_data.get("final_url") == url:
                captured_at = (
                    artifact_data.get("fetched_at")
                    or artifact_data.get("captured_at")
                    or artifact_data.get("timestamp")
                )
                captured_ts = None
                if isinstance(captured_at, (int, float)):
                    captured_ts = float(captured_at)
                elif isinstance(captured_at, str):
                    try:
                        captured_ts = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        captured_ts = None
                if captured_ts is None:
                    captured_ts = artifact_file.stat().st_mtime
                artifact_candidates.append({
                    "artifact_file": artifact_file,
                    "artifact_dir": artifact_file.parent,
                    "doc_id": artifact_data.get("doc_id"),
                    "captured_at": captured_at,
                    "captured_ts": captured_ts,
                })

    if artifact_candidates:
        artifact_match = max(artifact_candidates, key=lambda a: a["captured_ts"])

    if not artifact_match:
        raise HTTPException(status_code=404, detail="No artifact found for URL")

    doc_id = artifact_match["doc_id"]
    cleared = {"documents": 0, "chunks": 0, "qdrant": False}
    ensure_metadata_db_initialized()

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if not doc_id:
            cursor.execute("SELECT doc_id FROM documents WHERE url = ?", (url,))
            row = cursor.fetchone()
            if row:
                doc_id = row[0]
        if doc_id:
            cursor.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,))
            cleared["chunks"] = cursor.fetchone()[0]
            cursor.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            cursor.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            cleared["documents"] = cursor.rowcount
            conn.commit()
        conn.close()
    except Exception:
        pass

    try:
        system_config = load_config("system")
        qdrant_config = system_config.get("qdrant", {})
        qdrant_host = qdrant_config.get("host")
        collection_name = qdrant_config.get("collection", "ragai_chunks")
        client = QdrantClient(url=qdrant_host)
        client.delete(
            collection_name=collection_name,
            points_selector=rest.Filter(
                must=[rest.FieldCondition(key="doc_id", match=rest.MatchValue(value=doc_id))]
            ),
        )
        cleared["qdrant"] = True
    except Exception:
        cleared["qdrant"] = False

    if not doc_id:
        raise HTTPException(status_code=404, detail="No doc_id found for URL")

    job_id = f"job_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:6]}"
    job = {
        "job_id": job_id,
        "type": "ingest",
        "artifact_paths": [str(artifact_match["artifact_file"])],
        "chunks_estimate": 0,
        "meta": {"repair_url": url, "doc_id": doc_id},
    }
    await push_job(job)

    return {
        "status": "queued",
        "url": url,
        "doc_id": doc_id,
        "artifact_id": artifact_match["artifact_dir"].name,
        "cleared": cleared,
        "job_id": job_id,
    }


@router.post("/data/search")
async def search_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Search for keywords across artifacts and Qdrant."""
    import sqlite3
    from app.utils.config import load_config

    query = payload.get("query")
    limit = payload.get("limit", 10)
    scope = payload.get("scope", "all")

    if not query:
        raise HTTPException(status_code=400, detail="Missing 'query' field")

    result = {
        "query": query,
        "artifacts": [],
        "qdrant": [],
    }

    # Search artifacts (keyword search)
    if scope in ("all", "artifacts"):
        artifacts_path = Path("/app/data/artifacts")
        if artifacts_path.exists():
            matches = []
            query_lower = query.lower()

            for artifact_file in artifacts_path.glob("*/artifact.json"):
                try:
                    artifact_dir = artifact_file.parent
                    artifact_data = json.loads(artifact_file.read_text(encoding="utf-8"))

                    # Check content
                    content_file = artifact_dir / "content.html"
                    if content_file.exists():
                        content_text = content_file.read_text(encoding="utf-8")

                        if query_lower in content_text.lower():
                            # Extract snippet around first match
                            match_index = content_text.lower().find(query_lower)
                            start = max(0, match_index - 100)
                            end = min(len(content_text), match_index + 100)
                            snippet = content_text[start:end]

                            matches.append({
                                "artifact_id": artifact_dir.name,
                                "url": artifact_data.get("url"),
                                "title": artifact_data.get("title"),
                                "snippet": snippet,
                                "match_index": match_index,
                            })

                            if len(matches) >= limit:
                                break
                except Exception:
                    continue

            result["artifacts"] = matches

    # Search Qdrant (semantic search)
    if scope in ("all", "qdrant"):
        try:
            system_config = load_config("system")
            qdrant_config = system_config.get("qdrant", {})
            ollama_config = system_config.get("ollama", {})
            qdrant_host = qdrant_config.get("host")
            collection_name = qdrant_config.get("collection", "ragai_chunks")
            ollama_host = ollama_config.get("host")
            embedding_model = ollama_config.get("embedding_model")

            if ollama_host and embedding_model:
                # Generate embedding for query
                query_vector = embed_text(ollama_host, embedding_model, query)

                # Search Qdrant
                client = QdrantClient(url=qdrant_host)
                search_results = client.search(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    limit=limit,
                )

                qdrant_matches = []
                for hit in search_results:
                    payload_data = hit.payload or {}
                    qdrant_matches.append({
                        "chunk_id": hit.id,
                        "score": hit.score,
                        "text": payload_data.get("text", "")[:200],
                        "url": payload_data.get("url"),
                        "doc_id": payload_data.get("doc_id"),
                    })

                result["qdrant"] = qdrant_matches
            else:
                result["qdrant"] = {"error": "Embedding configuration not available"}
        except Exception as e:
            result["qdrant"] = {"error": str(e)}

    return result


@router.post("/clear_vectors")
async def clear_vectors() -> Dict[str, Any]:
    system_config = yaml.safe_load((CONFIG_DIR / "system.yml").read_text(encoding="utf-8")) or {}
    qdrant_config = system_config.get("qdrant", {})
    ollama_config = system_config.get("ollama", {})
    collection = qdrant_config.get("collection")
    qdrant_host = qdrant_config.get("host")
    embedding_model = ollama_config.get("embedding_model")
    ollama_host = ollama_config.get("host")
    if not collection or not qdrant_host:
        raise HTTPException(status_code=400, detail="Missing qdrant configuration")
    client = QdrantClient(url=qdrant_host)
    try:
        collections = client.get_collections().collections
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error connecting to Qdrant: {e}")

    deleted_items = []
    count_before = 0

    vector_size = None
    if any(col.name == collection for col in collections):
        try:
            info = client.get_collection(collection)
            vector_size = info.config.params.vectors.size
            count_before = info.points_count
        except AttributeError:
            # Handle case where config structure doesn't match expected schema
            print(f"Warning: Could not get vector size for collection '{collection}' due to schema mismatch")
        except Exception as e:
            # Handle pydantic validation errors
            if "validation" in str(e).lower() or "extra" in str(e).lower():
                print(f"Warning: Qdrant config validation error (server schema mismatch): {e}")
            else:
                raise HTTPException(status_code=500, detail=f"Error getting collection info: {e}")
        client.delete_collection(collection_name=collection)
        deleted_items.append(f"{count_before} vectors from collection '{collection}'")
    if vector_size is None:
        if not embedding_model or not ollama_host:
            raise HTTPException(status_code=400, detail="Missing embedding configuration")
        vector_size = len(embed_text(ollama_host, embedding_model, "dimension probe"))
    client.create_collection(
        collection_name=collection,
        vectors_config=rest.VectorParams(size=vector_size, distance=rest.Distance.COSINE),
    )
    client.create_payload_index(collection_name=collection, field_name="doc_id", field_schema="keyword")

    # Get count after recreation (should be 0)
    count_after = 0
    try:
        info = client.get_collection(collection)
        count_after = info.points_count
    except Exception:
        pass  # If we can't get the count, assume 0

    # Also delete ingest metadata
    if DB_PATH.exists():
        DB_PATH.unlink()
        deleted_items.append("ingest metadata.db")

    return {
        "status": "ok",
        "deleted": deleted_items,
        "collection": collection,
        "count_before": count_before,
        "count_after": count_after,
        "removed": count_before - count_after,
    }
