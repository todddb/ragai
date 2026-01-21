import logging
import time
from dataclasses import dataclass
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
AUTH_CONTENT_MARKERS = ("sign in", "log in", "login")


@dataclass
class AuthCheckResult:
    profile_name: str
    ok: bool
    final_url: str
    title: str
    status: Optional[int]
    error_reason: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "profile_name": self.profile_name,
            "ok": self.ok,
            "final_url": self.final_url,
            "title": self.title,
            "status": self.status,
            "error_reason": self.error_reason,
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
    use_for_domains = (profile or {}).get("use_for_domains") or []
    if use_for_domains:
        target_domain = use_for_domains[0]
        seed_url = _find_seed_for_domain(allow_block, target_domain)
        return seed_url or f"https://{target_domain}/"
    for rule in allow_block.get("allow_rules", []):
        if isinstance(rule, dict):
            if (rule.get("auth_profile") or rule.get("authProfile")) == profile_name:
                pattern = rule.get("pattern", "")
                if pattern:
                    return pattern
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
    host = (urlparse(final_url).hostname or "").lower()
    if any(idp_domain in host for idp_domain in AUTH_IDP_DOMAINS):
        return f"redirected to {host}"
    normalized_title = (title or "").lower()
    if any(marker in normalized_title for marker in AUTH_TITLE_MARKERS):
        return "page title indicates CAS"
    normalized_content = (content or "").lower()
    if any(marker in normalized_content for marker in AUTH_CONTENT_MARKERS):
        return "login marker detected"
    return None


def _cache_is_fresh() -> bool:
    return time.time() - float(_AUTH_STATUS_CACHE.get("timestamp", 0.0)) < AUTH_CACHE_TTL_SECONDS


def get_cached_auth_status() -> Dict[str, Dict[str, object]]:
    if not _cache_is_fresh():
        return {}
    return dict(_AUTH_STATUS_CACHE.get("results", {}))


def run_auth_checks(profile_names: Iterable[str], force: bool = False) -> Dict[str, Dict[str, object]]:
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
        result = validate_auth_profile(name, profile, crawler_config, allow_block)
        results[name] = result.to_dict()

    _AUTH_STATUS_CACHE["timestamp"] = time.time()
    _AUTH_STATUS_CACHE["results"] = {**_AUTH_STATUS_CACHE.get("results", {}), **results}
    return results


def validate_auth_profile(
    profile_name: str,
    profile: Dict,
    crawler_config: Dict,
    allow_block: Dict,
) -> AuthCheckResult:
    playwright_config = crawler_config.get("playwright", {})
    headless = playwright_config.get("headless", True)
    timeout_ms = playwright_config.get("navigation_timeout_ms", 60000)
    logger = logging.getLogger(__name__)

    storage_state_path = profile.get("storage_state_path")
    test_url = resolve_test_url(profile, allow_block, profile_name)
    if not storage_state_path:
        return AuthCheckResult(
            profile_name=profile_name,
            ok=False,
            final_url="",
            title="",
            status=None,
            error_reason="storage_state_path is not configured",
        )
    if not test_url:
        return AuthCheckResult(
            profile_name=profile_name,
            ok=False,
            final_url="",
            title="",
            status=None,
            error_reason="test_url is not configured",
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
        )

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=str(storage_state))
            try:
                page = context.new_page()
                logger.info("AUTH_CHECK profile=%s url=%s", profile_name, test_url)
                response = page.goto(test_url, wait_until="domcontentloaded", timeout=timeout_ms)
                final_url = page.url
                title = page.title()
                content = page.content()
                status = response.status if response else None
            finally:
                context.close()
                browser.close()
    except Exception as exc:
        return AuthCheckResult(
            profile_name=profile_name,
            ok=False,
            final_url=test_url,
            title="",
            status=None,
            error_reason=str(exc),
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
        )

    return AuthCheckResult(
        profile_name=profile_name,
        ok=True,
        final_url=final_url,
        title=title,
        status=status,
        error_reason="",
    )


def collect_required_profiles(crawler_config: Dict, allow_block: Dict) -> Dict[str, Dict]:
    playwright_config = crawler_config.get("playwright", {})
    profiles = playwright_config.get("auth_profiles", {})
    required: Dict[str, Dict] = {}
    allowed_domains = set()
    for seed in allow_block.get("seed_urls", []):
        seed_url = seed.get("url") if isinstance(seed, dict) else seed
        if seed_url:
            host = urlparse(seed_url).hostname
            if host:
                allowed_domains.add(host.lower())
    for rule in allow_block.get("allow_rules", []):
        if isinstance(rule, dict):
            pattern = rule.get("pattern", "")
            if pattern:
                host = urlparse(pattern).hostname
                if host:
                    allowed_domains.add(host.lower())
            profile_name = rule.get("auth_profile") or rule.get("authProfile")
            if profile_name and profile_name in profiles:
                required[profile_name] = profiles[profile_name]
    for name, profile in profiles.items():
        use_for_domains = [domain.lower() for domain in profile.get("use_for_domains", [])]
        if use_for_domains and allowed_domains.intersection(use_for_domains):
            required[name] = profile
    return required
