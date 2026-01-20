from playwright.sync_api import sync_playwright
from pathlib import Path
import argparse
import time

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://policy.byu.edu")
    ap.add_argument("--out", default="secrets/playwright/policy-byu-storageState.json")
    ap.add_argument("--autosave", type=int, default=30, help="Autosave interval seconds")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    out_path = (repo_root / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("\nLaunching Chromium (headed).", flush=True)
    print("1) Log in in the browser window (MFA included).", flush=True)
    print("2) When fully logged in, CLOSE THE BROWSER WINDOW.", flush=True)
    print(f"3) Autosaves every {args.autosave}s and exits on browser close.\n", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")

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

        # Browser is closed
        try:
            context.storage_state(path=str(out_path))
            print(f"\n✅ Final saved storageState to: {out_path}", flush=True)
        except Exception as e:
            print(f"\n⚠️ Could not do final save (autosave file should exist). Error: {e}", flush=True)

if __name__ == "__main__":
    main()
