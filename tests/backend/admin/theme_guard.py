"""The removed third-party admin theme's name, spelled so no source file contains it.

The "no third-party theme" guard test fails on any first-party file that mentions the
package, so every other test that needs to assert its absence (rendered pages,
templates, settings) takes the name from here instead of writing it out.
"""

from __future__ import annotations

REMOVED_THEME = "".join(("un", "fold"))
REMOVED_THEME_BYTES = REMOVED_THEME.encode()


def mentions_removed_theme(content: bytes | str) -> bool:
    """True when ``content`` (a rendered page, template or source) names the theme."""
    haystack = content if isinstance(content, bytes) else content.encode()
    return REMOVED_THEME_BYTES in haystack.lower()
