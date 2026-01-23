import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib.parse import urlparse

import yaml

CRAWLER_CONFIG_PATH = Path("/app/config/crawler.yml")
ALLOW_BLOCK_PATH = Path("/app/config/allow_block.yml")
AUTH_CACHE_TTL_SECONDS = 300

AUTH_IDP_DOMAINS = {
    "cas.byu.edu",
    "login.byu.edu",
    "auth.brightspot.byu.edu",
}
AUTH_TITLE_MARKERS = ("central authentication service", "cas")
# CAS-specific markers that indicate a login page (more specific than generic "login" text)
CAS_LOGIN_MARKERS = (
    "central authentication service",
    "/cas/login",
    'action="/cas/login"',
    'name="username"',
    'name="password"',
    'id="username"',
    'id="password"',
    "duo-frame",
    "sso-login",
)


@dataclass
class AuthCheckResult:
    profile_name: str
    ok: bool
    final_url: str
    title: str
    status: Optional[int]
    error_reason: str
    checked_at: Optional[str] = None  # ISO 8601 timestamp

    def to_dict(self) -> Dict[str, object]:
        return {
            "profile_name": self.profile_name,
            "ok": self.ok,
            "final_url": self.final_url,
            "title": self.title,
            "status": self.status,
            "error_reason": self.error_reason,
            "checked_at": self.checked_at,
        }


_AUTH_STATUS_CACHE: Dict[str, Dict[str, object]] = {
    "timestamp": 0.0,
    "results": {},
}


def _load_config(path: Path) -> Dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_crawler_config() -> Dict:
    return _load_config(CRAWLER_CONFIG_PATH)


def load_allow_block_config() -> Dict:
    return _load_config(ALLOW_BLOCK_PATH)


def resolve_test_url(profile: Dict, allow_block: Dict, profile_name: str) -> Optional[str]:
    test_url = (profile or {}).get("test_url")
    if test_url:
        return test_url
    test_urls = (profile or {}).get("test_urls") or []
    if test_urls:
        return test_urls[0]
    for rule in allow_block.get("allow_rules", []):
        if isinstance(rule, dict):
            if (rule.get("auth_profile") or rule.get("authProfile")) == profile_name:
                pattern = rule.get("pattern", "")
                if pattern:
                    return pattern
    start_url = (profile or {}).get("start_url")
    if start_url:
        return start_url
    return None


def _find_seed_for_domain(allow_block: Dict, domain: str) -> Optional[str]:
    for seed in allow_block.get("seed_urls", []):
        seed_url = seed.get("url") if isinstance(seed, dict) else seed
        if not seed_url:
            continue
        host = urlparse(seed_url).hostname or ""
        if host.lower() == domain.lower():
            return seed_url
    return None


def detect_auth_failure(final_url: str, title: str, content: str) -> Optional[str]:
    """
    Detect if the page indicates an authentication failure.
    Uses high-confidence signals to avoid false positives.
    """
    parsed = urlparse(final_url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()

    # High confidence: Redirected to known IdP domain
    if any(idp_domain in host for idp_domain in AUTH_IDP_DOMAINS):
        return f"redirected_to_idp:{host}"

    # High confidence: Title indicates CAS login
    normalized_title = (title or "").lower()
    if any(marker in normalized_title for marker in AUTH_TITLE_MARKERS):
        return "title_indicates_login"

    # High confidence: CAS login path
    if "/cas/login" in path:
        return "cas_login_path_detected"

    # Medium-high confidence: CAS-specific markers in content
    normalized_content = (content or "").lower()
    cas_marker_count = sum(1 for marker in CAS_LOGIN_MARKERS if marker.lower() in normalized_content)

    # Require multiple CAS markers to avoid false positives from footer text
    if cas_marker_count >= 2:
        return "cas_login_form_detected"

    # No auth failure detected
    return None


def _cache_is_fresh() -> bool:
    return time.time() - float(_AUTH_STATUS_CACHE.get("timestamp", 0.0)) < AUTH_CACHE_TTL_SECONDS


def get_cached_auth_status() -> Dict[str, Dict[str, object]]:
    if not _cache_is_fresh():
        return {}
    return dict(_AUTH_STATUS_CACHE.get("results", {}))


async def run_auth_checks(profile_names: Iterable[str], force: bool = False) -> Dict[str, Dict[str, object]]:
    """
    Run auth validation checks for specified profiles.
    Uses async Playwright API to avoid blocking the event loop.
    """
    crawler_config = load_crawler_config()
    allow_block = load_allow_block_config()
    playwright_config = crawler_config.get("playwright", {})
    profiles = playwright_config.get("auth_profiles", {})

    results: Dict[str, Dict[str, object]] = {}
    for name in profile_names:
        if not force and _cache_is_fresh():
            cached = _AUTH_STATUS_CACHE.get("results", {}).get(name)
            if cached:
                results[name] = cached
                continue
        profile = profiles.get(name) or {}
        result = await validate_auth_profile(name, profile, crawler_config, allow_block)
        results[name] = result.to_dict()

    _AUTH_STATUS_CACHE["timestamp"] = time.time()
    _AUTH_STATUS_CACHE["results"] = {**_AUTH_STATUS_CACHE.get("results", {}), **results}
    return results


async def validate_auth_profile(
    profile_name: str,
    profile: Dict,
    crawler_config: Dict,
    allow_block: Dict,
    test_url_override: Optional[str] = None,
) -> AuthCheckResult:
    """
    Validate an auth profile using async Playwright API.
    Tests if the stored auth state is still valid.
    """
    playwright_config = crawler_config.get("playwright", {})
    headless = playwright_config.get("headless", True)
    timeout_ms = playwright_config.get("navigation_timeout_ms", 60000)
    logger = logging.getLogger(__name__)

    checked_at = datetime.utcnow().isoformat() + "Z"

    storage_state_path = profile.get("storage_state_path")
    test_url = test_url_override or resolve_test_url(profile, allow_block, profile_name)
    if not storage_state_path:
        return AuthCheckResult(
            profile_name=profile_name,
            ok=False,
            final_url="",
            title="",
            status=None,
            error_reason="storage_state_path is not configured",
            checked_at=checked_at,
        )
    if not test_url:
        return AuthCheckResult(
            profile_name=profile_name,
            ok=False,
            final_url="",
            title="",
            status=None,
            error_reason="test_url is not configured",
            checked_at=checked_at,
        )

    storage_state = Path(storage_state_path)
    if not storage_state.exists():
        return AuthCheckResult(
            profile_name=profile_name,
            ok=False,
            final_url=test_url,
            title="",
            status=None,
            error_reason=f"storage state not found: {storage_state_path}",
            checked_at=checked_at,
        )

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless)
            context = await browser.new_context(storage_state=str(storage_state))
            try:
                page = await context.new_page()
                logger.info("AUTH_CHECK profile=%s url=%s", profile_name, test_url)
                response = await page.goto(test_url, wait_until="domcontentloaded", timeout=timeout_ms)
                final_url = page.url
                title = await page.title()
                content = await page.content()
                status = response.status if response else None
            finally:
                await context.close()
                await browser.close()
    except Exception as exc:
        return AuthCheckResult(
            profile_name=profile_name,
            ok=False,
            final_url=test_url,
            title="",
            status=None,
            error_reason=str(exc),
            checked_at=checked_at,
        )

    failure_reason = detect_auth_failure(final_url, title, content)
    if failure_reason:
        return AuthCheckResult(
            profile_name=profile_name,
            ok=False,
            final_url=final_url,
            title=title,
            status=status,
            error_reason=failure_reason,
            checked_at=checked_at,
        )

    return AuthCheckResult(
        profile_name=profile_name,
        ok=True,
        final_url=final_url,
        title=title,
        status=status,
        error_reason="",
        checked_at=checked_at,
    )


def collect_required_profiles(crawler_config: Dict, allow_block: Dict) -> Dict[str, Dict]:
    playwright_config = crawler_config.get("playwright", {})
    profiles = playwright_config.get("auth_profiles", {})
    required: Dict[str, Dict] = {}
    for rule in allow_block.get("allow_rules", []):
        if isinstance(rule, dict):
            profile_name = rule.get("auth_profile") or rule.get("authProfile")
            if profile_name and profile_name in profiles:
                required[profile_name] = profiles[profile_name]
    return required


def playwright_available() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401

        return True
    except Exception:
        return False
