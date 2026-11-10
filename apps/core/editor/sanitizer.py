"""Profile-driven HTML sanitising: nh3 stays the only authority.

The allow-list is *derived* from the enabled features of an editor profile, so the
server accepts exactly what the toolbar can produce and nothing more. The output must
render under the strict CSP, therefore: no inline styles (colours, fonts and alignment
are curated class names), no ``data:`` URLs, images only from the site's own origin and
iframes only for audited video providers, rewritten to their privacy-friendly player.
"""

from __future__ import annotations

import html as html_lib
import importlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from django.utils.html import escape, linebreaks
from django.utils.safestring import SafeString, mark_safe

from .features import ALL_FEATURE_KEYS, BASE_TAGS, FEATURE_BY_KEY
from .spec import ProfileSpec, UploadRules, get_profile
from .video import DEFAULT_PROVIDERS, rewrite_video_url

# Loaded dynamically: nh3 is a compiled extension that static analysis cannot read.
nh3: Any = importlib.import_module("nh3")

URL_SCHEMES = {"http", "https", "mailto", "tel"}
DROPPED_WITH_CONTENT = {
    "script",
    "style",
    "svg",
    "math",
    "noscript",
    "template",
    "textarea",
    "title",
    "object",
    "embed",
    "applet",
}
LINK_REL = "nofollow noopener"
IFRAME_FIXED_ATTRIBUTES = {
    "loading": "lazy",
    "referrerpolicy": "strict-origin-when-cross-origin",
    "allowfullscreen": "",
    "sandbox": "allow-scripts allow-same-origin allow-presentation",
    "class": "mf-video",
}
RENDER_PROFILE = ProfileSpec(
    key="render",
    features=ALL_FEATURE_KEYS,
    video_providers=DEFAULT_PROVIDERS,
    upload=UploadRules(enabled=True),
)
MEDIA_TAGS = frozenset({"img", "iframe", "hr", "table"})
NUMERIC_ATTRIBUTES = frozenset({"width", "height", "colspan", "rowspan", "start"})
SCOPE_VALUES = frozenset({"row", "col", "rowgroup", "colgroup"})

_NUMBER = re.compile(r"^[0-9]{1,4}$")
_CONTROL = re.compile(r"[\x00-\x20\x7f]")
_MEDIA = re.compile(
    r"<(?P<name>img|iframe)\b(?P<attrs>(?:[^>\"']|\"[^\"]*\")*)>(?:</iframe>)?"
)
_HAS_SRC = re.compile(r"\ssrc=\"")
_HAS_ALT = re.compile(r"\salt=")
_IMG = re.compile(r"<img\b(?P<attrs>(?:[^>\"']|\"[^\"]*\")*?)\s*/?>", re.IGNORECASE)
_HREF = re.compile(r'\shref="([^"]*)"')
HTML_HINT = re.compile(r"<[a-zA-Z!/]")


@dataclass(frozen=True, slots=True)
class Policy:
    """The tags, class names and video providers a profile permits."""

    tags: frozenset[str]
    classes: frozenset[str]
    video_providers: tuple[str, ...]


@lru_cache(maxsize=256)
def build_policy(spec: ProfileSpec) -> Policy:
    """Derive the allow-list from ``spec``'s enabled features."""
    tags: set[str] = set(BASE_TAGS)
    classes: set[str] = set()
    for key in spec.features:
        feature = FEATURE_BY_KEY.get(key)
        if feature is None:
            continue
        tags |= feature.tags
        classes |= feature.classes
    if not spec.video_enabled:
        tags.discard("iframe")
        classes.discard("mf-video")
    return Policy(
        tags=frozenset(tags),
        classes=frozenset(classes),
        video_providers=spec.video_providers if spec.video_enabled else (),
    )


def _attributes(policy: Policy) -> dict[str, set[str]]:
    attributes: dict[str, set[str]] = {}
    if policy.classes:
        attributes["*"] = {"class"}
    if "a" in policy.tags:
        attributes["a"] = {"href", "title"}
    if "img" in policy.tags:
        attributes["img"] = {"src", "alt", "width", "height", "loading"}
    if "iframe" in policy.tags:
        attributes["iframe"] = {"src", "title", "width", "height"}
    if "td" in policy.tags:
        attributes["td"] = {"colspan", "rowspan"}
        attributes["th"] = {"colspan", "rowspan", "scope"}
    if "ol" in policy.tags:
        attributes["ol"] = {"start"}
    return attributes


def _same_origin_path(value: str) -> bool:
    return (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and not _CONTROL.search(value)
    )


def _filter_value(policy: Policy, tag: str, attribute: str, value: str) -> str | None:
    if attribute == "class":
        kept = [token for token in value.split() if token in policy.classes]
        return " ".join(dict.fromkeys(kept)) or None
    if attribute == "src":
        if tag == "iframe":
            return rewrite_video_url(value, policy.video_providers)
        return value if _same_origin_path(value) else None
    if attribute in NUMERIC_ATTRIBUTES:
        return value if _NUMBER.fullmatch(value) else None
    if attribute == "scope":
        return value if value in SCOPE_VALUES else None
    return "lazy" if attribute == "loading" else value


def _make_filter(policy: Policy) -> Callable[[str, str, str], str | None]:
    def attribute_filter(tag: str, attribute: str, value: str) -> str | None:
        return _filter_value(policy, tag, attribute, value)

    return attribute_filter


def _drop_srcless(match: re.Match[str]) -> str:
    return match.group(0) if _HAS_SRC.search(match.group("attrs")) else ""


def _strip_srcless_media(cleaned: str) -> str:
    without_src = _MEDIA.sub(_drop_srcless, cleaned)
    return without_src


def _complete_image(match: re.Match[str]) -> str:
    """Give every kept ``<img>`` an ``alt`` (empty marks it decorative)."""
    attributes = match.group("attrs")
    if _HAS_ALT.search(attributes):
        return match.group(0)
    return f'<img{attributes} alt="">'


def _complete_images(cleaned: str) -> str:
    return _IMG.sub(_complete_image, cleaned)


def _resolve(profile: ProfileSpec | str) -> ProfileSpec:
    return get_profile(profile) if isinstance(profile, str) else profile


def text_content(html: str) -> str:
    """The visible text of ``html`` (tags removed, entities decoded)."""
    if not html:
        return ""
    stripped = nh3.clean(html, tags=set(), clean_content_tags=DROPPED_WITH_CONTENT)
    return html_lib.unescape(stripped).replace("\xa0", " ").strip()


def text_with_links(html: str) -> str:
    """Visible text plus every link target, for screening (spam, link counts)."""
    links = " ".join(_HREF.findall(html or ""))
    return f"{text_content(html)} {links}".strip()


def text_length(html: str) -> int:
    """Length of the visible text, used for the profile's ``max_length``."""
    return len(text_content(html))


_BLOCK_END = re.compile(r"</(?:p|div|li|h[1-6]|blockquote|tr|pre)>|<br\s*/?>", re.I)


def plain_text(html: str | None) -> str:
    """One-line visible text for cards, snippets and meta tags.

    Block boundaries become spaces so words never run together; legacy plain text
    passes through unchanged. The result is *not* HTML: escape it when rendering.
    """
    if not html:
        return ""
    spaced = _BLOCK_END.sub(" ", html)
    return " ".join(text_content(spaced).split())


def _has_content(cleaned: str) -> bool:
    if text_content(cleaned):
        return True
    return any(f"<{tag}" in cleaned for tag in MEDIA_TAGS)


def _as_paragraphs(text: str) -> str:
    """Wrap tag-less text (already entity-safe) into paragraphs and line breaks."""
    return str(linebreaks(text, autoescape=False))


def sanitize_rich_text(
    html: str | None, profile: ProfileSpec | str, *, paragraphs: bool = False
) -> str:
    """Return ``html`` reduced to what ``profile`` allows; empty input stays empty.

    With ``paragraphs`` plain text (no markup) is converted to ``<p>`` blocks first
    and tag-less results are wrapped, so the stored value is always HTML.
    """
    if not html or not html.strip():
        return ""
    source = html
    if paragraphs and not HTML_HINT.search(html):
        source = str(linebreaks(escape(html)))
    spec = _resolve(profile)
    policy = build_policy(spec)
    options: dict[str, Any] = {
        "tags": set(policy.tags),
        "clean_content_tags": DROPPED_WITH_CONTENT - set(policy.tags),
        "attributes": _attributes(policy),
        "attribute_filter": _make_filter(policy),
        "strip_comments": True,
        "link_rel": LINK_REL,
        "url_schemes": URL_SCHEMES,
    }
    if "iframe" in policy.tags:
        options["set_tag_attribute_values"] = {"iframe": IFRAME_FIXED_ATTRIBUTES}
    cleaned = _complete_images(
        _strip_srcless_media(nh3.clean(source, **options))
    ).strip()
    if not _has_content(cleaned):
        return ""
    if paragraphs and "<" not in cleaned:
        return _as_paragraphs(cleaned)
    return cleaned


def video_embed_options(
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
) -> dict[str, Any]:
    """nh3 options that let another sanitiser keep audited video embeds.

    The caller adds ``iframe`` to its tags and ``{"src", "title"}`` to the iframe
    attributes; sources are rewritten to the privacy-friendly players (anything else
    is dropped) and the iframe gets the fixed sandbox/lazy attributes.
    """

    def attribute_filter(tag: str, attribute: str, value: str) -> str | None:
        if tag == "iframe" and attribute == "src":
            return rewrite_video_url(value, providers)
        return value

    return {
        "attribute_filter": attribute_filter,
        "set_tag_attribute_values": {"iframe": IFRAME_FIXED_ATTRIBUTES},
    }


def strip_srcless_embeds(html: str) -> str:
    """Remove ``<img>``/``<iframe>`` elements whose source was rejected."""
    return _strip_srcless_media(html)


def sanitize_stored_html(html: str | None) -> str:
    """Backstop for stored content: the widest policy any profile can produce.

    Used by model ``save()`` and at render time. It never depends on the current
    admin configuration (nor on the database), so content written while a feature
    was on keeps rendering after an administrator switches that feature off; the
    per-context narrowing happens only when a form accepts new input.
    """
    return sanitize_rich_text(html, RENDER_PROFILE)


def render_rich_text(html: str | None) -> SafeString:
    """Sanitise again at render time; legacy plain text becomes paragraphs."""
    if not html:
        return mark_safe("")
    return mark_safe(sanitize_rich_text(html, RENDER_PROFILE, paragraphs=True))
