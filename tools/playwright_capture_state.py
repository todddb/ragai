#!/usr/bin/env python3
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Open a Chromium session and save Playwright storage state after login."
    )
    parser.add_argument(
        "--url",
        default="https://policy.byu.edu",
        help="URL to open for interactive login.",
    )
    parser.add_argument(
        "--output",
        default="secrets/playwright/policy-byu-storageState.json",
        help="Path to write the storage state JSON.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium in headless mode (not recommended for manual login).",
    )
    args = parser.parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        input("Complete login in the browser, then press Enter to save storage state...")
        context.storage_state(path=str(output_path))
        context.close()
        browser.close()

    print(f"Saved storage state to {output_path}")


if __name__ == "__main__":
    main()
