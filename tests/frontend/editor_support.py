"""Playwright helpers for pages whose textareas are upgraded to the rich-text editor.

The editor mounts lazily and then hides the original textarea, so tests must wait for
the editing area instead of filling the textarea (which would race with the mount).
"""

from __future__ import annotations

from playwright.sync_api import Page


def editing_area(page: Page, textarea_selector: str):
    """The contenteditable area that replaces ``textarea_selector`` once mounted."""
    return page.locator(f"{textarea_selector} + .note-editor .note-editable")


def fill_rich_text(
    page: Page, textarea_selector: str, text: str, *, html: bool = False
) -> None:
    """Wait for the editor of ``textarea_selector`` and put ``text`` into it.

    With ``html`` the value is treated as markup (as if pasted); otherwise it is typed.
    """
    page.locator(textarea_selector).evaluate(
        """(element) => {
            const editor = element.nextElementSibling;
            const target = editor && editor.classList.contains('note-editor')
                ? editor : element;
            target.scrollIntoView({ block: 'center' });
        }"""
    )
    area = editing_area(page, textarea_selector)
    area.wait_for(state="visible", timeout=15_000)
    if html:
        area.evaluate(
            """(element, markup) => {
                element.innerHTML = markup;
                element.dispatchEvent(new Event('input', { bubbles: true }));
            }""",
            text,
        )
    else:
        area.click()
        area.press("Control+A")
        area.type(text)
