from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://e-learning.youpass.vn/"


async def main() -> None:
    profile = Path("data/browser-profile")
    profile.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile), headless=False, channel="chrome"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        print("YouPass login window is open. Close the entire window when login is complete.", flush=True)
        await context.wait_for_event("close", timeout=0)


if __name__ == "__main__":
    asyncio.run(main())
