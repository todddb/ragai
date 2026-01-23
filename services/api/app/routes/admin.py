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
from app.workers.ingest_worker import DB_PATH, run_ingest_job

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


@router.post("/reset_ingest")
async def reset_ingest() -> Dict[str, Any]:
    """Reset ingest state by deleting metadata database."""
    import sqlite3
    deleted_items = []

    if DB_PATH.exists():
        # Count records before deleting
        try:
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
    summary_path = SUMMARY_DIR / "validate_ingest_latest.json"
    if not summary_path.is_file():
        latest = _latest_summary("validate_ingest_")
        if not latest or not latest.is_file():
            raise HTTPException(status_code=404, detail="No ingest validation summary available")
        summary_path = latest

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return _format_ingest_summary(payload)


@router.get("/validate/crawl/summary")
async def get_crawl_summary() -> Dict[str, Any]:
    summary_path = SUMMARY_DIR / "validate_crawl_latest.json"

    if not summary_path.is_file():
        latest = _latest_summary("validate_crawl_")
        if not latest or not latest.is_file():
            raise HTTPException(
                status_code=404,
                detail="No crawl validation summary available"
            )
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
