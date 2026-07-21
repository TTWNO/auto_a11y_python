"""Render the user-guide diagrams (diagrams.html) to PNG via Playwright."""
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "docs" / "images" / "user-guide"


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        pg = await browser.new_page(device_scale_factor=2)
        await pg.goto(f"file://{HERE / 'diagrams.html'}")
        await pg.locator("#workflow").screenshot(path=str(OUT / "diagram-workflow.png"))
        await pg.locator("#concepts").screenshot(path=str(OUT / "diagram-concepts.png"))
        print("rendered diagram-workflow.png, diagram-concepts.png")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
