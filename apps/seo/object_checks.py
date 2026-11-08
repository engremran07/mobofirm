"""Object-aware content checks: what a page's own data says about its quality.

Models are reached through ``django.apps`` so ``apps.seo`` keeps working when an
optional app is not installed; a check for a missing model simply yields nothing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from django.apps import apps as django_apps
from django.db.models import Q

from .policy import clean_text
from .rules import THIN_PAGE_WORDS, THIN_POST_WORDS

ERROR, WARNING, INFO = "error", "warning", "info"
_IMG_WITHOUT_ALT = re.compile(r"<img\b(?![^>]*\balt\s*=)[^>]*>", re.IGNORECASE)
_BODY_H1 = re.compile(r"<h1\b", re.IGNORECASE)
GUIDE_MARKER = "how to flash"
DIGEST_MIN_WORDS = 60


@dataclass(frozen=True)
class Check:
    code: str
    severity: str
    message: str
    measured: str = ""
    expected: str = ""
    key: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContentFacts:
    """Per-object measurements the scan stores in the page snapshot."""

    word_count: int = 0
    digest: str = ""
    checks: list[Check] = field(default_factory=list)


def _model(label: str) -> Any | None:
    try:
        return django_apps.get_model(label)
    except LookupError:
        return None


def _words(html: str) -> int:
    return len(clean_text(html).split())


def _post_checks(obj: Any) -> ContentFacts:
    facts = ContentFacts()
    body = str(getattr(obj, "body", "") or "")
    facts.word_count = _words(body)
    if getattr(obj, "is_live", False) and facts.word_count < THIN_POST_WORDS:
        facts.checks.append(
            Check(
                "post_thin_content",
                WARNING,
                f"The post has {facts.word_count} words.",
                measured=f"{facts.word_count} words",
                expected=f"at least {THIN_POST_WORDS} words",
            )
        )
    missing_alt = len(_IMG_WITHOUT_ALT.findall(body))
    if missing_alt:
        facts.checks.append(
            Check(
                "post_images_missing_alt",
                WARNING,
                f"{missing_alt} image(s) in the post body have no alt attribute.",
                measured=f"{missing_alt} images",
                expected="every image has alt text",
            )
        )
    if _BODY_H1.search(body):
        facts.checks.append(
            Check(
                "post_body_h1",
                WARNING,
                "The post body contains an <h1>; the page title is already the <h1>.",
                expected="<h2> and below in the body",
            )
        )
    if not clean_text(str(getattr(obj, "summary", "") or "")):
        facts.checks.append(
            Check("post_no_summary", INFO, "The post has no summary.", measured="empty")
        )
    if facts.word_count >= DIGEST_MIN_WORDS:
        opening = " ".join(clean_text(body).lower().split()[:60])
        facts.digest = hashlib.sha256(opening.encode()).hexdigest()
    return facts


def _page_checks(obj: Any) -> ContentFacts:
    facts = ContentFacts(word_count=_words(str(getattr(obj, "content", "") or "")))
    if facts.word_count < THIN_PAGE_WORDS:
        facts.checks.append(
            Check(
                "page_thin_content",
                INFO,
                f"The page has {facts.word_count} words.",
                measured=f"{facts.word_count} words",
                expected=f"at least {THIN_PAGE_WORDS} words",
            )
        )
    return facts


def _live_guide_posts(*, model_pk: Any = None, chipset_pk: Any = None) -> bool:
    """Whether a live post carries the flashing guide for a model or a chipset."""
    if _model("blog.Post") is None or (
        model_pk is None and _model("firmwares.AutomatedBlogLink") is None
    ):
        return True
    return find_guide_post(model_pk=model_pk, chipset_pk=chipset_pk) is not None


def find_guide_post(*, model_pk: Any = None, chipset_pk: Any = None) -> Any | None:
    """The live post that carries the flashing guide for a model or chipset."""
    post_model = _model("blog.Post")
    if post_model is None:
        return None
    if model_pk is not None:
        scope = Q(firmware_model_id=model_pk, body__icontains=GUIDE_MARKER) | Q(
            flashing_guide_source__model_id=model_pk
        )
    else:
        link_model = _model("firmwares.AutomatedBlogLink")
        if link_model is None:
            return None
        post_ids = link_model.objects.filter(
            kind="chipset", chipset_id=chipset_pk
        ).values("post_id")
        scope = Q(pk__in=post_ids, body__icontains=GUIDE_MARKER)
    return post_model.objects.live().filter(scope).order_by("-pk").first()


def _model_checks(obj: Any, indexable: bool) -> ContentFacts:
    facts = ContentFacts()
    if not getattr(obj, "chipset_ref_id", None):
        facts.checks.append(
            Check(
                "model_no_chipset",
                INFO,
                "The model is not linked to a chipset record.",
                measured="no chipset",
                expected="a linked chipset",
            )
        )
    if indexable and not _live_guide_posts(model_pk=obj.pk):
        facts.checks.append(
            Check(
                "model_missing_flash_guide",
                WARNING,
                "No live 'How to flash' guide covers this model.",
                expected="a published guide naming the flashing tool",
            )
        )
    return facts


def _chipset_checks(obj: Any, indexable: bool) -> ContentFacts:
    facts = ContentFacts()
    if not indexable:
        return facts
    if not obj.flashing_tools.filter(is_active=True).exists():
        facts.checks.append(
            Check(
                "chipset_no_tool",
                WARNING,
                "No active flashing tool supports this chipset.",
                expected="at least one supported tool",
            )
        )
    if not _live_guide_posts(chipset_pk=obj.pk):
        facts.checks.append(
            Check(
                "chipset_missing_flash_guide",
                WARNING,
                "No live flashing guide covers this chipset.",
                expected="a published guide with tools and firmware",
            )
        )
    return facts


def _tool_checks(obj: Any) -> ContentFacts:
    facts = ContentFacts()
    if not obj.chipsets.exists() and not obj.supported_brands.exists():
        facts.checks.append(
            Check(
                "tool_unlinked",
                WARNING,
                "The tool lists no supported chipsets or brands.",
                expected="chipsets or brands the tool supports",
            )
        )
    return facts


def _brand_checks(obj: Any) -> ContentFacts:
    facts = ContentFacts()
    if not clean_text(str(getattr(obj, "description", "") or "")):
        facts.checks.append(
            Check(
                "brand_no_description",
                INFO,
                "The brand has no description.",
                measured="empty",
            )
        )
    return facts


def content_facts(label: str, obj: Any, *, indexable: bool) -> ContentFacts:
    if label == "blog.post":
        return _post_checks(obj)
    if label == "pages.page":
        return _page_checks(obj)
    if label == "firmwares.model":
        return _model_checks(obj, indexable)
    if label == "firmwares.chipset":
        return _chipset_checks(obj, indexable)
    if label == "firmwares.flashingtool":
        return _tool_checks(obj)
    if label == "firmwares.brand":
        return _brand_checks(obj)
    return ContentFacts()
