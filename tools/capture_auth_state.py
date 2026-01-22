#!/usr/bin/env python3
"""
tools/capture_auth_state.py

Single consolidated Playwright auth-state capture tool:
- Supports multiple named auth profiles stored in config/crawler.yml (playwright.auth_profiles)
- Interactive login (headed Chromium)
- ENTER-to-validate-and-save (deterministic)
- Validation against one or more "protected" test URLs (detects CAS redirect)
- Optional retry loop without closing the browser
- Writes storageState JSON to secrets/playwright/*.json (or any path you set)
- Prints cookie + origin summary and basic sanity flags

Typical usage:
  python tools/capture_auth_state.py
  python tools/capture_auth_state.py --profile policy_cas
  python tools/capture_auth_state.py --config config/crawler.yml

Profile schema in config/crawler.yml:
playwright:
  auth_profiles:
    policy_cas:
      storage_state_path: secrets/playwright/policy-byu-storageState.json
      use_for_domains: ["policy.byu.edu"]
      start_url: "https://policy.byu.edu/"
      test_urls:
        - "https://policy.byu.edu/view/business-gifts-and-entertainment-policy"
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from playwright.sync_api import sync_playwright


# ---------- Heuristics ----------

DEFAULT_TEST_URL = "https://policy.byu.edu/view/business-gifts-and-entertainment-policy"


def looks_like_cas(url: str, title: str) -> bool:
    u = (url or "").lower()
    t = (title or "").lower()
    return (
        ("cas.byu.edu" in u)
        or ("central authentication service" in t)
        or (t.strip().startswith("cas"))
        or ("login" in t and "cas" in u)
    )


def ensure_http_url(label: str, url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise SystemExit(f"{label} URL is required.")
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise SystemExit(f"{label} URL must be http(s): {url}")
    return url


# ---------- Repo root + config IO ----------

def find_repo_root(start: Path) -> Path:
    """
    Walk upward until we find docker-compose.yml (preferred) or .git.
    """
    start = start.resolve()
    for p in [start] + list(start.parents):
        if (p / "docker-compose.yml").exists():
            return p
        if (p / ".git").exists():
            return p
    # fallback: assume tools/ is under repo root
    return start.parents[0]


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise SystemExit(f"Failed to read YAML {path}: {e}")


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    except Exception as e:
        raise SystemExit(f"Failed to write YAML {path}: {e}")


def load_auth_hints(repo_root: Path) -> dict[str, Any]:
    hints_path = repo_root / "data" / "logs" / "auth_hints.json"
    if not hints_path.exists():
        return {}
    try:
        return json.loads(hints_path.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        return {}


def print_auth_hints(hints: dict[str, Any]) -> None:
    """
    Auth hints are best-effort signals gathered during previous crawl attempts.

    When the crawler tries to fetch pages and is redirected to common
    authentication endpoints (CAS, Duo, Brightspot auth, etc.), we record:
      - which auth hosts were seen most often
      - which target domains most frequently triggered those redirects

    These hints help you decide whether a single auth profile may be reused
    across multiple domains. They are advisory, not guarantees.
    """
    by_domain = hints.get("by_domain", {}) or {}
    if not by_domain:
        return

    # Aggregate redirect hosts (CAS, Duo, Brightspot auth, etc.)
    redirect_hosts: dict[str, int] = {}
    for domain, entry in by_domain.items():
        host = (entry.get("redirect_host") or "").strip()
        count = int(entry.get("count", 0) or 0)
        if host:
            redirect_hosts[host] = redirect_hosts.get(host, 0) + count

    print("\n" + "=" * 70)
    print("AUTH HINTS (from previous crawl logs: data/logs/auth_hints.json)")
    print("=" * 70)

    if redirect_hosts:
        frequent = ", ".join(sorted(redirect_hosts.keys()))
        print("\nWe found these login/SSO redirects while crawling")
        print("(sites likely need authentication):")
        print(f"  {frequent}")

    suggested_domains = ", ".join(sorted(by_domain.keys()))
    if suggested_domains:
        print("\nThese domains showed similar auth behavior and may use the")
        print("same auth profile:")
        print(f"  {suggested_domains}")
        print("\nRecommendation: Start with one domain, add test URLs to validate,")
        print("then add more domains if they share the same login flow.")

    print("=" * 70 + "\n")


# ---------- Profile model ----------

@dataclass
class AuthProfile:
    name: str
    storage_state_path: str
    use_for_domains: list[str]
    start_url: str
    test_urls: list[str]


def coerce_profile(name: str, raw: dict[str, Any]) -> AuthProfile:
    storage_state_path = (raw.get("storage_state_path") or "").strip()
    use_for_domains = raw.get("use_for_domains") or []
    start_url = (raw.get("start_url") or "").strip()
    test_urls = raw.get("test_urls") or []

    if isinstance(use_for_domains, str):
        use_for_domains = [x.strip() for x in use_for_domains.split(",") if x.strip()]
    if not isinstance(use_for_domains, list):
        use_for_domains = []

    if isinstance(test_urls, str):
        test_urls = [x.strip() for x in test_urls.split(",") if x.strip()]
    if not isinstance(test_urls, list):
        test_urls = []

    # Gentle defaults
    if not start_url and use_for_domains:
        start_url = f"https://{use_for_domains[0]}/"
    if not test_urls:
        test_urls = [DEFAULT_TEST_URL] if "policy.byu.edu" in " ".join(use_for_domains) else []

    return AuthProfile(
        name=name,
        storage_state_path=storage_state_path,
        use_for_domains=[str(x).strip() for x in use_for_domains if str(x).strip()],
        start_url=start_url,
        test_urls=[str(x).strip() for x in test_urls if str(x).strip()],
    )


def profile_to_dict(p: AuthProfile) -> dict[str, Any]:
    return {
        "storage_state_path": p.storage_state_path,
        "use_for_domains": p.use_for_domains,
        "start_url": p.start_url,
        "test_urls": p.test_urls,
    }


# ---------- UX helpers ----------

def prompt_choice(prompt: str, n_options: int) -> int:
    while True:
        s = input(prompt).strip()
        if not s.isdigit():
            print("Please enter a number.")
            continue
        idx = int(s)
        if 0 <= idx <= n_options:
            return idx
        print("Invalid selection.")


def prompt_default(prompt: str, default: str) -> str:
    if default:
        s = input(f"{prompt} [{default}]: ").strip()
        return s or default
    return input(f"{prompt}: ").strip()


def resolve_out_path(repo_root: Path, storage_state_path: str) -> Path:
    out = Path(storage_state_path)
    if not out.is_absolute():
        out = (repo_root / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def dump_state_summary(state_path: Path, want_domains: list[str]) -> None:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️ Could not read saved state JSON: {e}")
        return

    cookies = data.get("cookies", []) or []
    origins = data.get("origins", []) or []
    domains = sorted({c.get("domain", "") for c in cookies if c.get("domain")})

    print("\nSaved state summary:")
    print(f"  cookies: {len(cookies)}")
    print(f"  cookie domains: {', '.join(domains) if domains else '(none)'}")
    print(f"  origins/localStorage entries: {len(origins)}")

    has_cas = any("cas." in d or "cas" in d for d in domains)
    print(f"  has CAS-ish cookies: {has_cas}")

    # Per-domain flags
    for d in want_domains:
        has = any(d in cd for cd in domains)
        print(f"  has cookies for {d}: {has}")


# ---------- Playwright flow ----------

def preflight_open(page, url: str, label: str) -> None:
    """
    Quick "can we reach the login site?" check.
    Not a full auth check — just ensures navigation works and prints status/URL/title.
    """
    print(f"\nPreflight: opening {label}: {url}")
    resp = page.goto(url, wait_until="domcontentloaded")
    status = resp.status if resp else None
    final_url = page.url
    title = page.title()
    print(f"  HTTP status: {status}")
    print(f"  Final URL:   {final_url}")
    print(f"  Title:       {title}")


def validate_urls(page, test_urls: list[str]) -> tuple[bool, list[str]]:
    """
    Validate by loading protected URLs and ensuring we are not redirected to CAS.
    Returns (ok, failures).
    """
    failures: list[str] = []
    for u in test_urls:
        print(f"\nValidating access to: {u}")
        resp = page.goto(u, wait_until="domcontentloaded")
        status = resp.status if resp else None
        final_url = page.url
        title = page.title()
        print(f"  HTTP status: {status}")
        print(f"  Final URL:   {final_url}")
        print(f"  Title:       {title}")

        if looks_like_cas(final_url, title):
            failures.append(f"{u} -> redirected to CAS ({final_url})")
            continue

        # Optional: treat non-2xx/3xx as failure (still might render, but usually not useful for crawl)
        if status is not None and status >= 400:
            failures.append(f"{u} -> HTTP {status} ({final_url})")

    return (len(failures) == 0), failures


# ---------- Main ----------

def main() -> None:
    ap = argparse.ArgumentParser(description="Capture Playwright auth state with profiles + validation.")
    ap.add_argument("--config", default="config/crawler.yml", help="YAML config path (relative to repo root by default).")
    ap.add_argument("--profile", default="", help="Profile name to use directly (skips menu if exists).")
    ap.add_argument("--headless", action="store_true", help="Launch Chromium headless (NOT recommended for manual login).")
    ap.add_argument("--no-hints", action="store_true", help="Do not print auth_hints suggestions.")
    args = ap.parse_args()

    repo_root = find_repo_root(Path(__file__))
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (repo_root / config_path).resolve()

    config = load_yaml(config_path)
    playwright_cfg = config.setdefault("playwright", {})
    auth_profiles = playwright_cfg.setdefault("auth_profiles", {})

    if not args.no_hints:
        hints = load_auth_hints(repo_root)
        print_auth_hints(hints)

    # Build profile list
    profile_names = sorted(auth_profiles.keys())

    # Choose/create profile
    chosen_name = (args.profile or "").strip()
    if chosen_name and chosen_name not in auth_profiles:
        print(f"❌ Profile '{chosen_name}' not found in {config_path}")
        print(f"Available profiles: {', '.join(profile_names) if profile_names else '(none)'}")
        raise SystemExit(2)

    if not chosen_name:
        print("Select an auth profile to update/use:")
        print("0) Create new profile")
        for idx, name in enumerate(profile_names, 1):
            print(f"{idx}) {name}")
        selection = prompt_choice("Enter choice: ", len(profile_names))
        if selection == 0:
            chosen_name = input("New profile name: ").strip()
            if not chosen_name:
                raise SystemExit("Profile name is required.")
            if chosen_name in auth_profiles:
                raise SystemExit(f"Profile '{chosen_name}' already exists.")
            auth_profiles[chosen_name] = {}
        else:
            chosen_name = profile_names[selection - 1]

    raw_profile = auth_profiles.get(chosen_name, {}) or {}
    prof = coerce_profile(chosen_name, raw_profile)

    # Prompt for missing/overrideable fields
    default_storage = prof.storage_state_path or f"secrets/playwright/{chosen_name}-storageState.json"
    prof.storage_state_path = prompt_default("Storage state path", default_storage).strip()

    domains_default = ", ".join(prof.use_for_domains) if prof.use_for_domains else ""
    domains_raw = prompt_default("Use-for domains (comma-separated)", domains_default).strip()
    prof.use_for_domains = [d.strip() for d in domains_raw.split(",") if d.strip()]

    start_default = prof.start_url or (f"https://{prof.use_for_domains[0]}/" if prof.use_for_domains else "")
    prof.start_url = prompt_default("Start URL (login entry point)", start_default).strip()
    prof.start_url = ensure_http_url("Start", prof.start_url)

    tests_default = ", ".join(prof.test_urls) if prof.test_urls else (DEFAULT_TEST_URL if prof.use_for_domains else "")
    tests_raw = prompt_default(
        "Test URLs (comma-separated protected pages to validate; at least 1 recommended)",
        tests_default,
    ).strip()
    prof.test_urls = [ensure_http_url("Test", u.strip()) for u in tests_raw.split(",") if u.strip()]
    if not prof.test_urls:
        print("⚠️ No test URLs provided. You can still save state, but you lose the safety check.")
        print("   For CAS sites, you really want at least one protected test URL.\n")

    # Persist profile back to config
    auth_profiles[prof.name] = profile_to_dict(prof)
    save_yaml(config_path, config)
    print(f"\n✅ Updated profile '{prof.name}' in {config_path}")

    out_path = resolve_out_path(repo_root, prof.storage_state_path)

    # Run Playwright login + validation + save
    print("\nLaunching Chromium (headed recommended).")
    print("Workflow:")
    print("  1) Log in (MFA included) in the browser window.")
    print("  2) After login, optionally navigate around to confirm you can see protected content.")
    print("  3) Press ENTER here to validate test URL(s) and save storageState.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context()
        page = context.new_page()

        # Preflight: can we open the start URL at all?
        preflight_open(page, prof.start_url, "start_url")

        while True:
            input("\nWhen you are fully logged in and can see normal content, press ENTER to validate + save... ")

            try:
                # If no test urls, skip validation
                ok = True
                failures: list[str] = []
                if prof.test_urls:
                    ok, failures = validate_urls(page, prof.test_urls)

                if not ok:
                    print("\n❌ Validation failed (auth likely NOT usable for crawling):")
                    for f in failures:
                        print(f"  - {f}")
                    print("\nTry these (in the SAME Playwright window) then retry:")
                    print("  - Re-login and ensure you end on the target domain (not cas.byu.edu).")
                    print("  - Open the exact protected URL(s) and confirm you can see the real page.")
                    print("  - If it keeps bouncing, CAS session may not be persisting (cookies/SameSite).")

                    ans = input("\nRetry validation after you fix login? [y/N]: ").strip().lower()
                    if ans == "y":
                        continue

                    print("\nNot saving state.")
                    context.close()
                    browser.close()
                    raise SystemExit(2)

                # Save state only after successful validation (or if user gave no test urls)
                context.storage_state(path=str(out_path))
                print(f"\n✅ Saved auth state to: {out_path}")

                dump_state_summary(out_path, prof.use_for_domains)
                context.close()
                browser.close()
                break

            except Exception as e:
                # Check if it's a TargetClosedError or similar browser-closed error
                error_msg = str(e).lower()
                if "closed" in error_msg or "target" in error_msg:
                    print("\n" + "=" * 70)
                    print("❌ ERROR: Browser window was closed before validation")
                    print("=" * 70)
                    print("\nIt looks like the browser window was closed before validation.")
                    print("\nIMPORTANT:")
                    print("  • Do NOT close the browser window manually")
                    print("  • Keep the browser window open during the entire process")
                    print("  • Return to this terminal and press ENTER to continue")
                    print("  • The script will validate and save your auth state")
                    print("\nAuth state was NOT saved. Please run the script again.")
                    print("=" * 70 + "\n")
                    try:
                        context.close()
                        browser.close()
                    except:
                        pass
                    raise SystemExit(2)
                else:
                    # Re-raise unexpected errors
                    raise

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
