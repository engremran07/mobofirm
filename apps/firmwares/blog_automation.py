"""Automated blog posts for firmware brands, models and chipsets.

Each brand, model and chipset owns exactly one post, tracked by an
``AutomatedBlogLink`` row (not by slug), so syncing is idempotent. Posts are
authored by a dedicated system account and published automatically; later firmware
additions, approvals or removals refresh the same post with the current download
listing grouped by every firmware category.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe
from django.utils.text import slugify

from . import catalog
from .models import AutomatedBlogLink, Brand, Chipset, FlashingTool, Model
from .tool_matcher import get_chipset_family, get_primary_flash_tool_name

logger = logging.getLogger(__name__)

MODEL_FILE_CAP = 100
AGGREGATE_FILE_CAP = 25
AGGREGATE_MODEL_CAP = 60

GUIDE_STEPS: dict[str, tuple[str, str, list[str]]] = {
    "MediaTek": (
        "SP Flash Tool",
        "BROM/Preloader mode",
        [
            "Install MediaTek USB VCOM drivers on your PC.",
            "Open SP Flash Tool and load the scatter file from the firmware package.",
            "Choose Download Only (keeps data) or Firmware Upgrade (full wipe).",
            "Power the device off and connect it by USB to enter BROM/Preloader mode.",
            "Click Download and wait for the green confirmation.",
        ],
    ),
    "Qualcomm": (
        "QFIL (Qualcomm Flash Image Loader)",
        "EDL mode (Qualcomm 9008)",
        [
            "Install the Qualcomm HS-USB QDLoader 9008 drivers.",
            "Open QFIL and choose Flat Build.",
            "Browse to rawprogram0.xml and patch0.xml from the firmware package.",
            "Boot the device into EDL mode and connect it by USB.",
            "Click Download and wait for completion.",
        ],
    ),
    "Unisoc": (
        "SPD Research Tool",
        "download mode",
        [
            "Install the Spreadtrum/Unisoc USB drivers.",
            "Open the SPD tool and load the .pac firmware file.",
            "Power the device off, hold Volume Down and connect it by USB.",
            "Start the download and do not disconnect until it completes.",
        ],
    ),
    "Samsung Exynos": (
        "Odin",
        "Download mode",
        [
            "Load the AP, BL, CP and CSC files into their Odin slots.",
            "Boot the device into Download mode and connect it by USB.",
            "Wait for Odin to show Added!! and press Start.",
            "Wait for the PASS! message before disconnecting.",
        ],
    ),
}
GENERIC_STEPS = (
    "the manufacturer's flash tool",
    "download or fastboot mode",
    [
        "Install the USB drivers for your device brand.",
        "Load the firmware files into the flashing tool.",
        "Boot the device into download or fastboot mode and connect it by USB.",
        "Start flashing and wait for completion; the first boot can take several minutes.",
    ],
)


@dataclass
class Draft:
    """Rendered content for one automated post."""

    title: str
    summary: str
    body: str
    slug_base: str
    seo_title: str
    seo_description: str
    category: Any
    brand: Brand | None = None
    model: Model | None = None
    tag_names: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        payload = (
            f"{self.title}\n{self.summary}\n{self.body}\n"
            f"{self.seo_title}\n{self.seo_description}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def get_automation_author():
    """Return the dedicated, non-login account that authors automated posts."""
    user_model = get_user_model()
    username = getattr(
        settings, "FIRMWARE_BLOG_AUTOMATION_USERNAME", "mobofirm-automation"
    )
    author = user_model.objects.filter(username=username).first()
    if author is None:
        author = user_model.objects.create_user(
            email=f"{username}@automation.invalid", username=username
        )
    return author


def _category_model():
    return apps.get_model("blog", "Category")


def _get_or_create_category(slug: str, name: str, parent=None):
    """Find a category by slug or name; never renames categories it did not create."""
    Category = _category_model()
    category = (
        Category.objects.filter(slug=slug).first()
        or Category.objects.filter(name=name).first()
    )
    if category is not None:
        return category
    try:
        with transaction.atomic():
            return Category.objects.create(slug=slug, name=name, parent=parent)
    except IntegrityError:
        return Category.objects.get(slug=slug)


def _firmware_url(name: str, firmware: Any) -> str:
    return reverse(
        f"firmwares:{name}",
        kwargs={
            "firmware_type": firmware.firmware_type,
            "firmware_id": str(firmware.pk),
        },
    )


def _firmware_sections(
    firmwares: list, *, cap: int, browse_query: str = ""
) -> SafeString:
    """HTML listing of public firmware grouped by category with download links."""
    groups = catalog.group_by_category(firmwares)
    if not groups:
        return mark_safe("<p>No approved firmware files are listed yet.</p>")
    parts: list[SafeString] = []
    for key, label, items in groups:
        shown = items[:cap]
        rows = format_html_join(
            "",
            '<tr><td><a href="{}">{}</a></td><td>{}</td><td>{}</td><td>{}</td>'
            '<td>{}</td><td>{}</td><td><a href="{}">{}</a></td></tr>',
            (
                (
                    _firmware_url("firmware_detail", fw),
                    fw.original_file_name,
                    fw.variant.name if fw.variant_id else "—",
                    fw.build_number or "—",
                    fw.android_version or "—",
                    fw.get_package_format_display(),
                    catalog.format_file_size(fw.file_size),
                    _firmware_url("firmware_download", fw),
                    "Download (external host)" if fw.is_external else "Download",
                )
                for fw in shown
            ),
        )
        more = ""
        if len(items) > len(shown):
            more = format_html(
                '<p><a href="{}?type={}{}">View all {} {} files</a></p>',
                reverse("firmwares:browse"),
                key,
                browse_query,
                len(items),
                label.lower(),
            )
        parts.append(
            format_html(
                "<h3>{} firmware ({})</h3>"
                '<table class="table"><thead><tr><th>File</th><th>Variant</th>'
                "<th>Build</th><th>Android</th><th>Package</th><th>Size</th>"
                "<th>Download</th></tr></thead><tbody>{}</tbody></table>{}",
                label,
                len(items),
                rows,
                more,
            )
        )
    return format_html_join("", "{}", ((part,) for part in parts))


def _tools_section(brand: Brand | None, chipsets: list[Chipset]) -> SafeString:
    condition = Q()
    if brand is not None:
        condition |= Q(supported_brands=brand)
    if chipsets:
        condition |= Q(chipsets__in=chipsets)
    if not condition:
        return mark_safe("")
    tools = list(
        FlashingTool.objects.filter(condition, is_active=True)
        .select_related("category")
        .distinct()
        .order_by("-is_featured", "name")[:12]
    )
    if not tools:
        return mark_safe("")
    items = format_html_join(
        "",
        '<li><a href="{}">{}</a> — {}, {}</li>',
        (
            (
                reverse("firmwares:flashing_tool_detail", kwargs={"slug": tool.slug}),
                tool.name,
                tool.get_platform_display(),
                tool.get_risk_level_display(),
            )
            for tool in tools
        ),
    )
    return format_html(
        '<h3>Recommended flashing tools</h3><ul>{}</ul><p><a href="{}">Browse all tools and guides</a></p>',
        items,
        reverse("firmwares:flashing_tools"),
    )


def _guide_section(device: str, chipset_text: str, brand_name: str) -> SafeString:
    family = get_chipset_family(chipset_text)
    tool, mode, steps = GUIDE_STEPS.get(family, GENERIC_STEPS)
    if family == "" and brand_name.lower() == "samsung":
        tool, mode, steps = GUIDE_STEPS["Samsung Exynos"]
    primary = get_primary_flash_tool_name(brand_name, chipset_text) or tool
    steps_html = format_html_join("", "<li>{}</li>", ((step,) for step in steps))
    return format_html(
        "<h3>How to flash firmware on {}</h3>"
        "<p>The usual tool for this device is <strong>{}</strong> in {}.</p>"
        "<ol>{}</ol>"
        "<p>Prerequisites: a battery above 50%, the original USB cable, installed "
        "USB drivers and a full backup of your data.</p>",
        device,
        primary,
        mode,
        steps_html,
    )


SAFETY_NOTE = mark_safe(
    "<h3>Important</h3><ul>"
    "<li>Back up your data before flashing firmware.</li>"
    "<li>Verify that your device variant and region match the firmware.</li>"
    "<li>Check chipset compatibility before flashing.</li>"
    "<li>Password-protected archives need the extraction password from the uploader.</li>"
    "</ul>"
)


def _model_chipsets(model: Model) -> list[Chipset]:
    chipsets: dict[int, Chipset] = {}
    if model.chipset_ref is not None:
        chipsets[model.chipset_ref.pk] = model.chipset_ref
    for variant in model.variants.select_related("chipset_ref"):
        if variant.chipset_ref is not None:
            chipsets[variant.chipset_ref.pk] = variant.chipset_ref
    return list(chipsets.values())


class FirmwareBlogService:
    """Builds and synchronises the single automated post per brand/model/chipset."""

    # -- categories -------------------------------------------------------

    @classmethod
    def ensure_brand_category(cls, brand: Brand):
        if not apps.is_installed("apps.blog"):
            return None
        return _get_or_create_category(brand.slug, brand.name)

    @classmethod
    def ensure_model_category(cls, model: Model):
        if not apps.is_installed("apps.blog"):
            return None
        parent = cls.ensure_brand_category(model.brand)
        name = model.name
        if model.marketing_name and model.marketing_name != model.name:
            marketing = model.marketing_name.removeprefix(f"{model.brand.name} ")
            if marketing != model.name:
                name = f"{model.name} ({marketing})"
        return _get_or_create_category(
            slugify(f"{model.brand.slug}-{model.slug}"), name, parent
        )

    @classmethod
    def ensure_chipset_category(cls):
        return _get_or_create_category("chipsets", "Chipsets")

    # -- drafts -----------------------------------------------------------

    @classmethod
    def _model_draft(cls, model: Model) -> Draft:
        brand = model.brand
        device = f"{brand.name} {model.name}"
        marketing = (
            model.marketing_name
            if model.marketing_name not in {model.name, device}
            else ""
        )
        if marketing:
            device = f"{device} ({marketing})"
        chipsets = _model_chipsets(model)
        firmwares = catalog.get_all_firmwares_for_model(model)

        alt_names = [
            value
            for value in (
                marketing,
                model.model_code,
            )
            if value
        ]
        spec_rows = [
            ("Model code", model.all_model_codes_display),
            ("Codename", model.codename),
            ("Launch OS", model.os_version),
            ("Display", model.display_size),
            ("Battery", model.battery),
            (
                "Released",
                model.release_date.strftime("%B %Y") if model.release_date else "",
            ),
        ]
        spec_html = format_html_join(
            "",
            '<tr><th scope="row">{}</th><td>{}</td></tr>',
            ((label, value) for label, value in spec_rows if value),
        )
        chipset_html = mark_safe("")
        if chipsets:
            chipset_html = format_html(
                '<tr><th scope="row">Chipset</th><td>{}</td></tr>',
                format_html_join(
                    ", ",
                    '<a href="{}">{}</a>',
                    (
                        (
                            reverse(
                                "firmwares:chipset_detail",
                                kwargs={"slug": chipset.slug},
                            ),
                            chipset.display_name,
                        )
                        for chipset in chipsets
                    ),
                ),
            )
        chipset_text = chipsets[0].display_name if chipsets else model.chipset
        body = format_html(
            "<h2>Download {} firmware — all variants</h2>"
            "<p>Approved firmware files for <strong>{}</strong>{}. "
            "This list updates automatically when firmware is added, approved or removed.</p>"
            '<h3>Device details</h3><table class="table"><tbody>{}{}</tbody></table>'
            "<h3>Firmware downloads by category</h3>{}{}{}{}",
            device,
            device,
            f" (also known as {', '.join(alt_names)})" if alt_names else "",
            spec_html,
            chipset_html,
            _firmware_sections(firmwares, cap=MODEL_FILE_CAP),
            _tools_section(brand, chipsets),
            _guide_section(device, chipset_text, brand.name),
            SAFETY_NOTE,
        )
        title = f"{device} Firmware Download — All Variants"
        seo_title = f"{brand.name} {model.name} Firmware Download"
        if len(seo_title) > 60:
            seo_title = f"{brand.name} {model.name} Firmware"[:60]
        tag_names = [brand.name, f"{brand.name} {model.name}"]
        tag_names += [
            f"{label} Firmware"
            for _k, label, _i in catalog.group_by_category(firmwares)[:3]
        ]
        tag_names += [chipset.display_name for chipset in chipsets[:3]]
        tag_names += ["Firmware", "Download"]
        return Draft(
            title=title[:200],
            summary=f"Download {device} firmware for all variants."[:1000],
            body=str(body),
            slug_base=slugify(f"{brand.slug}-{model.slug}-firmware-download"),
            seo_title=seo_title,
            seo_description=f"Download {brand.name} {model.name} firmware files for all variants."[
                :160
            ],
            category=cls.ensure_model_category(model),
            brand=brand,
            model=model,
            tag_names=tuple(tag_names),
        )

    @classmethod
    def _brand_draft(cls, brand: Brand) -> Draft:
        firmwares = catalog.get_public_firmwares(brand=brand)
        models = list(
            Model.objects.filter(brand=brand, is_active=True).order_by("name")[
                :AGGREGATE_MODEL_CAP
            ]
        )
        chipsets = list(
            Chipset.objects.filter(models__brand=brand)
            .distinct()
            .order_by("vendor", "name")[:20]
        )
        model_list = format_html_join(
            "",
            '<li><a href="{}">{}</a></li>',
            (
                (
                    reverse(
                        "firmwares:model_detail",
                        kwargs={"brand_slug": brand.slug, "model_slug": model.slug},
                    ),
                    model.display_name,
                )
                for model in models
            ),
        )
        description = (
            format_html("<p>{}</p>", brand.description.strip())
            if brand.description.strip()
            else ""
        )
        body = format_html(
            "<h2>{} device and firmware catalog</h2>{}"
            '<p><a href="{}">Browse the {} device catalog</a>.</p>'
            "<h3>Device models</h3><ul>{}</ul>"
            "<h3>Firmware downloads by category</h3>{}{}",
            brand.name,
            description,
            reverse("firmwares:brand_detail", kwargs={"slug": brand.slug}),
            brand.name,
            model_list,
            _firmware_sections(
                firmwares, cap=AGGREGATE_FILE_CAP, browse_query=f"&brand={brand.slug}"
            ),
            _tools_section(brand, chipsets),
        )
        title = f"{brand.name} device and firmware catalog"
        summary = (
            f"Explore the Mobofirm device and firmware catalog for {brand.name}, "
            "including supported models and approved firmware."
        )
        return Draft(
            title=title[:200],
            summary=summary,
            body=str(body),
            slug_base=slugify(f"{brand.slug}-device-firmware-catalog"),
            seo_title=title[:240],
            seo_description=summary[:160],
            category=cls.ensure_brand_category(brand),
            brand=brand,
            tag_names=(brand.name, "Firmware"),
        )

    @classmethod
    def _chipset_draft(cls, chipset: Chipset) -> Draft:
        firmwares = catalog.get_public_firmwares(catalog.chipset_filter(chipset))
        models = list(
            Model.objects.filter(
                Q(chipset_ref=chipset) | Q(variants__chipset_ref=chipset),
                is_active=True,
            )
            .select_related("brand")
            .distinct()
            .order_by("brand__name", "name")[:AGGREGATE_MODEL_CAP]
        )
        model_list = format_html_join(
            "",
            '<li><a href="{}">{} {}</a></li>',
            (
                (
                    reverse(
                        "firmwares:model_detail",
                        kwargs={
                            "brand_slug": model.brand.slug,
                            "model_slug": model.slug,
                        },
                    ),
                    model.brand.name,
                    model.display_name,
                )
                for model in models
            ),
        )
        description = (
            format_html("<p>{}</p>", chipset.description.strip())
            if chipset.description.strip()
            else ""
        )
        body = format_html(
            "<h2>{} firmware, tools and devices</h2>{}"
            '<p><a href="{}">Open the {} chipset page</a>.</p>'
            "<h3>Devices using this chipset</h3><ul>{}</ul>"
            "<h3>Firmware downloads by category</h3>{}{}{}",
            chipset.display_name,
            description,
            reverse("firmwares:chipset_detail", kwargs={"slug": chipset.slug}),
            chipset.display_name,
            model_list,
            _firmware_sections(
                firmwares,
                cap=AGGREGATE_FILE_CAP,
                browse_query=f"&chipset={chipset.slug}",
            ),
            _tools_section(None, [chipset]),
            _guide_section(chipset.display_name, chipset.display_name, ""),
        )
        title = f"{chipset.display_name} firmware downloads and flashing tools"
        summary = (
            f"Firmware, flashing tools and supported devices for the "
            f"{chipset.display_name} chipset."
        )
        return Draft(
            title=title[:200],
            summary=summary,
            body=str(body),
            slug_base=slugify(f"{chipset.slug}-chipset-firmware"),
            seo_title=title[:60],
            seo_description=summary[:160],
            category=cls.ensure_chipset_category(),
            tag_names=(chipset.display_name, "Firmware"),
        )

    # -- synchronisation --------------------------------------------------

    @classmethod
    def _unique_slug(cls, Post, base: str) -> str:
        slug = base[:230] or "firmware"
        candidate, index = slug, 2
        while Post.objects.filter(slug=candidate).exists():
            candidate = f"{slug[:225]}-{index}"
            index += 1
        return candidate

    @classmethod
    def _apply(cls, post, draft: Draft) -> None:
        post.title = draft.title
        post.summary = draft.summary
        post.body = draft.body
        post.seo_title = draft.seo_title
        post.seo_description = draft.seo_description
        post.category = draft.category
        post.firmware_brand = draft.brand
        post.firmware_model = draft.model
        post.is_ai_generated = True

    @classmethod
    def _sync(cls, kind: str, target, draft: Draft):
        Post = apps.get_model("blog", "Post")
        from apps.blog.models import PostStatus as Status

        lookup = {"kind": kind, kind: target}
        digest = draft.digest
        now = timezone.now()
        link = AutomatedBlogLink.objects.select_related("post").filter(**lookup).first()
        if link is not None:
            if link.content_hash != digest:
                cls._apply(link.post, draft)
                link.post.save()
                link.content_hash = digest
                link.last_synced_at = now
                link.save(
                    update_fields=["content_hash", "last_synced_at", "updated_at"]
                )
                cls._update_post_tags(link.post, draft.tag_names)
            return link.post
        try:
            with transaction.atomic():
                post = Post(
                    slug=cls._unique_slug(Post, draft.slug_base),
                    author=get_automation_author(),
                    status=Status.PUBLISHED,
                    publish_at=now,
                    published_at=now,
                    allow_comments=True,
                )
                cls._apply(post, draft)
                post.save()
                AutomatedBlogLink.objects.create(
                    post=post,
                    content_hash=digest,
                    last_synced_at=now,
                    **lookup,
                )
        except IntegrityError:
            link = (
                AutomatedBlogLink.objects.select_related("post")
                .filter(**lookup)
                .first()
            )
            if link is None:
                raise
            return link.post
        cls._update_post_tags(post, draft.tag_names)
        logger.info("Created automated %s blog post for %s", kind, target)
        return post

    @classmethod
    def sync_model_post(cls, model: Model):
        if not apps.is_installed("apps.blog"):
            return None
        model = Model.objects.select_related("brand", "chipset_ref").get(pk=model.pk)
        return cls._sync("model", model, cls._model_draft(model))

    @classmethod
    def sync_brand_post(cls, brand: Brand):
        if not apps.is_installed("apps.blog"):
            return None
        return cls._sync("brand", brand, cls._brand_draft(brand))

    @classmethod
    def sync_chipset_post(cls, chipset: Chipset):
        if not apps.is_installed("apps.blog"):
            return None
        return cls._sync("chipset", chipset, cls._chipset_draft(chipset))

    @classmethod
    def sync_for_firmware(cls, firmware) -> list:
        """Refresh the model, brand and chipset posts a firmware record feeds."""
        posts = []
        if firmware.model_id:
            posts.append(cls.sync_model_post(firmware.model))
        brand = firmware.brand or (firmware.model.brand if firmware.model_id else None)
        if brand is not None:
            posts.append(cls.sync_brand_post(brand))
        chipset_ids = set()
        for owner in (firmware.model if firmware.model_id else None, firmware.variant):
            if owner is not None and owner.chipset_ref_id:
                chipset_ids.add(owner.chipset_ref_id)
        for chipset in Chipset.objects.filter(pk__in=chipset_ids):
            posts.append(cls.sync_chipset_post(chipset))
        return [post for post in posts if post is not None]

    @classmethod
    def sync_all(cls, *, brand_slug: str | None = None) -> dict[str, int]:
        """Catch-up: ensure every brand, model and chipset has a current post."""
        counts = {"brands": 0, "models": 0, "chipsets": 0, "errors": 0}
        brands = Brand.objects.all()
        if brand_slug:
            brands = brands.filter(slug=brand_slug)
        for brand in brands:
            cls._safe(cls.sync_brand_post, brand, counts, "brands")
            for model in Model.objects.filter(brand=brand):
                cls._safe(cls.sync_model_post, model, counts, "models")
        chipsets = Chipset.objects.all()
        if brand_slug:
            chipsets = chipsets.filter(models__brand__slug=brand_slug).distinct()
        for chipset in chipsets:
            cls._safe(cls.sync_chipset_post, chipset, counts, "chipsets")
        return counts

    @classmethod
    def _safe(cls, func, target, counts: dict[str, int], key: str) -> None:
        try:
            func(target)
        except (DatabaseError, ValidationError, ValueError):
            logger.exception("Blog sync failed for %s", target)
            counts["errors"] += 1
        else:
            counts[key] += 1

    # -- backwards-compatible entry points ---------------------------------

    @classmethod
    def generate_firmware_post(cls, model: Model):
        return cls.sync_model_post(model)

    @classmethod
    def generate_brand_post(cls, brand: Brand):
        return cls.sync_brand_post(brand)

    # -- tags -------------------------------------------------------------

    @classmethod
    def _update_post_tags(cls, post, tag_names: tuple[str, ...]) -> None:
        """Create curated tags for the catalog names and make them the post's tags."""
        if not apps.is_installed("apps.tags"):
            return
        from apps.tags.services import assign_catalog_tags

        assign_catalog_tags(post, tag_names)
