from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from app.utils.auth_validation import (
    collect_required_profiles,
    get_cached_auth_status,
    load_allow_block_config,
    load_crawler_config,
    run_auth_checks,
)

router = APIRouter(prefix="/api/crawl", tags=["crawl"])


@router.get("/auth-status")
async def get_auth_status() -> Dict[str, Any]:
    return {"results": get_cached_auth_status()}


@router.post("/test-auth")
async def test_auth(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    profile_name = payload.get("profile_name")
    profile_names: List[str] = payload.get("profile_names") or payload.get("profiles") or []

    crawler_config = load_crawler_config()
    allow_block = load_allow_block_config()
    profiles = crawler_config.get("playwright", {}).get("auth_profiles", {})

    if profile_name:
        selected = [profile_name]
    elif profile_names:
        selected = profile_names
    else:
        selected = list(collect_required_profiles(crawler_config, allow_block).keys())

    selected = [name for name in selected if name in profiles]
    results = await run_auth_checks(selected, force=True)
    return {"results": results}
