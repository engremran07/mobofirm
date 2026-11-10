"""Catalogue of the rich-text editor features and what each one may produce.

A feature is the unit an administrator switches on or off in an editor profile. The
same catalogue drives three things so they can never drift apart: the toolbar the
browser builds, the checkboxes of the admin form, and the nh3 allow-list that decides
which tags, attributes and class names survive on the server.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Feature:
    """One switchable editor capability."""

    key: str
    label: str
    group: str
    tags: frozenset[str] = frozenset()
    classes: frozenset[str] = frozenset()


GROUP_TEXT = "text"
GROUP_PARAGRAPH = "paragraph"
GROUP_INSERT = "insert"
GROUP_TOOLS = "tools"

GROUP_LABELS: dict[str, str] = {
    GROUP_TEXT: "Text formatting",
    GROUP_PARAGRAPH: "Paragraphs and layout",
    GROUP_INSERT: "Insert",
    GROUP_TOOLS: "Tools",
}

COLOR_NAMES: tuple[str, ...] = (
    "red",
    "orange",
    "amber",
    "green",
    "teal",
    "blue",
    "indigo",
    "purple",
    "pink",
    "gray",
)
FONT_FAMILIES: tuple[str, ...] = ("sans", "serif", "mono")
FONT_SIZES: tuple[str, ...] = ("sm", "lg")
ALIGNMENTS: tuple[str, ...] = ("start", "center", "end", "justify")

FONT_CLASSES = frozenset(
    {f"mf-font-{name}" for name in FONT_FAMILIES}
    | {f"mf-size-{name}" for name in FONT_SIZES}
)
COLOR_CLASSES = frozenset(
    {f"mf-fg-{name}" for name in COLOR_NAMES}
    | {f"mf-bg-{name}" for name in COLOR_NAMES}
)
ALIGN_CLASSES = frozenset({f"mf-align-{name}" for name in ALIGNMENTS})
TABLE_CLASS = "mf-table"
VIDEO_CLASS = "mf-video"

FEATURES: tuple[Feature, ...] = (
    Feature("bold", "Bold", GROUP_TEXT, tags=frozenset({"strong", "b"})),
    Feature("italic", "Italic", GROUP_TEXT, tags=frozenset({"em", "i"})),
    Feature("underline", "Underline", GROUP_TEXT, tags=frozenset({"u"})),
    Feature("strike", "Strikethrough", GROUP_TEXT, tags=frozenset({"s", "del"})),
    Feature(
        "script",
        "Subscript and superscript",
        GROUP_TEXT,
        tags=frozenset({"sub", "sup"}),
    ),
    Feature("fonts", "Font families and sizes", GROUP_TEXT, classes=FONT_CLASSES),
    Feature("colors", "Text and highlight colours", GROUP_TEXT, classes=COLOR_CLASSES),
    Feature("clear_format", "Clear formatting", GROUP_TEXT),
    Feature(
        "style",
        "Headings, quotes and code blocks",
        GROUP_PARAGRAPH,
        tags=frozenset({"h2", "h3", "h4", "h5", "blockquote", "pre", "code", "kbd"}),
    ),
    Feature(
        "lists",
        "Bulleted and numbered lists",
        GROUP_PARAGRAPH,
        tags=frozenset({"ul", "ol", "li"}),
    ),
    Feature("align", "Alignment", GROUP_PARAGRAPH, classes=ALIGN_CLASSES),
    Feature("hr", "Horizontal rule", GROUP_PARAGRAPH, tags=frozenset({"hr"})),
    Feature(
        "tables",
        "Tables",
        GROUP_INSERT,
        tags=frozenset(
            {"table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption"}
        ),
        classes=frozenset({TABLE_CLASS}),
    ),
    Feature("links", "Links", GROUP_INSERT, tags=frozenset({"a"})),
    Feature(
        "images",
        "Images",
        GROUP_INSERT,
        tags=frozenset({"img", "figure", "figcaption"}),
    ),
    Feature(
        "video",
        "Video embeds (allow-listed hosts)",
        GROUP_INSERT,
        tags=frozenset({"iframe"}),
        classes=frozenset({VIDEO_CLASS}),
    ),
    Feature("emoji", "Emoji picker", GROUP_INSERT),
    Feature("undo_redo", "Undo and redo", GROUP_TOOLS),
    Feature("code_view", "HTML code view", GROUP_TOOLS),
    Feature("fullscreen", "Fullscreen", GROUP_TOOLS),
    Feature("help", "Keyboard shortcuts help", GROUP_TOOLS),
)

FEATURE_BY_KEY: dict[str, Feature] = {feature.key: feature for feature in FEATURES}
ALL_FEATURE_KEYS: frozenset[str] = frozenset(FEATURE_BY_KEY)

# Tags every profile keeps: they carry text and structure, never behaviour.
BASE_TAGS: frozenset[str] = frozenset({"p", "br", "div", "span"})


def feature_choices() -> list[tuple[str, list[tuple[str, str]]]]:
    """Grouped ``(group label, [(key, label), ...])`` choices for admin forms."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    for feature in FEATURES:
        grouped.setdefault(feature.group, []).append((feature.key, feature.label))
    return [(GROUP_LABELS[group], items) for group, items in grouped.items()]


def normalize_features(keys: object) -> frozenset[str]:
    """Keep only known feature keys; unknown or malformed input is ignored."""
    if not isinstance(keys, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(key for key in keys if isinstance(key, str)) & ALL_FEATURE_KEYS
