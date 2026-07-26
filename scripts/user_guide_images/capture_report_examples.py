"""Screenshot generated report files for the user guide.

Usage: capture_report_examples.py NAME=/path/to/report.html [NAME=PATH ...]
Each NAME becomes docs/images/user-guide/NAME.png.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent.parent
OUT = REPO / "docs" / "images" / "user-guide"


async def main() -> None:
    pairs: list[tuple[str, Path]] = []
    for arg in sys.argv[1:]:
        name, _, path = arg.partition("=")
        if not path:
            raise SystemExit(f"expected NAME=PATH, got: {arg}")
        pairs.append((name, Path(path).resolve()))
    if not pairs:
        raise SystemExit(__doc__)

    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        pg = await browser.new_page(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=2
        )
        await pg.emulate_media(color_scheme="light")
        for name, path in pairs:
            await pg.goto(f"file://{path}")
            await pg.wait_for_timeout(600)
            await pg.screenshot(path=str(OUT / f"{name}.png"))
            print("captured", name)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
