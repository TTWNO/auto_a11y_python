"""Capture the user-guide app screenshots (01-16) from a running instance.

Requires the app running in desktop mode (auto-login) and Playwright's
Chromium. See README.md in this directory for the full regeneration flow.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import Page, async_playwright

REPO = Path(__file__).resolve().parent.parent.parent
OUT = REPO / "docs" / "images" / "user-guide"


def shots(project_id: str, website_id: str, page_id: str) -> list[tuple[str, str, str | None]]:
    """(image name, url path, optional action) for each capture."""
    return [
        ("01-dashboard", "/dashboard", None),
        ("02-projects-list", "/projects/", None),
        ("03-project-create", "/projects/create", None),
        ("04-project-view", f"/projects/{project_id}", None),
        ("05-website-view", f"/websites/{website_id}", None),
        ("06-website-pages", f"/websites/{website_id}", "scroll_to_pages"),
        ("07-page-view", f"/pages/{page_id}", None),
        ("08-page-results", f"/pages/{page_id}", "scroll_to_results"),
        ("09-page-issue-expanded", f"/pages/{page_id}", "expand_issue"),
        ("10-testing-dashboard", "/testing/dashboard", None),
        ("11-fixture-status", "/testing/fixture-status", None),
        ("12-trends", "/testing/trends", None),
        ("13-reports-dashboard", "/reports/dashboard", None),
        ("14-report-modal", "/reports/dashboard", "open_static_modal"),
        ("15-schedules", "/schedules", None),
        ("16-help", "/help", None),
    ]


async def do_action(pg: Page, action: str) -> None:
    if action == "scroll_to_pages":
        await pg.evaluate(
            "() => { const h = [...document.querySelectorAll('h2,h3')]"
            ".find(e => /Pages/i.test(e.textContent)); if (h) h.scrollIntoView(); }"
        )
        await pg.wait_for_timeout(400)
    elif action == "scroll_to_results":
        await pg.evaluate(
            "() => { const h = [...document.querySelectorAll('h2,h3')]"
            ".find(e => /Latest Test Results/i.test(e.textContent));"
            " if (h) h.scrollIntoView({block:'start'}); }"
        )
        await pg.wait_for_timeout(400)
    elif action == "expand_issue":
        await do_action(pg, "scroll_to_results")
        btn = pg.locator(".accordion-button.collapsed").first
        await btn.click()
        await pg.wait_for_timeout(700)
        await btn.scroll_into_view_if_needed()
        await pg.wait_for_timeout(300)
    elif action == "open_static_modal":
        btn = pg.locator(
            "button:has-text('Offline Report'), [data-bs-target='#staticHtmlModal']"
        ).first
        await btn.click()
        await pg.wait_for_timeout(600)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5001")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--website-id", required=True)
    parser.add_argument("--page-id", required=True, help="A tested page with issues")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        pg = await browser.new_page(
            viewport={"width": 1440, "height": 900}, device_scale_factor=2
        )
        await pg.emulate_media(color_scheme="light")
        for name, url, action in shots(args.project_id, args.website_id, args.page_id):
            try:
                await pg.goto(args.base + url, wait_until="networkidle", timeout=20000)
            except Exception:
                pass  # screenshot whatever loaded
            await pg.wait_for_timeout(500)
            if action:
                await do_action(pg, action)
            await pg.screenshot(path=str(OUT / f"{name}.png"))
            print("captured", name)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
