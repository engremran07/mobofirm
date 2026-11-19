"""Fields the SEO screens may edit on a content object, and how they are written.

The SEO app edits two kinds of things:

* **Overrides** (``seo.Metadata``): title, description, canonical, robots, noindex and
  social fields. They live in this app's table and are applied by the resolver.
* **Object fields** declared here (slug, summary, description, and the images/headings
  inside an HTML body field). Models are reached with ``django.apps`` by label, never
  imported, and only the fields listed below are ever written. Every write goes
  through the model's own ``save()`` (so its slug history, signals and the SEO content
  observers run) and is recorded in the ``ChangeLedger`` so it can be rolled back.

An app can declare more with :func:`register_object_fields` from its ``seo_providers``
module; fields that do not exist on the installed model are ignored.
"""

from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass
from typing import Any

from django.apps import apps as django_apps
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models

from .contentsources import content_sources
from .managers import default_manager
from .policy import is_route_ref
from .rules import SLUG_MAX_LENGTH

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ALT = re.compile(r"\balt\s*=", re.IGNORECASE)
_SRC = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_H1_OPEN = re.compile(r"<h1\b", re.IGNORECASE)
_H1_CLOSE = re.compile(r"</h1\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    kind: str = "text"  # text | textarea | slug
    max_length: int = 0
    help_text: str = ""


SLUG_SPEC = FieldSpec(
    "slug",
    "URL slug",
    "slug",
    SLUG_MAX_LENGTH,
    "Lowercase words separated by single hyphens. The old URL redirects to the new one.",
)
SUMMARY_SPEC = FieldSpec("summary", "Summary", "textarea", 300)
DESCRIPTION_SPEC = FieldSpec("description", "Description", "textarea", 600)

_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "blog.post": (SLUG_SPEC, SUMMARY_SPEC),
    "pages.page": (SLUG_SPEC,),
    "tags.tag": (SLUG_SPEC,),
    "firmwares.brand": (SLUG_SPEC, DESCRIPTION_SPEC),
    "firmwares.model": (SLUG_SPEC, DESCRIPTION_SPEC),
    "firmwares.chipset": (SLUG_SPEC,),
    "firmwares.flashingtool": (SLUG_SPEC, DESCRIPTION_SPEC),
}
# Label -> name of the HTML body field whose images and headings can be repaired.
_HTML_FIELDS: dict[str, str] = {"blog.post": "body", "pages.page": "content"}


def register_object_fields(label: str, *specs: FieldSpec, html_field: str = "") -> None:
    """Declare editable fields (and optionally the HTML body field) for a model."""
    key = label.lower()
    merged = {spec.name: spec for spec in _SPECS.get(key, ())}
    merged.update({spec.name: spec for spec in specs})
    _SPECS[key] = tuple(merged.values())
    if html_field:
        _HTML_FIELDS[key] = html_field


def _model(label: str) -> type[models.Model] | None:
    try:
        return django_apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _has_field(model: type[models.Model], name: str) -> bool:
    try:
        model._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return True


def specs_for(label: str) -> list[FieldSpec]:
    """The declared fields that exist on the installed model.

    A slug is only editable where the SEO content observers watch the model: that is
    the machinery that records the moved URL and proposes the redirect from the old
    slug. A model nobody registered there keeps its slug read-only.
    """
    model = _model(label)
    if model is None:
        return []
    observed = label.lower() in content_sources()
    return [
        spec
        for spec in _SPECS.get(label.lower(), ())
        if _has_field(model, spec.name) and (spec.kind != "slug" or observed)
    ]


def editable_labels() -> frozenset[str]:
    """Model labels the ledger may change through the SEO object editors."""
    return frozenset(label for label in _SPECS if _model(label) is not None)


def html_field_for(label: str) -> str:
    model = _model(label)
    name = _HTML_FIELDS.get(label.lower(), "")
    return name if model is not None and name and _has_field(model, name) else ""


def load(label: str, pk: str) -> models.Model | None:
    if is_route_ref(label, str(pk)):
        return None
    model = _model(label)
    if model is None:
        return None
    return default_manager(model).filter(pk=pk).first()


def read(obj: models.Model, name: str) -> str:
    return str(getattr(obj, name, "") or "")


def validate_object_value(obj: models.Model, spec: FieldSpec, value: str) -> list[str]:
    """Reasons ``value`` cannot be stored in ``spec`` of ``obj`` (empty when valid)."""
    text = value.strip()
    if spec.kind == "slug":
        if not SLUG.match(text):
            return ["The slug must be lowercase words separated by single hyphens."]
        if len(text) > SLUG_MAX_LENGTH:
            return [f"The slug can have at most {SLUG_MAX_LENGTH} characters."]
    elif spec.max_length and len(text) > spec.max_length:
        return [f"{spec.label} can have at most {spec.max_length} characters."]
    model = type(obj)
    field = model._meta.get_field(spec.name)
    candidate = copy.copy(obj)
    setattr(candidate, spec.name, text)
    try:
        field.run_validators(text)
        candidate.validate_unique()
    except ValidationError as error:
        return [message for message in error.messages]
    return []


# --- HTML body repairs -----------------------------------------------------------


@dataclass(frozen=True)
class BodyImage:
    index: int  # position among all <img> tags of the body
    src: str
    alt: str | None  # ``None`` when the attribute is missing


def body_images(markup: str) -> list[BodyImage]:
    found: list[BodyImage] = []
    for index, match in enumerate(_IMG.finditer(markup)):
        tag = match.group(0)
        src = _SRC.search(tag)
        alt = re.search(r"""\balt\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag, re.IGNORECASE)
        found.append(
            BodyImage(
                index,
                (src.group(1) or src.group(2) or "") if src else "",
                None
                if not _ALT.search(tag)
                else html.unescape((alt.group(1) or alt.group(2) or "") if alt else ""),
            )
        )
    return found


def set_image_alts(markup: str, alts: dict[int, str]) -> str:
    """Write ``alt`` text into the ``<img>`` tags at the given indexes."""
    counter = {"i": -1}

    def substitute(match: re.Match[str]) -> str:
        counter["i"] += 1
        text = alts.get(counter["i"])
        if text is None:
            return match.group(0)
        tag = re.sub(
            r"""\s+alt\s*=\s*(?:"[^"]*"|'[^']*')""",
            "",
            match.group(0),
            flags=re.IGNORECASE,
        ).rstrip()
        self_closing = tag.endswith("/>")
        stem = tag[: -2 if self_closing else -1].rstrip()
        escaped = html.escape(text, quote=True)
        return f'{stem} alt="{escaped}"{" /" if self_closing else ""}>'

    return _IMG.sub(substitute, markup)


def body_h1_count(markup: str) -> int:
    return len(_H1_OPEN.findall(markup))


def demote_h1(markup: str) -> str:
    """Turn body ``<h1>`` headings into ``<h2>`` (the page title is the only h1)."""
    return _H1_CLOSE.sub("</h2>", _H1_OPEN.sub("<h2", markup))


def current_values(label: str, obj: models.Model) -> dict[str, Any]:
    return {spec.name: read(obj, spec.name) for spec in specs_for(label)}
