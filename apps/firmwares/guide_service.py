"""
Flashing guide auto-generation service.

Generates blog posts from FlashingGuideTemplate + Brand/Model combinations.
Uses templates with placeholder substitution.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import markdown
from django.db import transaction
from django.utils.text import slugify

from .models import (
    Brand,
    FlashingGuideTemplate,
    FlashingTool,
    GeneratedFlashingGuide,
    Model,
)
from .package_formats import APPLE_BRAND_SLUGS
from .tool_matcher import filter_tools_for_brand, get_compatible_tools_from_db

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)


def _get_recommended_tools(
    template: FlashingGuideTemplate,
    brand: Brand,
    chipset: str = "",
) -> list[FlashingTool]:
    """Get tools relevant to a brand/chipset, with chipset+brand validation.

    1. Takes template's M2M tools and filters out incompatible ones
    2. Falls back to DB query filtered by chipset family + brand
    """
    brand_name = brand.name if brand else ""

    # Try template-assigned tools first, but validate them
    raw_tools = list(template.recommended_tools.filter(is_active=True))
    if raw_tools:
        tools = filter_tools_for_brand(raw_tools, brand_name, chipset)
        if tools:
            return tools

    # Fallback: query DB with smart chipset+brand filtering
    return get_compatible_tools_from_db(brand_name, chipset)


def _format_tool_list(tools: list[FlashingTool]) -> str:
    """Format tools as Markdown bullet list."""
    if not tools:
        return "- Standard flashing tools for your device"
    lines: list[str] = []
    for tool in tools:
        risk = (
            f" ⚠️ {tool.get_risk_level_display()}" if tool.risk_level != "safe" else ""
        )
        platform = f" ({tool.get_platform_display()})"
        lines.append(f"- **{tool.name}**{platform}{risk}")
        if tool.description:
            lines.append(f"  {tool.description[:150]}")
    return "\n".join(lines)


APPLE_GUIDE_TYPES = frozenset(
    {"stock_flash", "recovery", "downgrade", "repair", "general"}
)

APPLE_STEPS: dict[str, list[str]] = {
    "stock_flash": [
        "1. **Back up** the device: a restore erases everything on it",
        (
            "2. **Update** Finder (Mac) or install the Apple Devices app (Windows 10 and 11); "
            "older Windows can use iTunes"
        ),
        "3. **Connect** the device with a good USB cable and unlock it, then tap **Trust**",
        "4. **Select** the device in the Finder sidebar or the Apple Devices app",
        (
            "5. **Choose** Restore iPhone / iPad to install the newest signed version, "
            "or Update to keep your data"
        ),
        (
            "6. **Or load an IPSW:** Option-click Restore (Mac) or Shift-click Restore "
            "(Windows), then pick an IPSW that Apple still signs for your exact model"
        ),
        "7. **Wait** until the device restarts; do not disconnect it",
        "8. **Set up** the device and sign in with the Apple Account it already uses",
    ],
    "recovery": [
        (
            "1. **Connect** the device to the computer and open Finder or the Apple "
            "Devices app"
        ),
        (
            "2. **Enter recovery mode:** press and release Volume Up, press and release "
            "Volume Down, then hold the side button until the recovery screen shows "
            "(iPhone 8 and later, iPads without a Home button)"
        ),
        (
            "3. **Older devices:** hold Home + Sleep/Wake (iPhone 7: Volume Down + "
            "Sleep/Wake) until the recovery screen shows"
        ),
        "4. **Choose** Update first; it keeps your data when it succeeds",
        "5. **Choose** Restore only if Update fails; it erases the device",
        "6. **Wait** for the download and install to finish",
    ],
    "repair": [
        "1. **Try** recovery mode first (see our recovery mode guide)",
        (
            "2. **Enter DFU mode** only if recovery mode fails. The key sequence depends "
            "on the generation: iPhone 8 and later and iPads without a Home button use "
            "the Volume Up, Volume Down, side button sequence; iPhone 7 uses Volume "
            "Down + side button; iPhone 6s and earlier use Home + side button"
        ),
        (
            "3. **Check** that the screen stays black; Finder or the Apple Devices app "
            "reports a device in recovery mode"
        ),
        "4. **Restore** the device; this erases it and installs a signed version",
        (
            "5. **Revive instead of erase:** on a Mac, Apple Configurator 2 can *Revive* "
            "a device that does not start, which keeps your data when possible, or "
            "*Restore* it with an IPSW"
        ),
        "6. **If** the device still fails, contact Apple or an authorised service provider",
    ],
    "downgrade": [
        (
            "1. **Check** which versions Apple still signs for your exact model; only "
            "signed IPSW files install"
        ),
        (
            "2. **Back up** first. Data from a newer iOS or iPadOS may not restore to "
            "an older one"
        ),
        "3. **Use** Finder or the Apple Devices app with an IPSW that Apple signs",
        (
            "4. **Do not** expect a downgrade to an unsigned version: the restore is "
            "refused by Apple's servers, and tools such as futurerestore need "
            "valid SHSH blobs saved earlier for that exact version"
        ),
        "5. **Keep** the device signed in with its own Apple Account throughout",
    ],
    "general": [
        (
            "1. **OTA update:** Settings > General > Software Update downloads only the "
            "changes, keeps your data, and needs a Wi-Fi connection and free space"
        ),
        (
            "2. **IPSW restore:** Finder or the Apple Devices app installs a complete "
            "firmware image from your computer; use it when the device cannot update, "
            "or to reinstall a clean copy of the signed version"
        ),
        (
            "3. **Choose** OTA for everyday updates and IPSW for recovery, a clean "
            "install or a computer-based update"
        ),
        (
            "4. **Remember** that Apple controls which versions are signed; older IPSW "
            "files stop installing once Apple stops signing them"
        ),
    ],
}


def build_standard_steps(
    guide_type: str,
    brand_name: str,
    model_name: str = "",
) -> str:
    """Build standard step-by-step instructions based on guide type."""
    device = f"{brand_name} {model_name}".strip()
    if brand_name.lower() in APPLE_BRAND_SLUGS:
        return "\n".join(APPLE_STEPS.get(guide_type, APPLE_STEPS["stock_flash"]))

    step_templates: dict[str, list[str]] = {
        "stock_flash": [
            f"1. **Download** the correct stock firmware for your {device}",
            "2. **Backup** all important data (contacts, photos, apps)",
            "3. **Charge** your device to at least 50% battery",
            "4. **Enable** USB Debugging in Developer Options",
            "5. **Install** the required flashing tool on your PC",
            "6. **Connect** your device via USB cable",
            "7. **Load** the firmware file in the flashing tool",
            "8. **Start** the flashing process and wait for completion",
            "9. **Reboot** your device — first boot may take several minutes",
            "10. **Verify** the firmware version in Settings > About Phone",
        ],
        "custom_rom": [
            "1. **Unlock** the bootloader (see our bootloader unlock guide)",
            "2. **Install** a custom recovery (TWRP recommended)",
            "3. **Download** the custom ROM and GApps package",
            "4. **Backup** your current ROM in recovery",
            "5. **Wipe** data, cache, and dalvik cache in recovery",
            "6. **Flash** the custom ROM zip file",
            "7. **Flash** the GApps package (if needed)",
            "8. **Reboot** and wait for first boot setup",
        ],
        "unlock_bootloader": [
            "1. **Enable** Developer Options (tap Build Number 7 times)",
            "2. **Enable** OEM Unlocking in Developer Options",
            "3. **Enable** USB Debugging",
            "4. **Connect** device to PC and open terminal/command prompt",
            "5. **Boot** into fastboot mode: `adb reboot bootloader`",
            "6. **Run** `fastboot oem unlock` or `fastboot flashing unlock`",
            "7. **Confirm** the unlock on your device screen",
            "8. **Reboot** — device will factory reset automatically",
        ],
        "root": [
            "1. **Unlock** the bootloader first (required for most methods)",
            "2. **Download** Magisk APK (latest stable version)",
            "3. **Extract** boot.img from your current firmware",
            "4. **Patch** boot.img using Magisk app on your phone",
            "5. **Flash** the patched boot.img via fastboot",
            "6. **Reboot** and verify root with a root checker app",
        ],
        "recovery": [
            "1. **Unlock** the bootloader (if locked)",
            "2. **Download** TWRP or other custom recovery for your device",
            "3. **Boot** into fastboot mode",
            "4. **Flash** recovery: `fastboot flash recovery recovery.img`",
            "5. **Boot** into recovery to verify installation",
        ],
        "frp_bypass": [
            "1. **Connect** to Wi-Fi during setup",
            "2. **Navigate** to accessibility settings via the setup wizard",
            "3. **Open** a browser or settings app through accessibility shortcuts",
            "4. **Download** and install an FRP bypass tool",
            "5. **Follow** the tool instructions to remove the Google account lock",
            "6. **Restart** the device and complete setup normally",
        ],
        "downgrade": [
            "1. **Find** the exact older firmware version you need",
            "2. **Backup** all data — downgrading will factory reset",
            "3. **Disable** security features (Find My Device, etc.)",
            "4. **Flash** the older firmware using the appropriate tool",
            "5. **Do NOT** update automatically — disable auto-updates",
        ],
        "repair": [
            "1. **Do NOT panic** — most bricks are recoverable",
            "2. **Try** force restart (hold Power + Volume Down for 15s)",
            "3. **Try** booting into recovery or download mode",
            "4. **Connect** to PC and use emergency flashing tools",
            "5. **Flash** a known-good firmware for your exact model",
            "6. **If** hardware brick: consider professional service",
        ],
    }

    return "\n".join(step_templates.get(guide_type, step_templates["stock_flash"]))


def _render_template(
    template_str: str,
    brand_name: str,
    model_name: str = "",
    chipset: str = "",
    guide_type_display: str = "",
    tools_text: str = "",
    steps_text: str = "",
) -> str:
    """Render a template string with placeholders."""
    return (
        template_str.replace("{brand}", brand_name)
        .replace("{model}", model_name or "devices")
        .replace("{chipset}", chipset or "")
        .replace("{guide_type}", guide_type_display)
        .replace("{tools}", tools_text)
        .replace("{steps}", steps_text)
    )


@transaction.atomic
def generate_guide_for_brand_model(
    template: FlashingGuideTemplate,
    brand: Brand,
    model_obj: Model | None = None,
) -> GeneratedFlashingGuide | None:
    """
    Generate a single blog post from a guide template for a brand/model.
    Returns the GeneratedFlashingGuide record, or None if already exists.
    """
    from apps.blog.models import Post, PostSource, PostStatus

    if brand.slug in APPLE_BRAND_SLUGS and template.guide_type not in APPLE_GUIDE_TYPES:
        # Apple devices are restored with Apple's own tools; bootloader, root, custom
        # ROM and lock-bypass guides do not apply (and Activation Lock is never bypassed).
        logger.info(
            "flashing_guide.skipped_for_apple",
            extra={"template": template.pk, "guide_type": template.guide_type},
        )
        return None
    template = FlashingGuideTemplate.objects.select_for_update().get(pk=template.pk)
    existing = GeneratedFlashingGuide.objects.filter(
        template=template,
        brand=brand,
        model=model_obj,
    ).first()
    if existing:
        return None

    from .blog_automation import get_automation_author

    author = get_automation_author()

    brand_name = brand.name
    model_name = model_obj.name if model_obj else ""
    chipset = (model_obj.chipset if model_obj else template.chipset_filter) or ""
    guide_type_display = template.get_guide_type_display()

    # Build content
    tools = _get_recommended_tools(template, brand, chipset)
    tools_text = _format_tool_list(tools)
    steps_text = build_standard_steps(template.guide_type, brand_name, model_name)

    title = _render_template(
        template.title_template,
        brand_name,
        model_name,
        chipset,
        guide_type_display,
    )
    body = _render_template(
        template.body_template,
        brand_name,
        model_name,
        chipset,
        guide_type_display,
        tools_text,
        steps_text,
    )
    summary = _render_template(
        template.summary_template
        or f"Complete {guide_type_display} guide for {{brand}} {{model}}",
        brand_name,
        model_name,
        chipset,
        guide_type_display,
    )

    # Templates are authored in Markdown; the post body is sanitised HTML
    body = markdown.markdown(body, extensions=["sane_lists"])

    # Create slug
    slug_base = slugify(f"{brand_name}-{model_name}-{template.guide_type}".strip("-"))
    slug = slug_base[:200]
    counter = 1
    while Post.objects.filter(slug=slug).exists():
        slug = f"{slug_base[:190]}-{counter}"
        counter += 1

    # Create blog post
    post = Post.objects.create(
        title=title[:200],
        slug=slug,
        summary=summary[:500],
        body=body,
        author=author,
        status=PostStatus.DRAFT,
        firmware_brand=brand,
        firmware_model=model_obj,
        content_source=PostSource.AUTOMATED,
        publish_at=None,
        published_at=None,
        is_published=False,
    )

    # Create tracking record
    guide = GeneratedFlashingGuide.objects.create(
        template=template,
        post=post,
        brand=brand,
        model=model_obj,
        chipset=chipset,
    )

    # Update template counter
    FlashingGuideTemplate.objects.filter(pk=template.pk).update(
        generated_count=template.generated_count + 1
    )

    if template.submit_for_review:
        from apps.blog.services.workflow import request_review

        request_review(post, user=author)

    from apps.blog.services.history import PostHistoryService

    PostHistoryService.record_current_version(
        post, user=author, change_summary="Generated flashing guide"
    )

    logger.info(
        "flashing_guide.generated",
        extra={
            "template": template.pk,
            "brand": brand_name,
            "model": model_name,
            "post": post.pk,
        },
    )
    return guide


def generate_guides_for_template(
    template: FlashingGuideTemplate,
    *,
    limit: int = 50,
) -> list[GeneratedFlashingGuide]:
    """
    Generate blog posts for all matching brand/model combinations.
    Uses template.brand filter and chipset_filter to find targets.
    """
    results: list[GeneratedFlashingGuide] = []

    # Get target brands
    if template.brand_id:
        brands: QuerySet[Brand] = Brand.objects.filter(pk=template.brand_id)
    else:
        brands = Brand.objects.filter(is_featured=True)

    for brand in brands:
        # Get models for this brand
        models_qs: QuerySet[Model] = Model.objects.filter(brand=brand, is_active=True)
        if template.chipset_filter:
            models_qs = models_qs.filter(chipset__icontains=template.chipset_filter)

        if models_qs.exists():
            for device_model in models_qs[:limit]:
                guide = generate_guide_for_brand_model(template, brand, device_model)
                if guide:
                    results.append(guide)
                    if len(results) >= limit:
                        return results
        else:
            # Generate brand-level guide only
            guide = generate_guide_for_brand_model(template, brand, None)
            if guide:
                results.append(guide)
                if len(results) >= limit:
                    return results

    return results


def get_tool_stats() -> dict[str, Any]:
    """Return aggregate statistics for the flashing tools catalog."""
    from .models import FlashingToolCategory

    return {
        "total_tools": FlashingTool.objects.filter(is_active=True).count(),
        "total_categories": FlashingToolCategory.objects.filter(is_active=True).count(),
        "oem_tools": FlashingTool.objects.filter(
            is_active=True, tool_type="oem"
        ).count(),
        "free_tools": FlashingTool.objects.filter(
            is_active=True, tool_type="free"
        ).count(),
        "open_source_tools": FlashingTool.objects.filter(
            is_active=True, tool_type="open_source"
        ).count(),
        "featured_tools": FlashingTool.objects.filter(
            is_active=True, is_featured=True
        ).count(),
        "total_templates": FlashingGuideTemplate.objects.filter(is_active=True).count(),
        "auto_templates": FlashingGuideTemplate.objects.filter(
            is_active=True, auto_generate=True
        ).count(),
        "total_generated": GeneratedFlashingGuide.objects.count(),
    }
