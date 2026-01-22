from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


DEFAULT_TEST_URL = "https://policy.byu.edu/view/business-gifts-and-entertainment-policy"


def looks_like_cas(url: str, title: str) -> bool:
    u = (url or "").lower()
    t = (title or "").lower()
    return ("cas.byu.edu" in u) or ("central authentication service" in t) or (t.strip().startswith("cas"))


def dump_cookie_summary(state_path: Path) -> None:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️ Could not read saved state JSON: {e}")
        return

    cookies = data.get("cookies", []) or []
    origins = data.get("origins", []) or []

    domains = sorted({c.get("domain", "") for c in cookies if c.get("domain")})
    cookie_count = len(cookies)
    origin_count = len(origins)

    print(f"\nSaved state summary:")
    print(f"  cookies: {cookie_count}")
    print(f"  cookie domains: {', '.join(domains) if domains else '(none)'}")
    print(f"  origins/localStorage entries: {origin_count}")

    # Helpful: do we have policy + cas cookies at all?
    has_policy = any("policy.byu.edu" in d for d in domains)
    has_cas = any("cas.byu.edu" in d for d in domains)
    print(f"  has policy.byu.edu cookies: {has_policy}")
    print(f"  has cas.byu.edu cookies: {has_cas}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-url", default="https://policy.byu.edu/", help="URL to open for login")
    ap.add_argument("--test-url", default=DEFAULT_TEST_URL, help="Protected URL to validate after login")
    ap.add_argument(
        "--out",
        default="secrets/playwright/policy-byu-storageState.json",
        help="Storage state output path (relative to repo root by default)",
    )
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = (repo_root / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    start_url = args.start_url
    test_url = args.test_url

    # sanity: ensure start/test url look like https
    for label, url in [("start", start_url), ("test", test_url)]:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            raise SystemExit(f"{label} URL must be http(s): {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print(f"Opening for login: {start_url}")
        page.goto(start_url, wait_until="domcontentloaded")

        print("\nComplete CAS/MFA login in the browser window.")
        print("Tip: After login, manually navigate to a protected policy page to confirm you can see content.")
        input("When you can see normal policy content, press ENTER here to validate + save... ")

        # Validate by loading a known protected URL and checking for CAS redirect
        print(f"\nValidating access to: {test_url}")
        resp = page.goto(test_url, wait_until="domcontentloaded")
        final_url = page.url
        title = page.title()

        print(f"HTTP status: {resp.status if resp else 'no response'}")
        print(f"Final URL:   {final_url}")
        print(f"Title:      {title}")

        if looks_like_cas(final_url, title):
            print("\n❌ AUTH STILL INVALID: You were redirected to CAS during validation.")
            print("Do NOT save this state; it will not work for the crawler.")
            print("\nThings to try in the same Playwright window:")
            print("  - Re-login and make sure you end on policy.byu.edu (not cas.byu.edu).")
            print("  - Open the exact protected policy URL in the browser and confirm you see the policy page.")
            print("  - If it keeps bouncing, your CAS session may not be persisting (cookies blocked / SameSite).")
            browser.close()
            raise SystemExit(2)

        # Save storage state only after successful validation
        context.storage_state(path=str(out_path))
        print(f"\n✅ Saved auth state to: {out_path}")
        dump_cookie_summary(out_path)

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
