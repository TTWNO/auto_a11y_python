"""Tests for native <dialog> modal accessibility in screen reader context.

Verifies that the modal utility (modal.js + modal.css) produces correct
accessibility semantics for screen readers: computed ARIA roles, focus
management, keyboard interaction, and background inertness.

Requires Playwright: python -m playwright install chromium
"""
import os
os.environ.setdefault('RUN_AI_ANALYSIS', 'false')

from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright, Page

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dialog_test_page.html"
FIXTURE_URL = FIXTURE_PATH.as_uri()


@pytest_asyncio.fixture
async def page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        pg = await ctx.new_page()
        await pg.goto(FIXTURE_URL)
        await pg.wait_for_load_state("domcontentloaded")
        yield pg
        await browser.close()


# ---------------------------------------------------------------------------
# 1. Dialog role and aria-modal
# ---------------------------------------------------------------------------

class TestDialogRole:
    """Native <dialog> opened with .showModal() must expose role=dialog
    and aria-modal=true to the accessibility tree."""

    @pytest.mark.asyncio
    async def test_open_dialog_has_role_dialog(self, page: Page):
        await page.click("#openStandard")
        # Playwright's get_by_role queries the accessibility tree —
        # native <dialog> has implicit role="dialog"
        count = await page.get_by_role("dialog", name="Standard Modal").count()
        assert count == 1, (
            "Expected one dialog with role='dialog' and name='Standard Modal' "
            "in the accessibility tree"
        )

    @pytest.mark.asyncio
    async def test_open_dialog_is_modal(self, page: Page):
        """A dialog opened with .showModal() makes background content inert,
        which is the behavioral equivalent of aria-modal=true."""
        await page.click("#openStandard")
        # .showModal() makes the dialog modal: background elements become
        # inert and unfocusable. Verify by attempting to focus a background
        # element — the browser should refuse.
        bg_focusable = await page.evaluate("""() => {
            const bg = document.getElementById('bgButton');
            bg.focus();
            return document.activeElement === bg;
        }""")
        assert not bg_focusable, (
            "Dialog opened with .showModal() should be modal — "
            "background elements should not be focusable"
        )

    @pytest.mark.asyncio
    async def test_closed_dialog_not_visible_to_screen_readers(self, page: Page):
        """A closed <dialog> should not be exposed to screen readers."""
        # Before opening, the dialog should not be findable by role
        count = await page.get_by_role("dialog", name="Standard Modal").count()
        assert count == 0, (
            "Closed dialog should not appear in the accessibility tree"
        )

    @pytest.mark.asyncio
    async def test_dialog_has_implicit_role_not_explicit(self, page: Page):
        """Native <dialog> should not need an explicit role attribute."""
        await page.click("#openStandard")
        explicit_role = await page.locator("#standardModal").get_attribute("role")
        assert explicit_role is None, (
            "Native <dialog> should rely on implicit role, not explicit role attr"
        )


# ---------------------------------------------------------------------------
# 2. Accessible name
# ---------------------------------------------------------------------------

class TestAccessibleName:
    """Dialogs must have an accessible name for screen reader announcements."""

    @pytest.mark.asyncio
    async def test_dialog_named_by_aria_labelledby(self, page: Page):
        await page.click("#openStandard")
        # Verify aria-labelledby links to the title element
        labelledby = await page.locator("#standardModal").get_attribute(
            "aria-labelledby"
        )
        assert labelledby == "standardModalTitle"

        # Verify the referenced element has the expected text
        title_text = await page.locator(f"#{labelledby}").inner_text()
        assert title_text == "Standard Modal"

        # Verify Playwright can find the dialog by its accessible name
        count = await page.get_by_role("dialog", name="Standard Modal").count()
        assert count == 1

    @pytest.mark.asyncio
    async def test_dialog_without_labelledby_has_no_accessible_name(self, page: Page):
        await page.click("#openNoLabel")
        labelledby = await page.locator("#noLabelModal").get_attribute(
            "aria-labelledby"
        )
        assert labelledby is None, (
            "Dialog without aria-labelledby should have no aria-labelledby attr"
        )

    @pytest.mark.asyncio
    async def test_close_button_has_accessible_label(self, page: Page):
        await page.click("#openStandard")
        close_btn = page.locator("#standardModal .btn-close")
        label = await close_btn.get_attribute("aria-label")
        assert label == "Close", (
            f"Close button should have aria-label='Close', got '{label}'"
        )


# ---------------------------------------------------------------------------
# 3. Focus management
# ---------------------------------------------------------------------------

class TestFocusManagement:
    """Screen readers rely on focus position to orient users."""

    @pytest.mark.asyncio
    async def test_focus_moves_into_dialog_on_open(self, page: Page):
        await page.click("#openStandard")
        focus_in_dialog = await page.evaluate("""() => {
            const dialog = document.getElementById('standardModal');
            return dialog.contains(document.activeElement);
        }""")
        assert focus_in_dialog, "Focus should move inside the dialog when opened"

    @pytest.mark.asyncio
    async def test_focus_never_reaches_background_elements(self, page: Page):
        """Tab should cycle within the dialog, never reaching background
        interactive elements. Note: focus may briefly pass through <body>
        between cycles — this is native <dialog> behavior and is fine."""
        await page.click("#openStandard")

        background_ids = {"openStandard", "openPersistent", "openProgrammatic",
                          "openNoLabel", "bgButton", "bgInput", "bgLink"}

        for i in range(10):
            await page.keyboard.press("Tab")
            focused_id = await page.evaluate(
                "() => document.activeElement?.id || ''"
            )
            assert focused_id not in background_ids, (
                f"Tab #{i+1}: focus escaped to background element '{focused_id}'"
            )

    @pytest.mark.asyncio
    async def test_shift_tab_never_reaches_background_elements(self, page: Page):
        await page.click("#openStandard")

        background_ids = {"openStandard", "openPersistent", "openProgrammatic",
                          "openNoLabel", "bgButton", "bgInput", "bgLink"}

        for i in range(10):
            await page.keyboard.press("Shift+Tab")
            focused_id = await page.evaluate(
                "() => document.activeElement?.id || ''"
            )
            assert focused_id not in background_ids, (
                f"Shift+Tab #{i+1}: focus escaped to background '{focused_id}'"
            )

    @pytest.mark.asyncio
    async def test_focus_restores_to_trigger_on_close(self, page: Page):
        await page.click("#openStandard")
        await page.locator("#standardModal .btn-close").click()
        focused_id = await page.evaluate("() => document.activeElement?.id")
        assert focused_id == "openStandard", (
            f"Focus should restore to trigger button, got '{focused_id}'"
        )

    @pytest.mark.asyncio
    async def test_focus_restores_after_escape(self, page: Page):
        await page.click("#openStandard")
        await page.keyboard.press("Escape")
        focused_id = await page.evaluate("() => document.activeElement?.id")
        assert focused_id == "openStandard", (
            f"Focus should restore to trigger after Escape, got '{focused_id}'"
        )

    @pytest.mark.asyncio
    async def test_programmatic_open_moves_focus(self, page: Page):
        await page.click("#openProgrammatic")
        focus_in_dialog = await page.evaluate("""() => {
            const dialog = document.getElementById('programmaticModal');
            return dialog.contains(document.activeElement);
        }""")
        assert focus_in_dialog, (
            "Focus should move inside dialog opened via openModal()"
        )


# ---------------------------------------------------------------------------
# 4. Keyboard interaction
# ---------------------------------------------------------------------------

class TestKeyboardInteraction:
    """Keyboard access is critical for screen reader users."""

    @pytest.mark.asyncio
    async def test_escape_closes_standard_dialog(self, page: Page):
        await page.click("#openStandard")
        is_open = await page.locator("#standardModal").evaluate("el => el.open")
        assert is_open

        await page.keyboard.press("Escape")
        is_open = await page.locator("#standardModal").evaluate("el => el.open")
        assert not is_open, "Escape should close a standard dialog"

    @pytest.mark.asyncio
    async def test_escape_does_not_close_persistent_dialog(self, page: Page):
        await page.click("#openPersistent")
        is_open = await page.locator("#persistentModal").evaluate("el => el.open")
        assert is_open

        await page.keyboard.press("Escape")
        is_open = await page.locator("#persistentModal").evaluate("el => el.open")
        assert is_open, "Escape should NOT close a persistent dialog"

    @pytest.mark.asyncio
    async def test_close_button_keyboard_accessible(self, page: Page):
        await page.click("#openStandard")
        close_btn = page.locator("#standardModal .btn-close")
        await close_btn.focus()
        await page.keyboard.press("Enter")
        is_open = await page.locator("#standardModal").evaluate("el => el.open")
        assert not is_open, "Close button should work via Enter key"

    @pytest.mark.asyncio
    async def test_data_close_modal_button_keyboard_accessible(self, page: Page):
        await page.click("#openStandard")
        cancel_btn = page.locator("#standardModal .modal-footer [data-close-modal]")
        await cancel_btn.focus()
        await page.keyboard.press("Space")
        is_open = await page.locator("#standardModal").evaluate("el => el.open")
        assert not is_open, "Cancel button with data-close-modal should work via Space"


# ---------------------------------------------------------------------------
# 5. Backdrop and dismissal
# ---------------------------------------------------------------------------

class TestBackdropDismissal:

    @pytest.mark.asyncio
    async def test_backdrop_click_closes_standard_dialog(self, page: Page):
        await page.click("#openStandard")
        await page.locator("#standardModal").click(position={"x": 5, "y": 5})
        is_open = await page.locator("#standardModal").evaluate("el => el.open")
        assert not is_open, "Clicking backdrop should close a standard dialog"

    @pytest.mark.asyncio
    async def test_backdrop_click_does_not_close_persistent_dialog(self, page: Page):
        await page.click("#openPersistent")
        await page.locator("#persistentModal").click(position={"x": 5, "y": 5})
        is_open = await page.locator("#persistentModal").evaluate("el => el.open")
        assert is_open, "Clicking backdrop should NOT close a persistent dialog"

    @pytest.mark.asyncio
    async def test_clicking_content_does_not_close_dialog(self, page: Page):
        await page.click("#openStandard")
        await page.locator("#standardModal .modal-body").click()
        is_open = await page.locator("#standardModal").evaluate("el => el.open")
        assert is_open, "Clicking inside the modal content should not close it"


# ---------------------------------------------------------------------------
# 6. Background inertness
# ---------------------------------------------------------------------------

class TestBackgroundInertness:
    """When a modal dialog is open, background content must be inert
    (unreachable by screen readers and keyboard)."""

    @pytest.mark.asyncio
    async def test_background_button_not_focusable(self, page: Page):
        await page.click("#openStandard")
        bg_got_focus = await page.evaluate("""() => {
            const bg = document.getElementById('bgButton');
            bg.focus();
            return document.activeElement === bg;
        }""")
        assert not bg_got_focus, (
            "Background button should not be focusable when dialog is open"
        )

    @pytest.mark.asyncio
    async def test_background_input_not_focusable(self, page: Page):
        await page.click("#openStandard")
        bg_got_focus = await page.evaluate("""() => {
            const bg = document.getElementById('bgInput');
            bg.focus();
            return document.activeElement === bg;
        }""")
        assert not bg_got_focus, (
            "Background input should not be focusable when dialog is open"
        )

    @pytest.mark.asyncio
    async def test_background_link_not_focusable(self, page: Page):
        await page.click("#openStandard")
        bg_got_focus = await page.evaluate("""() => {
            const bg = document.getElementById('bgLink');
            bg.focus();
            return document.activeElement === bg;
        }""")
        assert not bg_got_focus, (
            "Background link should not be focusable when dialog is open"
        )

    @pytest.mark.asyncio
    async def test_background_becomes_reachable_after_close(self, page: Page):
        await page.click("#openStandard")
        await page.keyboard.press("Escape")
        bg_got_focus = await page.evaluate("""() => {
            const bg = document.getElementById('bgButton');
            bg.focus();
            return document.activeElement === bg;
        }""")
        assert bg_got_focus, (
            "Background should become focusable again after dialog closes"
        )


# ---------------------------------------------------------------------------
# 7. Programmatic helpers
# ---------------------------------------------------------------------------

class TestProgrammaticHelpers:

    @pytest.mark.asyncio
    async def test_open_modal_helper(self, page: Page):
        await page.evaluate("openModal('standardModal')")
        is_open = await page.locator("#standardModal").evaluate("el => el.open")
        assert is_open

    @pytest.mark.asyncio
    async def test_close_modal_helper(self, page: Page):
        await page.evaluate("openModal('standardModal')")
        await page.evaluate("closeModal('standardModal')")
        is_open = await page.locator("#standardModal").evaluate("el => el.open")
        assert not is_open

    @pytest.mark.asyncio
    async def test_open_modal_nonexistent_id_does_not_throw(self, page: Page):
        error = await page.evaluate("""() => {
            try { openModal('nonexistent'); return null; }
            catch (e) { return e.message; }
        }""")
        assert error is None

    @pytest.mark.asyncio
    async def test_close_modal_nonexistent_id_does_not_throw(self, page: Page):
        error = await page.evaluate("""() => {
            try { closeModal('nonexistent'); return null; }
            catch (e) { return e.message; }
        }""")
        assert error is None
