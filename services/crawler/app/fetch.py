from pathlib import Path
from typing import Dict

import httpx
from playwright.sync_api import sync_playwright


def fetch_html_httpx(url: str, headers: Dict[str, str], timeout: float) -> str:
    response = httpx.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def fetch_html_playwright(
    url: str,
    storage_state_path: str,
    headless: bool,
    timeout_ms: int,
) -> str:
    storage_state = Path(storage_state_path)
    if not storage_state.exists():
        raise FileNotFoundError(f"Playwright storage state not found: {storage_state_path}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = None
        try:
            context = browser.new_context(storage_state=str(storage_state))
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return page.content()
        finally:
            if context is not None:
                context.close()
            browser.close()
