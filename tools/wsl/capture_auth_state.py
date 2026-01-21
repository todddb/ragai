from pathlib import Path
import argparse
import json
import time

import yaml
from playwright.sync_api import sync_playwright


def _load_crawler_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def _save_crawler_config(config_path: Path, config: dict) -> None:
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _load_auth_hints(repo_root: Path) -> dict:
    hints_path = repo_root / "data" / "logs" / "auth_hints.json"
    if not hints_path.exists():
        return {}
    try:
        return json.loads(hints_path.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        return {}


def _print_auth_hints(hints: dict) -> None:
    by_domain = hints.get("by_domain", {}) or {}
    if not by_domain:
        return
    redirect_hosts = {}
    for domain, entry in by_domain.items():
        host = entry.get("redirect_host") or ""
        if host:
            redirect_hosts[host] = redirect_hosts.get(host, 0) + entry.get("count", 0)
    if redirect_hosts:
        frequent = ", ".join(sorted(redirect_hosts.keys()))
        print(f"\nWe've seen frequent redirects to: {frequent}")
    suggested_domains = ", ".join(sorted(by_domain.keys()))
    if suggested_domains:
        print(f"Suggested profile domains: {suggested_domains}\n")


def _prompt_choice(prompt: str, options: list[str]) -> int:
    while True:
        choice = input(prompt).strip()
        if not choice.isdigit():
            print("Please enter a number.")
            continue
        idx = int(choice)
        if 0 <= idx <= len(options):
            return idx
        print("Invalid selection.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--autosave", type=int, default=30, help="Autosave interval seconds")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    config_path = repo_root / "config" / "crawler.yml"
    config = _load_crawler_config(config_path)
    playwright_cfg = config.setdefault("playwright", {})
    auth_profiles = playwright_cfg.setdefault("auth_profiles", {})

    hints = _load_auth_hints(repo_root)
    _print_auth_hints(hints)

    profile_names = sorted(auth_profiles.keys())
    print("Select an auth profile to update:")
    print("0) Create new profile")
    for idx, name in enumerate(profile_names, 1):
        print(f"{idx}) {name}")

    selection = _prompt_choice("Enter choice: ", profile_names)
    if selection == 0:
        profile_name = input("New profile name: ").strip()
        if not profile_name:
            raise SystemExit("Profile name is required.")
        storage_state_path = input(
            f"Storage state path (default /app/secrets/playwright/{profile_name}-storageState.json): "
        ).strip()
        if not storage_state_path:
            storage_state_path = f"/app/secrets/playwright/{profile_name}-storageState.json"
        domains_raw = input("Use-for domains (comma-separated, optional): ").strip()
        use_for_domains = [d.strip() for d in domains_raw.split(",") if d.strip()]
        auth_profiles[profile_name] = {
            "storage_state_path": storage_state_path,
            "use_for_domains": use_for_domains,
        }
        _save_crawler_config(config_path, config)
    else:
        profile_name = profile_names[selection - 1]

    profile = auth_profiles.get(profile_name, {})
    storage_state_path = profile.get("storage_state_path")
    if not storage_state_path:
        storage_state_path = input("Storage state path (absolute): ").strip()
        if not storage_state_path:
            raise SystemExit("Storage state path is required.")
        profile["storage_state_path"] = storage_state_path
        auth_profiles[profile_name] = profile
        _save_crawler_config(config_path, config)

    domains = profile.get("use_for_domains", [])
    default_url = f"https://{domains[0]}/" if domains else ""
    url_prompt = f"Target URL to open [{default_url}]: " if default_url else "Target URL to open: "
    target_url = input(url_prompt).strip() or default_url
    if not target_url:
        raise SystemExit("Target URL is required.")

    out_path = Path(storage_state_path)
    if not out_path.is_absolute():
        out_path = (repo_root / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("\nLaunching Chromium (headed).", flush=True)
    print("1) Log in in the browser window (MFA included).", flush=True)
    print("2) When fully logged in, CLOSE THE BROWSER WINDOW.", flush=True)
    print(f"3) Autosaves every {args.autosave}s and exits on browser close.\n", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(target_url, wait_until="domcontentloaded")

        last_save = 0.0
        while browser.is_connected():
            now = time.time()
            if now - last_save >= args.autosave:
                last_save = now
                try:
                    context.storage_state(path=str(out_path))
                    print(f"...autosaved: {out_path}", flush=True)
                except Exception as e:
                    print(f"...autosave failed (often harmless pre-login): {e}", flush=True)
            time.sleep(0.25)

        try:
            context.storage_state(path=str(out_path))
            print(f"\n✅ Final saved storageState to: {out_path}", flush=True)
        except Exception as e:
            print(f"\n⚠️ Could not do final save (autosave file should exist). Error: {e}", flush=True)


if __name__ == "__main__":
    main()
